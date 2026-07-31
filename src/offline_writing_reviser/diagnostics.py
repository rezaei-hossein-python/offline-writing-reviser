from __future__ import annotations

import ctypes
import json
import sys
import time
from typing import Any

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.service import OfflineWritingService
from offline_writing_reviser.paths import executable_path
from offline_writing_reviser.providers.base import OfflineWritingProviderError
from offline_writing_reviser.providers.ollama import (
    OllamaCliOfflineWritingProvider,
)
from offline_writing_reviser.settings import SettingsStore
from offline_writing_reviser.version import __version__


def collect_diagnostics(
    config: OfflineWritingConfig | None = None,
    *,
    include_gemma_test: bool = False,
    provider: OllamaCliOfflineWritingProvider | None = None,
) -> tuple[dict[str, Any], bool]:
    store = SettingsStore()
    config = config or store.load()
    provider = provider or OllamaCliOfflineWritingProvider(
        model=config.model, executable=config.ollama_executable
    )
    report: dict[str, Any] = {
        "application": {
            "name": "Offline Writing Reviser",
            "version": __version__,
            "executable": str(executable_path()),
            "frozen": bool(getattr(sys, "frozen", False)),
            "configuration": {
                "enabled": config.enabled,
                "provider": config.provider,
                "model": config.model,
                "hotkey": config.hotkey,
                "timeout_seconds": config.timeout_seconds,
                "max_characters": config.max_characters,
                "chunk_characters": config.chunk_characters,
            },
            "settings_found": store.path.is_file(),
            "log_directory_writable": _directory_writable(
                config.log_file.parent
            ),
        },
        "ollama": {
            "found": False,
            "executable": None,
            "version": None,
            "api_reachable": False,
            "required_model": config.model,
            "required_model_installed": False,
            "model_usable": None,
            "runtime": {
                "model_loaded": False,
                "acceleration": "unknown",
                "device": None,
                "backend": None,
            },
        },
        "hardware": _memory_status(),
        "revision": {
            "health_test_requested": include_gemma_test,
            "health_test_passed": None,
            "latency_seconds": None,
        },
        "limitations": [
            "Ollama reports VRAM offload but may not expose the GPU vendor or "
            "compute backend."
        ],
    }
    healthy = True
    try:
        report["ollama"]["executable"] = provider.resolved_executable()
        report["ollama"]["found"] = True
    except OfflineWritingProviderError as exc:
        report["ollama"]["executable_error"] = (
            f"{exc.__class__.__name__}: {exc}"
        )
        healthy = False
    try:
        provider.ensure_api_running(timeout_seconds=20.0)
        report["ollama"]["version"] = (
            provider.api_version(timeout_seconds=5.0)
            or provider.cli_version(timeout_seconds=5.0)
        )
        models = provider.api_models(timeout_seconds=5.0)
        report["ollama"]["api_reachable"] = True
        report["ollama"]["required_model_installed"] = config.model in models
        report["ollama"]["runtime"] = provider.runtime_diagnostics(
            timeout_seconds=5.0
        )
        if config.model not in models:
            healthy = False
    except OfflineWritingProviderError as exc:
        report["ollama"]["error"] = f"{exc.__class__.__name__}: {exc}"
        healthy = False

    if include_gemma_test and report["ollama"]["required_model_installed"]:
        started = time.perf_counter()
        try:
            result = OfflineWritingService(
                provider=provider, config=config
            ).revise("She work in the finance department.")
            passed = result.revised_text == (
                "She works in the finance department."
            )
            report["revision"]["health_test_passed"] = passed
            report["revision"]["latency_seconds"] = (
                time.perf_counter() - started
            )
            report["ollama"]["model_usable"] = passed
            try:
                report["ollama"]["runtime"] = provider.runtime_diagnostics(
                    timeout_seconds=5.0
                )
            except OfflineWritingProviderError:
                pass
            healthy = healthy and passed
        except Exception as exc:
            report["revision"]["health_error"] = (
                f"{exc.__class__.__name__}: {exc}"
            )
            report["revision"]["health_test_passed"] = False
            report["ollama"]["model_usable"] = False
            healthy = False
    return report, healthy


def format_diagnostics(report: dict[str, Any]) -> str:
    application = report["application"]
    ollama = report["ollama"]
    runtime = ollama["runtime"]
    hardware = report["hardware"]
    revision = report["revision"]
    lines = [
        "Offline Writing Reviser Diagnostics",
        "===================================",
        "",
        "Application",
        f"  Version: {application['version']}",
        f"  Executable: {application['executable']}",
        f"  Packaged: {_yes_no(application['frozen'])}",
        f"  Hotkey: {application['configuration']['hotkey']}",
        f"  Model: {application['configuration']['model']}",
        f"  Settings found: {_yes_no(application['settings_found'])}",
        "  Log directory writable: "
        f"{_yes_no(application['log_directory_writable'])}",
        "",
        "Ollama",
        f"  Found: {_yes_no(ollama['found'])}",
        f"  Version: {ollama.get('version') or 'Unavailable'}",
        f"  API reachable: {_yes_no(ollama['api_reachable'])}",
        f"  Required model: {ollama['required_model']}",
        "  Required model installed: "
        f"{_yes_no(ollama['required_model_installed'])}",
        f"  Model loaded: {_yes_no(runtime.get('model_loaded'))}",
        f"  Acceleration: {runtime.get('acceleration', 'unknown')}",
        f"  Model VRAM bytes: {runtime.get('model_vram_bytes')}",
        f"  Context length: {runtime.get('context_length')}",
        f"  Device: {runtime.get('device') or 'Not exposed by Ollama'}",
        f"  Backend: {runtime.get('backend') or 'Not exposed by Ollama'}",
        "",
        "Hardware",
        f"  Total physical RAM bytes: {hardware.get('total_ram_bytes')}",
        f"  Available physical RAM bytes: {hardware.get('available_ram_bytes')}",
        "",
        "Revision engine",
        "  Health test requested: "
        f"{_yes_no(revision['health_test_requested'])}",
        "  Health test: "
        f"{_test_state(revision['health_test_passed'])}",
        f"  Latency seconds: {revision['latency_seconds']}",
        "",
        "Limitations",
    ]
    lines.extend(f"  - {item}" for item in report["limitations"])
    errors = _collect_errors(report)
    if errors:
        lines.extend(["", "Errors"])
        lines.extend(f"  - {item}" for item in errors)
    return "\n".join(lines)


def diagnostics_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)


def _directory_writable(path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path.is_dir()
    except OSError:
        return False


def _memory_status() -> dict[str, int | None]:
    if sys.platform != "win32":
        return {"total_ram_bytes": None, "available_ram_bytes": None}

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {"total_ram_bytes": None, "available_ram_bytes": None}
    return {
        "total_ram_bytes": int(status.ullTotalPhys),
        "available_ram_bytes": int(status.ullAvailPhys),
    }


def _collect_errors(value: Any, prefix: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            label = f"{prefix}.{key}" if prefix else key
            if key.endswith("error") and nested:
                errors.append(f"{label}: {nested}")
            elif isinstance(nested, dict):
                errors.extend(_collect_errors(nested, label))
    return errors


def _yes_no(value: Any) -> str:
    return "Yes" if value else "No"


def _test_state(value: bool | None) -> str:
    if value is None:
        return "Not run"
    return "Passed" if value else "Failed"
