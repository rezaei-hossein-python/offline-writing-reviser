from __future__ import annotations

import json
import logging
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from offline_writing_reviser.core.errors import (
    OfflineWritingLanguageToolUnavailable,
)
from offline_writing_reviser.paths import private_runtime_path
from offline_writing_reviser.windows.owned_processes import OwnedProcessJob


LANGUAGE = "en-US"
LANGUAGETOOL_VERSION = "6.6"
SERVER_MAIN_CLASS = "org.languagetool.server.HTTPServer"


def default_java_path() -> Path:
    return private_runtime_path(Path("java") / "bin" / "javaw.exe")


def default_java_paths() -> tuple[Path, Path]:
    java_bin = private_runtime_path(Path("java") / "bin")
    return java_bin / "javaw.exe", java_bin / "java.exe"


def default_server_jar_path() -> Path:
    return private_runtime_path(
        Path("languagetool") / "languagetool-server.jar"
    )


def hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if not hasattr(subprocess, "STARTUPINFO"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


@dataclass(frozen=True)
class LanguageToolClient:
    base_url: str
    timeout: float = 30.0

    def check(self, text: str) -> tuple[dict[str, Any], float]:
        body = urllib.parse.urlencode(
            {"language": LANGUAGE, "text": text}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/v2/check",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (
            OSError,
            TimeoutError,
            UnicodeError,
            json.JSONDecodeError,
            urllib.error.URLError,
        ) as exc:
            raise OfflineWritingLanguageToolUnavailable(
                "The private LanguageTool service is unavailable"
            ) from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("matches"), list
        ):
            raise OfflineWritingLanguageToolUnavailable(
                "LanguageTool returned an invalid response"
            )
        return payload, time.perf_counter() - started


class LanguageToolRuntime:
    """Own one loopback-only LanguageTool child process."""

    def __init__(
        self,
        java_path: Path | None = None,
        server_jar_path: Path | None = None,
        *,
        startup_timeout: float = 60.0,
        request_timeout: float = 30.0,
        logger: logging.Logger | None = None,
    ):
        self.java_path = (java_path or default_java_path()).resolve()
        self.server_jar_path = (
            server_jar_path or default_server_jar_path()
        ).resolve()
        self.startup_timeout = startup_timeout
        self.request_timeout = request_timeout
        self.logger = logger or logging.getLogger("offline-writing-reviser")
        self._process: subprocess.Popen[bytes] | None = None
        self._client: LanguageToolClient | None = None
        self._lock = threading.RLock()
        self._port: int | None = None
        self._shutdown = threading.Event()
        self._process_job: OwnedProcessJob | None = None

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self._process

    @property
    def is_running(self) -> bool:
        return bool(self._process and self._process.poll() is None)

    @property
    def base_url(self) -> str | None:
        return (
            f"http://127.0.0.1:{self._port}"
            if self._port is not None
            else None
        )

    def dependency_status(self) -> dict[str, Any]:
        return {
            "java_path": str(self.java_path),
            "java_found": self.java_path.is_file(),
            "server_jar_path": str(self.server_jar_path),
            "languagetool_found": self.server_jar_path.is_file(),
            "version": LANGUAGETOOL_VERSION,
            "running": self.is_running,
            "base_url": self.base_url,
        }

    def client(self) -> LanguageToolClient:
        with self._lock:
            if self._shutdown.is_set():
                raise OfflineWritingLanguageToolUnavailable(
                    "LanguageTool runtime is shutting down"
                )
            self._ensure_running()
            assert self._client is not None
            return self._client

    def check(self, text: str) -> tuple[dict[str, Any], float]:
        for attempt in range(2):
            try:
                return self.client().check(text)
            except OfflineWritingLanguageToolUnavailable:
                if attempt:
                    raise
                if self._shutdown.is_set():
                    raise
                self.logger.warning(
                    "LanguageTool request failed; restarting owned runtime"
                )
                with self._lock:
                    self._stop_locked()
        raise OfflineWritingLanguageToolUnavailable(
            "LanguageTool restart failed"
        )

    def health_check(self) -> tuple[bool, float | None, str | None]:
        started = time.perf_counter()
        try:
            payload, _ = self.check("This sentence is correct.")
            healthy = isinstance(payload.get("matches"), list)
            return healthy, time.perf_counter() - started, None
        except OfflineWritingLanguageToolUnavailable as exc:
            return False, None, str(exc)

    def stop(self) -> None:
        self._shutdown.set()
        with self._lock:
            self._stop_locked()

    def _validate_paths(self) -> None:
        if not self.java_path.is_file():
            raise OfflineWritingLanguageToolUnavailable(
                f"Bundled Java executable not found: {self.java_path}"
            )
        if not self.server_jar_path.is_file():
            raise OfflineWritingLanguageToolUnavailable(
                f"Bundled LanguageTool server not found: {self.server_jar_path}"
            )

    def _ensure_running(self) -> None:
        if self._shutdown.is_set():
            raise OfflineWritingLanguageToolUnavailable(
                "LanguageTool runtime is shutting down"
            )
        if self.is_running and self._client is not None:
            return
        self._validate_paths()
        self._stop_locked()
        port = _find_loopback_port()
        command = [
            str(self.java_path),
            "-cp",
            str(self.server_jar_path),
            SERVER_MAIN_CLASS,
            "--port",
            str(port),
        ]
        self.logger.info(
            "LanguageTool startup version=%s port=%s java_private=true "
            "loopback_only=true",
            LANGUAGETOOL_VERSION,
            port,
        )
        try:
            self._process = subprocess.Popen(
                command,
                cwd=str(self.server_jar_path.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=hidden_startupinfo(),
                creationflags=getattr(
                    subprocess, "CREATE_NO_WINDOW", 0x08000000
                ),
            )
            self._process_job = OwnedProcessJob()
            self._process_job.assign(self._process._handle)
        except OSError as exc:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
                self._process.wait(timeout=5)
            if self._process_job is not None:
                self._process_job.close()
            self._process = None
            self._process_job = None
            raise OfflineWritingLanguageToolUnavailable(
                "Bundled LanguageTool could not be started"
            ) from exc
        self._port = port
        self._client = LanguageToolClient(
            f"http://127.0.0.1:{port}", self.request_timeout
        )
        deadline = time.monotonic() + self.startup_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                code = self._process.returncode
                self._stop_locked()
                raise OfflineWritingLanguageToolUnavailable(
                    "LanguageTool exited before readiness "
                    f"(exit code {code})"
                )
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v2/languages", method="GET"
                )
                with urllib.request.urlopen(request, timeout=1.0) as response:
                    if response.status == 200:
                        self.logger.info(
                            "LanguageTool ready version=%s port=%s",
                            LANGUAGETOOL_VERSION,
                            port,
                        )
                        return
            except (OSError, urllib.error.URLError) as exc:
                last_error = exc
                time.sleep(0.1)
        self._stop_locked()
        raise OfflineWritingLanguageToolUnavailable(
            "LanguageTool startup timed out"
        ) from last_error

    def _stop_locked(self) -> None:
        process = self._process
        process_job = self._process_job
        self._process = None
        self._process_job = None
        self._client = None
        self._port = None
        if process is None or process.poll() is not None:
            if process_job is not None:
                process_job.close()
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process_job is not None:
            process_job.close()
        self.logger.info("LanguageTool owned runtime stopped")


def _find_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
