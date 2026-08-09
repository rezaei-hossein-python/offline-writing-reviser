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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from offline_writing_reviser.paths import private_runtime_path
from offline_writing_reviser.proofreading.semantic import protected_values


LANGUAGE = "en-US"
LANGUAGETOOL_VERSION = "6.6"
JAVA_VERSION = "17.0.20+8"
SERVER_MAIN_CLASS = "org.languagetool.server.HTTPServer"
READINESS_PROBE_TEXT = "This sentence is correct."
MECHANICAL_ISSUE_TYPES = frozenset(
    {"misspelling", "grammar", "typographical", "duplication"}
)
MECHANICAL_CATEGORIES = frozenset(
    {"TYPOS", "GRAMMAR", "PUNCTUATION", "CASING"}
)
DEMONSTRATED_UNSAFE_RULE_IDS = frozenset({"BEEN_PART_AGREEMENT"})
PROTECTED_CATEGORIES = (
    "urls",
    "emails",
    "phones",
    "numbers",
    "dates",
    "times",
    "identifiers",
    "quotes",
    "names",
    "negation",
    "modality",
)


class LanguageToolRuntimeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        duration_ms: float | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.duration_ms = duration_ms


@dataclass(frozen=True)
class LanguageToolFailure:
    code: str
    message: str
    recoverable: bool


@dataclass(frozen=True)
class LanguageToolEdit:
    offset: int
    length: int
    original: str
    replacement: str
    rule_id: str
    category: str
    issue_type: str
    message: str
    reason: str | None = None


@dataclass(frozen=True)
class LanguageToolCorrectionResult:
    original_text: str
    corrected_text: str
    applied_edits: tuple[LanguageToolEdit, ...]
    skipped_edits: tuple[LanguageToolEdit, ...]
    rule_ids: tuple[str, ...]
    categories: tuple[str, ...]
    duration_ms: float
    runtime_status: str
    error_status: str | None
    failure: LanguageToolFailure | None

    @property
    def changed(self) -> bool:
        return self.corrected_text != self.original_text


def default_javaw_path() -> Path:
    return private_runtime_path(Path("java") / "bin" / "javaw.exe")


def default_server_jar_path() -> Path:
    return private_runtime_path(
        Path("languagetool") / "languagetool-server.jar"
    )


