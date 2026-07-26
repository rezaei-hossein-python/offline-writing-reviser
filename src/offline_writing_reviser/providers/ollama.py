from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from offline_writing_reviser.providers.base import (
    OfflineWritingModelMissing,
    OfflineWritingProvider,
    OfflineWritingProviderError,
    OfflineWritingProviderTimeout,
    OfflineWritingProviderUnavailable,
)

OLLAMA_API_URL = "http://127.0.0.1:11434"
PROOFREADING_KEEP_ALIVE = "10m"
PROOFREADING_GENERATION_OPTIONS = {
    "temperature": 0,
    "seed": 0,
    "num_ctx": 8192,
    "num_predict": 4096,
}


class OllamaCliOfflineWritingProvider(OfflineWritingProvider):
    def __init__(self, model: str, executable: str = "ollama"):
        self._model = model
        self._executable = executable

    @property
    def provider_name(self) -> str:
        return "ollama_cli"

    @property
    def model_identifier(self) -> str:
        return self._model

    @property
    def executable(self) -> str:
        return self._executable

    def is_available(self) -> bool:
        try:
            self.ensure_model_available(timeout_seconds=5.0)
        except (
            OfflineWritingModelMissing,
            OfflineWritingProviderError,
            OfflineWritingProviderTimeout,
            OfflineWritingProviderUnavailable,
        ):
            return False
        return True

    def ensure_model_available(self, timeout_seconds: float = 5.0) -> None:
        installed = self.list_installed_models(timeout_seconds)
        if self._model not in installed:
            raise OfflineWritingModelMissing(
                f"Configured Ollama model is missing model={self._model}"
            )

    def list_installed_models(self, timeout_seconds: float = 5.0) -> list[str]:
        executable = self._resolve_executable()
        result = self._run([executable, "list"], timeout_seconds=timeout_seconds)
        if result.returncode != 0:
            raise OfflineWritingProviderUnavailable("Ollama model list failed")
        return sorted(_parse_ollama_list(result.stdout), key=str.casefold)

    def revise(self, text: str, instruction: str, timeout_seconds: float) -> str:
        self.ensure_model_available(timeout_seconds=5.0)
        payload = {
            "model": self._model,
            "stream": False,
            "think": False,
            "keep_alive": PROOFREADING_KEEP_ALIVE,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": text},
            ],
            "options": PROOFREADING_GENERATION_OPTIONS,
        }
        response = self._request_chat(payload, timeout_seconds)
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise OfflineWritingProviderError("Local revision response was invalid")
        return content

    def _request_chat(
        self, payload: dict[str, Any], timeout_seconds: float
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{OLLAMA_API_URL}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise OfflineWritingModelMissing(
                    f"Configured Ollama model is missing model={self._model}"
                ) from exc
            raise OfflineWritingProviderError("Local revision request failed") from exc
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            raise OfflineWritingProviderTimeout("Local revision timed out") from exc
        except (OSError, urllib.error.URLError) as exc:
            raise OfflineWritingProviderUnavailable(
                "Ollama local API is unavailable"
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OfflineWritingProviderError(
                "Local revision response was invalid"
            ) from exc
        if not isinstance(parsed, dict):
            raise OfflineWritingProviderError("Local revision response was invalid")
        return parsed

    def _resolve_executable(self) -> str:
        configured = Path(self._executable)
        if configured.parent != Path(".") and configured.exists():
            return str(configured)
        resolved = shutil.which(self._executable)
        if resolved:
            return resolved
        for candidate in _default_windows_ollama_paths():
            if candidate.exists():
                return str(candidate)
        raise OfflineWritingProviderUnavailable("Ollama executable is unavailable")

    def _run(
        self,
        args: list[str],
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                startupinfo=_hidden_startupinfo(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OfflineWritingProviderTimeout("Local revision timed out") from exc
        except OSError as exc:
            raise OfflineWritingProviderUnavailable("Ollama executable is unavailable") from exc


def _parse_ollama_list(output: str) -> set[str]:
    names: set[str] = set()
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("name "):
            continue
        names.add(stripped.split()[0])
    return names


def _default_windows_ollama_paths() -> list[Path]:
    local_app_data = Path.home() / "AppData" / "Local"
    return [
        local_app_data / "Programs" / "Ollama" / "ollama.exe",
        Path("C:/Program Files/Ollama/ollama.exe"),
    ]


def _hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if not hasattr(subprocess, "STARTUPINFO"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo
