from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from dataclasses import dataclass

from offline_writing_reviser.providers.base import (
    OfflineWritingModelMissing,
    OfflineWritingProvider,
    OfflineWritingProviderError,
    OfflineWritingProviderCancelled,
    OfflineWritingProviderTimeout,
    OfflineWritingProviderUnavailable,
)

OLLAMA_API_URL = "http://127.0.0.1:11434"
PROOFREADING_KEEP_ALIVE = "10m"
PROOFREADING_GENERATION_OPTIONS = {
    "temperature": 0.2,
    "top_p": 0.9,
    "repeat_penalty": 1.05,
    "seed": 25,
    "num_ctx": 4096,
    "num_predict": 384,
}


@dataclass(frozen=True)
class OllamaInferenceResult:
    text: str
    telemetry: dict[str, Any]


class OllamaCliOfflineWritingProvider(OfflineWritingProvider):
    def __init__(self, model: str, executable: str = "ollama"):
        self._model = model
        self._executable = executable
        self._model_cache: list[str] | None = None
        self._model_cache_time = 0.0
        self._cancel_event = threading.Event()
        self._response_lock = threading.Lock()
        self._active_response = None

    def cancel_current(self) -> None:
        self._cancel_event.set()
        with self._response_lock:
            response = self._active_response
        if response is not None:
            try:
                response.close()
            except OSError:
                pass

    @property
    def provider_name(self) -> str:
        return "ollama_cli"

    @property
    def model_identifier(self) -> str:
        return self._model

    @property
    def executable(self) -> str:
        return self._executable

    def resolved_executable(self) -> str:
        return self._resolve_executable()

    def cli_version(self, timeout_seconds: float = 5.0) -> str | None:
        executable = self._resolve_executable()
        result = self._run(
            [executable, "--version"], timeout_seconds=timeout_seconds
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or result.stderr.strip() or None

    def ensure_api_running(self, timeout_seconds: float = 20.0) -> None:
        try:
            self.api_version(timeout_seconds=2.0)
            return
        except OfflineWritingProviderError:
            pass
        executable = self._resolve_executable()
        try:
            subprocess.Popen(
                [executable, "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=_hidden_startupinfo(),
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                ),
                close_fds=True,
            )
        except OSError as exc:
            raise OfflineWritingProviderUnavailable(
                "Ollama could not be started"
            ) from exc
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                self.api_version(timeout_seconds=2.0)
                return
            except OfflineWritingProviderError:
                time.sleep(0.25)
        raise OfflineWritingProviderUnavailable(
            "Ollama did not become ready before the startup timeout"
        )

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
        if (
            self._model_cache is not None
            and time.monotonic() - self._model_cache_time < 30.0
        ):
            installed = self._model_cache
        else:
            installed = self.list_installed_models(timeout_seconds)
            self._model_cache = installed
            self._model_cache_time = time.monotonic()
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
        self._cancel_event.clear()
        self.ensure_model_available(timeout_seconds=5.0)
        self.ensure_api_running(timeout_seconds=min(20.0, timeout_seconds))
        return self._perform_revision(
            text, instruction, timeout_seconds
        ).text

    def revise_with_telemetry(
        self, text: str, instruction: str, timeout_seconds: float
    ) -> OllamaInferenceResult:
        self._cancel_event.clear()
        installed = self.api_models(timeout_seconds=5.0)
        if self._model not in installed:
            raise OfflineWritingModelMissing(
                f"Configured Ollama model is missing model={self._model}"
            )
        return self._perform_revision(text, instruction, timeout_seconds)

    def _perform_revision(
        self, text: str, instruction: str, timeout_seconds: float
    ) -> OllamaInferenceResult:
        payload = {
            "model": self._model,
            "stream": True,
            "think": False,
            "keep_alive": PROOFREADING_KEEP_ALIVE,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": text},
            ],
            "options": PROOFREADING_GENERATION_OPTIONS,
        }
        payload_bytes = len(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        started = time.perf_counter()
        response = self._request_chat(payload, timeout_seconds)
        wall_seconds = time.perf_counter() - started
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise OfflineWritingProviderError("Local revision response was invalid")
        telemetry = {
            "wall_seconds": wall_seconds,
            "first_token_seconds": response.get("_first_token_wall_seconds"),
            "total_duration_seconds": _nanoseconds(response, "total_duration"),
            "load_duration_seconds": _nanoseconds(response, "load_duration"),
            "prompt_eval_duration_seconds": _nanoseconds(
                response, "prompt_eval_duration"
            ),
            "generation_duration_seconds": _nanoseconds(
                response, "eval_duration"
            ),
            "prompt_token_count": _integer(response, "prompt_eval_count"),
            "generation_token_count": _integer(response, "eval_count"),
            "request_payload_bytes": payload_bytes,
            "response_bytes": len(
                json.dumps(response, ensure_ascii=False).encode("utf-8")
            ),
        }
        return OllamaInferenceResult(content, telemetry)

    def api_version(self, timeout_seconds: float = 5.0) -> str | None:
        response = self._request_json("/api/version", None, timeout_seconds)
        value = response.get("version")
        return value if isinstance(value, str) else None

    def api_models(self, timeout_seconds: float = 5.0) -> list[str]:
        response = self._request_json("/api/tags", None, timeout_seconds)
        return sorted(
            {
                str(item.get("name"))
                for item in response.get("models", [])
                if isinstance(item, dict) and item.get("name")
            },
            key=str.casefold,
        )

    def api_model_size(
        self, model: str | None = None, timeout_seconds: float = 5.0
    ) -> int | None:
        model = model or self._model
        response = self._request_json("/api/tags", None, timeout_seconds)
        for item in response.get("models", []):
            if isinstance(item, dict) and item.get("name") == model:
                size = item.get("size")
                return size if isinstance(size, int) and size >= 0 else None
        return None

    def remove_model(self, model: str, timeout_seconds: float = 120.0) -> None:
        if not model or model != model.strip():
            raise OfflineWritingProviderError("Refusing invalid model removal")
        self._request_json(
            "/api/delete",
            {"model": model},
            timeout_seconds,
            method="DELETE",
            allow_empty=True,
        )

    def verify_minimal_inference(self, timeout_seconds: float = 120.0) -> None:
        """Load the configured model and prove that it can produce a response."""
        response = self._request_json(
            "/api/generate",
            {
                "model": self._model,
                "prompt": "Reply with OK.",
                "stream": False,
                "keep_alive": PROOFREADING_KEEP_ALIVE,
                "options": {
                    "temperature": 0,
                    "num_predict": 1,
                },
            },
            timeout_seconds,
        )
        if response.get("done") is not True:
            raise OfflineWritingProviderError(
                "Ollama did not complete the model readiness check"
            )

    def runtime_diagnostics(
        self, timeout_seconds: float = 5.0
    ) -> dict[str, Any]:
        response = self._request_json("/api/ps", None, timeout_seconds)
        running_models = (
            response.get("models")
            if isinstance(response.get("models"), list)
            else []
        )
        selected = next(
            (
                item
                for item in running_models
                if isinstance(item, dict)
                and item.get("name") in {self._model, f"{self._model}:latest"}
            ),
            None,
        )
        size = _integer(selected or {}, "size")
        size_vram = _integer(selected or {}, "size_vram")
        acceleration = "unknown"
        if selected is not None and size_vram is not None:
            if size_vram == 0:
                acceleration = "cpu"
            elif size and size_vram >= size * 0.95:
                acceleration = "gpu"
            else:
                acceleration = "partial_gpu"
        return {
            "model_loaded": selected is not None,
            "acceleration": acceleration,
            "model_size_bytes": size,
            "model_vram_bytes": size_vram,
            "context_length": _integer(selected or {}, "context_length"),
            "expires_at": (
                selected.get("expires_at")
                if isinstance(selected, dict)
                else None
            ),
            "device": None,
            "backend": None,
            "backend_limitation": (
                "The local Ollama API reports model VRAM offload but does not "
                "identify the GPU vendor or compute backend."
            ),
        }

    def _request_chat(
        self, payload: dict[str, Any], timeout_seconds: float
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{OLLAMA_API_URL}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        stream_started = time.perf_counter()
        deadline = time.monotonic() + timeout_seconds
        pieces: list[str] = []
        final: dict[str, Any] = {}
        first_token_seconds: float | None = None
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                with self._response_lock:
                    self._active_response = response
                while True:
                    if self._cancel_event.is_set():
                        raise OfflineWritingProviderCancelled(
                            "Local revision was cancelled"
                        )
                    if time.monotonic() >= deadline:
                        raise OfflineWritingProviderTimeout(
                            "Local revision timed out"
                        )
                    _set_response_timeout(response, deadline - time.monotonic())
                    line = response.readline()
                    if not line:
                        if self._cancel_event.is_set():
                            raise OfflineWritingProviderCancelled(
                                "Local revision was cancelled"
                            )
                        break
                    item = json.loads(line.decode("utf-8"))
                    if not isinstance(item, dict):
                        raise OfflineWritingProviderError(
                            "Local revision response was invalid"
                        )
                    message = item.get("message")
                    content = (
                        message.get("content")
                        if isinstance(message, dict)
                        else None
                    )
                    if isinstance(content, str):
                        if content and first_token_seconds is None:
                            first_token_seconds = time.perf_counter() - stream_started
                        pieces.append(content)
                    final = item
                    if item.get("done") is True:
                        break
            parsed = dict(final)
            parsed["message"] = {"content": "".join(pieces)}
            parsed["_first_token_wall_seconds"] = first_token_seconds
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise OfflineWritingModelMissing(
                    f"Configured Ollama model is missing model={self._model}"
                ) from exc
            raise OfflineWritingProviderError("Local revision request failed") from exc
        except OfflineWritingProviderCancelled:
            raise
        except OfflineWritingProviderTimeout:
            raise
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            raise OfflineWritingProviderTimeout("Local revision timed out") from exc
        except (OSError, urllib.error.URLError) as exc:
            if self._cancel_event.is_set():
                raise OfflineWritingProviderCancelled(
                    "Local revision was cancelled"
                ) from exc
            raise OfflineWritingProviderUnavailable(
                "Ollama local API is unavailable"
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OfflineWritingProviderError(
                "Local revision response was invalid"
            ) from exc
        finally:
            with self._response_lock:
                self._active_response = None
        if not isinstance(parsed, dict):
            raise OfflineWritingProviderError("Local revision response was invalid")
        return parsed
    def _request_json(
        self,
        path: str,
        payload: dict[str, Any] | None,
        timeout_seconds: float,
        *,
        method: str | None = None,
        allow_empty: bool = False,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{OLLAMA_API_URL}{path}",
            data=(
                None
                if payload is None
                else json.dumps(payload).encode("utf-8")
            ),
            headers={"Content-Type": "application/json"},
            method=method or ("GET" if payload is None else "POST"),
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_seconds
            ) as response:
                raw = response.read()
                parsed = {} if allow_empty and not raw else json.loads(raw.decode("utf-8"))
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            raise OfflineWritingProviderTimeout(
                "Ollama local API timed out"
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise OfflineWritingProviderUnavailable(
                "Ollama local API is unavailable"
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OfflineWritingProviderError(
                "Ollama local API returned invalid JSON"
            ) from exc
        if not isinstance(parsed, dict):
            raise OfflineWritingProviderError(
                "Ollama local API returned an invalid response"
            )
        return parsed

    def _resolve_executable(self) -> str:
        configured = Path(self._executable)
        if configured.parent != Path(".") and configured.exists():
            return str(configured)
        resolved = shutil.which(self._executable)
        if resolved:
            return resolved
        for candidate in _default_windows_ollama_paths():
            try:
                if candidate.exists():
                    return str(candidate)
            except OSError:
                continue
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
                creationflags=getattr(
                    subprocess, "CREATE_NO_WINDOW", 0x08000000
                ),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OfflineWritingProviderTimeout("Local revision timed out") from exc
        except OSError as exc:
            raise OfflineWritingProviderUnavailable("Ollama executable is unavailable") from exc


def _set_response_timeout(response: Any, remaining_seconds: float) -> None:
    """Bound the next blocking read by the request's absolute deadline."""
    try:
        response.fp.raw._sock.settimeout(max(0.1, remaining_seconds))
    except (AttributeError, OSError):
        return


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


def _integer(payload: dict[str, Any], name: str) -> int | None:
    value = payload.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _nanoseconds(payload: dict[str, Any], name: str) -> float | None:
    value = _integer(payload, name)
    return value / 1_000_000_000 if value is not None else None