def _hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if not hasattr(subprocess, "STARTUPINFO"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


@dataclass(frozen=True)
class LanguageToolClient:
    base_url: str
    timeout_seconds: float = 5.0

    def check(
        self,
        text: str,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[dict[str, Any], float]:
        body = urllib.parse.urlencode(
            {"language": LANGUAGE, "text": text, "level": "default"}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/v2/check",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        started = time.perf_counter()
        timeout = (
            self.timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError) as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            raise LanguageToolRuntimeError(
                "The private LanguageTool request timed out",
                code="request_timeout",
                duration_ms=duration_ms,
            ) from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            raise LanguageToolRuntimeError(
                "The private LanguageTool response was unavailable",
                code="malformed_response",
                duration_ms=duration_ms,
            ) from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("matches"), list
        ):
            raise LanguageToolRuntimeError(
                "LanguageTool returned an invalid response",
                code="malformed_response",
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        return payload, (time.perf_counter() - started) * 1000


class LanguageToolRuntime:
    """Own exactly one private loopback LanguageTool child process."""

    def __init__(
        self,
        javaw_path: Path | None = None,
        server_jar_path: Path | None = None,
        *,
        startup_timeout_seconds: float = 30.0,
        request_timeout_seconds: float = 5.0,
        logger: logging.Logger | None = None,
    ):
        self.javaw_path = (javaw_path or default_javaw_path()).resolve()
        self.server_jar_path = (
            server_jar_path or default_server_jar_path()
        ).resolve()
        self.startup_timeout_seconds = startup_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.logger = logger or logging.getLogger("offline-writing-reviser")
        self._process: subprocess.Popen[bytes] | None = None
        self._client: LanguageToolClient | None = None
        self._port: int | None = None
        self._ready = False
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._warm_thread: threading.Thread | None = None
        self._last_error: str | None = None
        self._startup_duration_ms: float | None = None
        self._warmup_duration_ms: float | None = None

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self._process

    @property
    def is_running(self) -> bool:
        return bool(self._process and self._process.poll() is None)

    @property
    def is_ready(self) -> bool:
        return bool(self._ready and self.is_running and self._client is not None)

    @property
    def base_url(self) -> str | None:
        return (
            f"http://127.0.0.1:{self._port}"
            if self._port is not None and self.is_ready
            else None
        )

    @property
    def startup_duration_ms(self) -> float | None:
        return self._startup_duration_ms

    def status(self) -> dict[str, Any]:
        if self.is_ready:
            state = "ready"
        elif self.is_running:
            state = "starting"
        else:
            state = "stopped"
        return {
            "state": state,
            "version": LANGUAGETOOL_VERSION,
            "java_version": JAVA_VERSION,
            "language": LANGUAGE,
            "javaw_path": str(self.javaw_path),
            "server_jar_path": str(self.server_jar_path),
            "base_url": self.base_url,
            "pid": self._process.pid if self.is_running and self._process else None,
            "startup_duration_ms": self._startup_duration_ms,
            "warmup_duration_ms": self._warmup_duration_ms,
            "last_error": self._last_error,
        }

    def start_in_background(self) -> None:
        with self._lock:
            if self._shutdown.is_set() or self.is_ready:
                return
            if self._warm_thread and self._warm_thread.is_alive():
                return
            self._warm_thread = threading.Thread(
                target=self._background_start,
                name="languagetool-private-startup",
                daemon=True,
            )
            self._warm_thread.start()

    def _background_start(self) -> None:
        try:
            self.warmup()
        except LanguageToolRuntimeError as exc:
            self.logger.warning(
                "LanguageTool background startup failed category=%s",
                exc.code,
            )

    def warmup(self) -> float:
        """Start once and prove the production correction API is usable."""
        started = time.perf_counter()
        self.check(READINESS_PROBE_TEXT)
        duration_ms = (time.perf_counter() - started) * 1000
        self._warmup_duration_ms = duration_ms
        self.logger.info(
            "LanguageTool private English warmup duration_ms=%.2f",
            duration_ms,
        )
        return duration_ms

    def start(self) -> float:
        with self._lock:
            if self._shutdown.is_set():
                raise LanguageToolRuntimeError(
                    "LanguageTool runtime is shutting down",
                    code="runtime_stopped",
                )
            if self.is_ready:
                return self._startup_duration_ms or 0.0
            self._validate_paths()
            self._stop_locked(reason="startup_replacing_stale_state")
            port = _find_loopback_port()
            command = [
                str(self.javaw_path),
                "-Xms64m",
                "-Xmx512m",
                "-cp",
                str(self.server_jar_path),
                SERVER_MAIN_CLASS,
                "--port",
                str(port),
            ]
            started = time.perf_counter()
            self.logger.info(
                "LanguageTool private startup initiated version=%s "
                "java_version=%s port=%s java_private=true loopback_only=true",
                LANGUAGETOOL_VERSION,
                JAVA_VERSION,
                port,
            )
            try:
                spawn_started = time.perf_counter()
                process = subprocess.Popen(
                    command,
                    cwd=str(self.server_jar_path.parent),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    startupinfo=_hidden_startupinfo(),
                    creationflags=getattr(
                        subprocess, "CREATE_NO_WINDOW", 0x08000000
                    ),
                    close_fds=True,
                )
            except OSError as exc:
                self._last_error = "startup_failed"
                raise LanguageToolRuntimeError(
                    "The bundled LanguageTool runtime could not start",
                    code="startup_failed",
                ) from exc
            self._process = process
            self._port = port
            self._client = None
            self._ready = False
            client = LanguageToolClient(
                f"http://127.0.0.1:{port}", self.request_timeout_seconds
            )
            self.logger.info(
                "LanguageTool private process spawned pid=%s port=%s "
                "spawn_ms=%.2f alive=%s stdout=devnull stderr=devnull",
                process.pid,
                port,
                (time.perf_counter() - spawn_started) * 1000,
                process.poll() is None,
            )
            deadline = time.monotonic() + self.startup_timeout_seconds
            last_error: Exception | None = None
            listener_attempts = 0
            while time.monotonic() < deadline:
                if self._shutdown.is_set():
                    self._stop_locked(reason="shutdown_during_startup")
                    raise LanguageToolRuntimeError(
                        "LanguageTool startup cancelled during shutdown",
                        code="runtime_stopped",
                    )
                if process.poll() is not None:
                    code = process.returncode
                    self._last_error = f"early_exit_{code}"
                    self._stop_locked(reason="process_exit_during_startup")
                    raise LanguageToolRuntimeError(
                        f"LanguageTool exited before readiness (code {code})",
                        code="early_exit",
                    )
                try:
                    listener_attempts += 1
                    with socket.create_connection(
                        ("127.0.0.1", port), timeout=0.1
                    ):
                        break
                except OSError as exc:
                    last_error = exc
                    time.sleep(0.05)
            else:
                self._last_error = "startup_timeout"
                self.logger.warning(
                    "LanguageTool readiness listener timeout pid=%s port=%s "
                    "attempts=%s alive=%s",
                    process.pid,
                    port,
                    listener_attempts,
                    process.poll() is None,
                )
                self._stop_locked(reason="listener_startup_timeout")
                raise LanguageToolRuntimeError(
                    "LanguageTool startup timed out", code="startup_timeout"
                ) from last_error

            listener_ms = (time.perf_counter() - started) * 1000
            self.logger.info(
                "LanguageTool readiness listener available pid=%s port=%s "
                "attempts=%s duration_ms=%.2f alive=%s",
                process.pid,
                port,
                listener_attempts,
                listener_ms,
                process.poll() is None,
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._last_error = "startup_timeout"
                self._stop_locked(reason="readiness_deadline_elapsed")
                raise LanguageToolRuntimeError(
                    "LanguageTool startup timed out", code="startup_timeout"
                )
            probe_started = time.perf_counter()
            self.logger.info(
                "LanguageTool readiness probe initiated pid=%s port=%s "
                "attempt=1 endpoint=/v2/check",
                process.pid,
                port,
            )
            try:
                client.check(
                    READINESS_PROBE_TEXT,
                    timeout_seconds=max(0.1, remaining),
                )
            except LanguageToolRuntimeError as exc:
                probe_ms = (time.perf_counter() - probe_started) * 1000
                self._last_error = "startup_timeout"
                self.logger.warning(
                    "LanguageTool readiness probe failed pid=%s port=%s "
                    "attempt=1 endpoint=/v2/check result=%s duration_ms=%.2f "
                    "alive=%s exit_code=%s",
                    process.pid,
                    port,
                    exc.code,
                    probe_ms,
                    process.poll() is None,
                    process.poll(),
                )
                self._stop_locked(reason=f"readiness_probe_{exc.code}")
                raise LanguageToolRuntimeError(
                    "LanguageTool correction API did not become ready",
                    code="startup_timeout",
                    duration_ms=probe_ms,
                ) from exc

            if process.poll() is not None:
                code = process.returncode
                self._last_error = f"early_exit_{code}"
                self._stop_locked(reason="process_exit_after_readiness_probe")
                raise LanguageToolRuntimeError(
                    f"LanguageTool exited after readiness probe (code {code})",
                    code="early_exit",
                )
            probe_ms = (time.perf_counter() - probe_started) * 1000
            duration_ms = (time.perf_counter() - started) * 1000
            self._client = client
            self._ready = True
            self._startup_duration_ms = duration_ms
            self._last_error = None
            self.logger.info(
                "LanguageTool readiness probe succeeded pid=%s port=%s "
                "attempt=1 endpoint=/v2/check result=valid duration_ms=%.2f",
                process.pid,
                port,
                probe_ms,
            )
            self.logger.info(
                "LanguageTool private ready version=%s pid=%s port=%s "
                "startup_ms=%.2f endpoint=/v2/check",
                LANGUAGETOOL_VERSION,
                process.pid,
                port,
                duration_ms,
            )
            return duration_ms

    def check(self, text: str) -> tuple[dict[str, Any], float]:
        with self._lock:
            last_error: LanguageToolRuntimeError | None = None
            for retry in range(2):
                try:
                    self.start()
                    assert self._client is not None
                    process = self._process
                    port = self._port
                    request_started = time.perf_counter()
                    self.logger.info(
                        "LanguageTool correction request initiated pid=%s "
                        "port=%s retry=%s chars=%s alive=%s",
                        process.pid if process else None,
                        port,
                        retry,
                        len(text),
                        bool(process and process.poll() is None),
                    )
                    payload, duration_ms = self._client.check(text)
                    if process is None or process.poll() is not None:
                        raise LanguageToolRuntimeError(
                            "LanguageTool exited during correction request",
                            code="process_exited",
                            duration_ms=(
                                time.perf_counter() - request_started
                            ) * 1000,
                        )
                    self.logger.info(
                        "LanguageTool correction request succeeded pid=%s "
                        "port=%s retry=%s duration_ms=%.2f alive=true",
                        process.pid,
                        port,
                        retry,
                        duration_ms,
                    )
                    return payload, duration_ms
                except LanguageToolRuntimeError as exc:
                    last_error = exc
                    process = self._process
                    exit_code = process.poll() if process else None
                    self.logger.warning(
                        "LanguageTool correction request failed pid=%s port=%s "
                        "retry=%s category=%s duration_ms=%.2f alive=%s "
                        "exit_code=%s",
                        process.pid if process else None,
                        self._port,
                        retry,
                        exc.code,
                        exc.duration_ms or 0.0,
                        bool(process and exit_code is None),
                        exit_code,
                    )
                    self._stop_locked(reason=f"request_{exc.code}")
                    if retry == 0 and not self._shutdown.is_set() and exc.code not in {
                        "java_missing",
                        "invalid_java_path",
                        "languagetool_missing",
                        "runtime_stopped",
                    }:
                        self.logger.warning(
                            "LanguageTool private restart reason=%s retry=1",
                            exc.code,
                        )
                        continue
                    raise
            assert last_error is not None
            raise last_error

    def stop(self) -> float:
        started = time.perf_counter()
        self._shutdown.set()
        with self._lock:
            self._stop_locked(reason="application_shutdown")
        duration_ms = (time.perf_counter() - started) * 1000
        self.logger.info(
            "LanguageTool private shutdown duration_ms=%.2f", duration_ms
        )
        return duration_ms

    def _validate_paths(self) -> None:
        if not self.javaw_path.is_file():
            raise LanguageToolRuntimeError(
                "Bundled javaw.exe is missing", code="java_missing"
            )
        if self.javaw_path.name.casefold() != "javaw.exe":
            raise LanguageToolRuntimeError(
                "Private runtime must use javaw.exe", code="invalid_java_path"
            )
        if not self.server_jar_path.is_file():
            raise LanguageToolRuntimeError(
                "Bundled LanguageTool server is missing",
                code="languagetool_missing",
            )

    def _stop_locked(self, *, reason: str) -> None:
        process = self._process
        port = self._port
        self._process = None
        self._client = None
        self._port = None
        self._ready = False
        if process is None:
            return
        exit_code = process.poll()
        if exit_code is not None:
            self.logger.info(
                "LanguageTool private state cleared pid=%s port=%s reason=%s "
                "alive=false exit_code=%s",
                process.pid,
                port,
                reason,
                exit_code,
            )
            return
        self.logger.info(
            "LanguageTool private termination initiated pid=%s port=%s "
            "reason=%s alive=true",
            process.pid,
            port,
            reason,
        )
        try:
            process.terminate()
        except OSError:
            self.logger.warning(
                "LanguageTool private terminate failed pid=%s port=%s reason=%s",
                process.pid,
                port,
                reason,
            )
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)
        self.logger.info(
            "LanguageTool private termination completed pid=%s port=%s "
            "reason=%s alive=%s exit_code=%s",
            process.pid,
            port,
            reason,
            process.poll() is None,
            process.poll(),
        )


class LanguageToolCorrectionService:
    """Apply one bounded, conservative LanguageTool correction pass."""

    def __init__(
        self,
        runtime: LanguageToolRuntime | None = None,
        logger: logging.Logger | None = None,
    ):
        self.runtime = runtime or shared_languagetool_runtime()
        self.logger = logger or logging.getLogger("offline-writing-reviser")

    def correct(self, text: str) -> LanguageToolCorrectionResult:
        started = time.perf_counter()
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if not text:
            return self._result(text, text, (), (), started, "not_required", None)
        try:
            payload, request_ms = self.runtime.check(text)
        except LanguageToolRuntimeError as exc:
            self.logger.warning(
                "LanguageTool correction failed category=%s chars=%s",
                exc.code,
                len(text),
            )
            return self._result(
                text,
                text,
                (),
                (),
                started,
                "unavailable",
                LanguageToolFailure(exc.code, str(exc), True),
            )

        candidates, skipped = _normalize_matches(text, payload["matches"])
        conflicting = _conflicting_indexes(candidates)
        applicable: list[LanguageToolEdit] = []
        for index, edit in enumerate(candidates):
            reason: str | None = None
            if index in conflicting:
                reason = "overlapping_or_conflicting_edit"
            elif edit.rule_id in DEMONSTRATED_UNSAFE_RULE_IDS:
                reason = "demonstrated_unsafe_rule"
            elif (
                edit.issue_type.casefold() not in MECHANICAL_ISSUE_TYPES
                and edit.category.upper() not in MECHANICAL_CATEGORIES
            ):
                reason = "non_mechanical_suggestion"
            elif _contains_unsafe_text(edit.replacement):
                reason = "unsafe_replacement_text"
            else:
                proposed = (
                    text[: edit.offset]
                    + edit.replacement
                    + text[edit.offset + edit.length :]
                )
                changed_categories = _changed_protected_categories(text, proposed)
                if changed_categories:
                    reason = "protected_tokens_changed:" + ",".join(
                        changed_categories
                    )
            if reason:
                skipped.append(replace(edit, reason=reason))
            else:
                applicable.append(edit)

        corrected = text
        for edit in sorted(applicable, key=lambda item: item.offset, reverse=True):
            corrected = (
                corrected[: edit.offset]
                + edit.replacement
                + corrected[edit.offset + edit.length :]
            )
        combined_changes = _changed_protected_categories(text, corrected)
        if combined_changes:
            reason = "combined_protected_tokens_changed:" + ",".join(
                combined_changes
            )
            skipped.extend(replace(edit, reason=reason) for edit in applicable)
            applicable = []
            corrected = text

        self.logger.info(
            "LanguageTool correction completed chars=%s applied=%s skipped=%s "
            "request_ms=%.2f changed=%s",
            len(text),
            len(applicable),
            len(skipped),
            request_ms,
            corrected != text,
        )
        return self._result(
            text,
            corrected,
            tuple(applicable),
            tuple(skipped),
            started,
            "ready",
            None,
        )

    @staticmethod
    def _result(
        original: str,
        corrected: str,
        applied: tuple[LanguageToolEdit, ...],
        skipped: tuple[LanguageToolEdit, ...],
        started: float,
        runtime_status: str,
        failure: LanguageToolFailure | None,
    ) -> LanguageToolCorrectionResult:
        edits = applied + skipped
        return LanguageToolCorrectionResult(
            original_text=original,
            corrected_text=corrected,
            applied_edits=applied,
            skipped_edits=skipped,
            rule_ids=tuple(sorted({edit.rule_id for edit in edits})),
            categories=tuple(sorted({edit.category for edit in edits})),
            duration_ms=(time.perf_counter() - started) * 1000,
            runtime_status=runtime_status,
            error_status=failure.code if failure else None,
            failure=failure,
        )


def _normalize_matches(
    text: str, matches: list[Any]
) -> tuple[list[LanguageToolEdit], list[LanguageToolEdit]]:
    candidates: list[LanguageToolEdit] = []
    skipped: list[LanguageToolEdit] = []
    for match in matches:
        edit, reason = _normalize_match(text, match)
        if edit is None:
            skipped.append(
                LanguageToolEdit(
                    offset=0,
                    length=0,
                    original="",
                    replacement="",
                    rule_id=_nested_string(match, "rule", "id") or "unknown",
                    category=(
                        _nested_string(match, "rule", "category", "id")
                        or "unknown"
                    ),
                    issue_type=(
                        _nested_string(match, "rule", "issueType") or "unknown"
                    ),
                    message=(
                        match.get("message", "")
                        if isinstance(match, dict)
                        and isinstance(match.get("message"), str)
                        else ""
                    ),
                    reason=reason or "malformed_match",
                )
            )
        elif reason:
            skipped.append(replace(edit, reason=reason))
        else:
            candidates.append(edit)
    return candidates, skipped


def _normalize_match(
    text: str, match: Any
) -> tuple[LanguageToolEdit | None, str | None]:
    if not isinstance(match, dict):
        return None, "malformed_match"
    offset_units = match.get("offset")
    length_units = match.get("length")
    if (
        not isinstance(offset_units, int)
        or isinstance(offset_units, bool)
        or not isinstance(length_units, int)
        or isinstance(length_units, bool)
        or offset_units < 0
        or length_units < 0
    ):
        return None, "invalid_offset"
    start = _utf16_index(text, offset_units)
    end = _utf16_index(text, offset_units + length_units)
    if start is None or end is None or end < start:
        return None, "invalid_offset"
    replacements = match.get("replacements")
    if not isinstance(replacements, list) or not replacements:
        return None, "no_replacement"
    replacement_values = [
        item.get("value")
        for item in replacements
        if isinstance(item, dict) and isinstance(item.get("value"), str)
    ]
    if not replacement_values:
        return None, "no_replacement"
    replacement = replacement_values[0]
    rule_id = _nested_string(match, "rule", "id") or "unknown"
    category = (
        _nested_string(match, "rule", "category", "id") or "unknown"
    )
    issue_type = _nested_string(match, "rule", "issueType") or "unknown"
    message = match.get("message") if isinstance(match.get("message"), str) else ""
    edit = LanguageToolEdit(
        offset=start,
        length=end - start,
        original=text[start:end],
        replacement=replacement,
        rule_id=rule_id,
        category=category,
        issue_type=issue_type,
        message=message,
    )
    if replacement == edit.original:
        return edit, "no_effect"
    return edit, None


def _conflicting_indexes(edits: list[LanguageToolEdit]) -> set[int]:
    conflicts: set[int] = set()
    for left_index, left in enumerate(edits):
        left_end = left.offset + max(left.length, 1)
        for right_index in range(left_index + 1, len(edits)):
            right = edits[right_index]
            right_end = right.offset + max(right.length, 1)
            if left.offset < right_end and right.offset < left_end:
                conflicts.update((left_index, right_index))
    return conflicts


def _changed_protected_categories(source: str, candidate: str) -> list[str]:
    source_values = protected_values(source)
    candidate_values = protected_values(candidate)
    return [
        category
        for category in PROTECTED_CATEGORIES
        if source_values[category] != candidate_values[category]
    ]


def _contains_unsafe_text(value: str) -> bool:
    return any(
        (ord(character) < 32 and character not in "\t\r\n")
        for character in value
    )


def _utf16_index(text: str, code_units: int) -> int | None:
    consumed = 0
    if code_units == 0:
        return 0
    for index, character in enumerate(text, start=1):
        consumed += 2 if ord(character) > 0xFFFF else 1
        if consumed == code_units:
            return index
        if consumed > code_units:
            return None
    return len(text) if consumed == code_units else None


def _nested_string(value: Any, *path: str) -> str | None:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) else None


def _find_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


_SHARED_RUNTIME: LanguageToolRuntime | None = None
_SHARED_RUNTIME_LOCK = threading.Lock()


def shared_languagetool_runtime() -> LanguageToolRuntime:
    global _SHARED_RUNTIME
    with _SHARED_RUNTIME_LOCK:
        if _SHARED_RUNTIME is None:
            _SHARED_RUNTIME = LanguageToolRuntime()
        return _SHARED_RUNTIME
