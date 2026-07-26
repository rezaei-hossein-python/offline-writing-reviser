from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.hybrid_service import (
    HybridProofreadingService,
)
from offline_writing_reviser.paths import executable_path
from offline_writing_reviser.proofreading.languagetool import (
    LANGUAGETOOL_VERSION,
    LanguageToolRuntime,
    hidden_startupinfo,
)
from offline_writing_reviser.proofreading.policy import (
    normalize_matches,
    safe_filter,
)
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
    language_tool: LanguageToolRuntime | None = None,
    provider: OllamaCliOfflineWritingProvider | None = None,
) -> tuple[dict[str, Any], bool]:
    store = SettingsStore()
    config = config or store.load()
    language_tool = language_tool or LanguageToolRuntime()
    try:
        return _collect_diagnostics(
            config=config,
            store=store,
            language_tool=language_tool,
            provider=provider,
            include_gemma_test=include_gemma_test,
        )
    finally:
        language_tool.stop()


def _collect_diagnostics(
    *,
    config: OfflineWritingConfig,
    store: SettingsStore,
    language_tool: LanguageToolRuntime,
    provider: OllamaCliOfflineWritingProvider | None,
    include_gemma_test: bool,
) -> tuple[dict[str, Any], bool]:
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
        "languagetool": language_tool.dependency_status(),
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
        "proofreading": {
            "languagetool_health": False,
            "languagetool_latency_seconds": None,
            "safe_health_test_passed": False,
            "gemma_test_requested": include_gemma_test,
            "gemma_health_test_passed": None,
            "gemma_latency_seconds": None,
        },
        "limitations": [
            "GPU vendor and backend are reported only when Ollama exposes them; "
            "size_vram can establish offload but not the exact backend."
        ],
    }
    healthy = True
    try:
        java_version = _java_version(language_tool.java_path)
        report["languagetool"]["java_version"] = java_version
    except OSError as exc:
        report["languagetool"]["java_error"] = str(exc)
        healthy = False
    try:
        payload, latency = language_tool.check("This adress is correct.")
        matches = normalize_matches(payload, "This adress is correct.")
        safe_output, _, _ = safe_filter("This adress is correct.", matches)
        report["proofreading"]["languagetool_health"] = True
        report["proofreading"]["languagetool_latency_seconds"] = latency
        report["proofreading"]["safe_health_test_passed"] = (
            safe_output == "This address is correct."
        )
        report["languagetool"]["running"] = language_tool.is_running
        report["languagetool"]["base_url"] = language_tool.base_url
        if not report["proofreading"]["safe_health_test_passed"]:
            healthy = False
    except Exception as exc:
        report["proofreading"]["languagetool_error"] = (
            f"{exc.__class__.__name__}: {exc}"
        )
        healthy = False

    try:
        report["ollama"]["executable"] = provider.resolved_executable()
        report["ollama"]["found"] = True
    except OfflineWritingProviderError as exc:
        report["ollama"]["executable_error"] = (
            f"{exc.__class__.__name__}: {exc}"
        )
    try:
        report["ollama"]["version"] = (
            provider.api_version(timeout_seconds=5.0)
            or provider.cli_version(timeout_seconds=5.0)
        )
        models = provider.api_models(timeout_seconds=5.0)
        report["ollama"]["found"] = True
        report["ollama"]["api_reachable"] = True
        report["ollama"]["required_model_installed"] = (
            config.model in models
        )
        report["ollama"]["runtime"] = provider.runtime_diagnostics(
            timeout_seconds=5.0
        )
        if config.model not in models:
            healthy = False
    except OfflineWritingProviderError as exc:
        report["ollama"]["error"] = f"{exc.__class__.__name__}: {exc}"
        healthy = False

    if include_gemma_test and report["ollama"]["required_model_installed"]:
        gemma_started = time.perf_counter()
        try:
            service = HybridProofreadingService(
                provider=provider,
                language_tool=language_tool,
                config=config,
            )
            result = service.revise("She work in the finance department.")
            report["proofreading"]["gemma_health_test_passed"] = (
                result.revised_text == "She works in the finance department."
            )
            report["proofreading"]["gemma_latency_seconds"] = (
                time.perf_counter() - gemma_started
            )
            report["proofreading"]["gemma_metadata"] = result.metadata
            report["ollama"]["model_usable"] = report["proofreading"][
                "gemma_health_test_passed"
            ]
            try:
                report["ollama"]["runtime"] = provider.runtime_diagnostics(
                    timeout_seconds=5.0
                )
            except OfflineWritingProviderError:
                pass
            if not report["proofreading"]["gemma_health_test_passed"]:
                healthy = False
        except Exception as exc:
            report["proofreading"]["gemma_health_error"] = (
                f"{exc.__class__.__name__}: {exc}"
            )
            report["proofreading"]["gemma_health_test_passed"] = False
            report["ollama"]["model_usable"] = False
            healthy = False
    return report, healthy


def format_diagnostics(report: dict[str, Any]) -> str:
    application = report["application"]
    lt = report["languagetool"]
    ollama = report["ollama"]
    runtime = ollama["runtime"]
    hardware = report["hardware"]
    proofreading = report["proofreading"]
    lines = [
        "Offline Writing Reviser Diagnostics",
        "===================================",
        "",
        "Application",
        f"  Version: {application['version']}",
        f"  Executable: {application['executable']}",
        f"  Packaged: {_yes_no(application['frozen'])}",
        f"  Model: {application['configuration']['model']}",
        f"  Settings found: {_yes_no(application['settings_found'])}",
        "  Log directory writable: "
        f"{_yes_no(application['log_directory_writable'])}",
        "",
        "LanguageTool",
        f"  Version: {lt.get('version', LANGUAGETOOL_VERSION)}",
        f"  Bundled Java found: {_yes_no(lt['java_found'])}",
        f"  LanguageTool found: {_yes_no(lt['languagetool_found'])}",
        f"  Java version: {lt.get('java_version', 'Unavailable')}",
        "  Health check: "
        f"{'Passed' if proofreading['languagetool_health'] else 'Failed'}",
        "  SAFE test: "
        f"{'Passed' if proofreading['safe_health_test_passed'] else 'Failed'}",
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
        "Proofreading",
        "  Gemma health test requested: "
        f"{_yes_no(proofreading['gemma_test_requested'])}",
        "  Gemma health test: "
        f"{_test_state(proofreading['gemma_health_test_passed'])}",
        f"  Gemma latency seconds: {proofreading['gemma_latency_seconds']}",
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


def _java_version(path: Path) -> str:
    if not path.is_file():
        raise OSError(f"Bundled Java executable not found: {path}")
    result = subprocess.run(
        [str(path), "-version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        startupinfo=hidden_startupinfo(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        check=False,
    )
    output = (result.stderr or result.stdout).splitlines()
    if result.returncode != 0 or not output:
        raise OSError("Bundled Java version check failed")
    return output[0].strip()


def _directory_writable(path: Path) -> bool:
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
