from __future__ import annotations

import subprocess
import json
from pathlib import Path

import pytest

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.errors import OfflineWritingMalformedOutput
from offline_writing_reviser.core.models import WritingRevisionResult
from offline_writing_reviser.core.paraphrase import (
    PARAPHRASE_INSTRUCTION,
    ParaphraseService,
    validate_paraphrase_output,
)
from offline_writing_reviser.windows.controller import OfflineWritingController
from offline_writing_reviser.windows.controller import (
    start_offline_writing_runtime,
)
from offline_writing_reviser.windows.owned_processes import (
    cleanup_owned_languagetool_processes,
)
from offline_writing_reviser.production_acceptance import (
    ACCEPTANCE_ENVIRONMENT,
    run_production_acceptance,
)


class FakeProvider:
    provider_name = "ollama_cli"
    model_identifier = "gemma3:4b"

    def __init__(self, output: str):
        self.output = output
        self.calls: list[tuple[str, str, float]] = []

    def is_available(self) -> bool:
        return True

    def revise(self, text: str, instruction: str, timeout_seconds: float) -> str:
        self.calls.append((text, instruction, timeout_seconds))
        return self.output


def test_paraphrase_prompt_allows_restructuring_but_preserves_facts():
    assert "Paraphrase" in PARAPHRASE_INSTRUCTION
    assert "restructure" in PARAPHRASE_INSTRUCTION
    assert "names, numbers, dates" in PARAPHRASE_INSTRUCTION
    assert "Return only the revised text" in PARAPHRASE_INSTRUCTION
    assert "minimum changes" not in PARAPHRASE_INSTRUCTION


def test_paraphrase_service_routes_to_gemma_and_returns_revised_text_only():
    provider = FakeProvider(
        "We had a productive meeting and identified several priorities "
        "for next week."
    )
    service = ParaphraseService(provider, OfflineWritingConfig())
    source = (
        "The meeting was good and we talked about several things that we "
        "need to work on next week."
    )

    result = service.revise(source)

    assert result.revised_text == provider.output
    assert provider.calls[0][0] == source
    assert provider.calls[0][1] == PARAPHRASE_INSTRUCTION
    assert result.metadata["mode"] == "paraphrase"


def test_paraphrase_validator_preserves_numbers_names_and_paragraphs():
    source = (
        "Jordan Lee reviewed 12 items on July 8, 2026.\r\n\r\n"
        "Microsoft will deliver 3 updates."
    )
    output = (
        "On July 8, 2026, Jordan Lee went over 12 items.\n\n"
        "Microsoft is set to provide 3 updates."
    )

    validation = validate_paraphrase_output(source, output)

    assert validation["accepted"] is True
    assert validation["reasons"] == []


@pytest.mark.parametrize(
    ("output", "reason"),
    [
        ("", "empty_output"),
        ("Here is the revised text: A clearer sentence.", "commentary"),
        ("```text\nA clearer sentence.\n```", "unexpected_markdown_wrapper"),
        ("A fragment,", "truncated_output"),
        ("Visit https://example.com for details.", "hallucinated_url"),
    ],
)
def test_paraphrase_validator_rejects_unsafe_output(output, reason):
    validation = validate_paraphrase_output(
        "This is a complete source sentence.", output
    )

    assert validation["accepted"] is False
    assert reason in validation["reasons"]


def test_paraphrase_validator_rejects_number_loss():
    validation = validate_paraphrase_output(
        "The 2026 budget includes 15 projects.",
        "The budget covers several projects.",
    )

    assert validation["accepted"] is False
    assert "numbers_not_preserved" in validation["reasons"]


def test_paraphrase_validator_rejects_substantial_paragraph_loss():
    validation = validate_paraphrase_output(
        "First paragraph is here.\n\nSecond paragraph is here.",
        "Both points appear together in one paragraph.",
    )

    assert validation["accepted"] is False
    assert "paragraph_structure_lost" in validation["reasons"]


def test_paraphrase_service_rejects_truncated_response():
    service = ParaphraseService(
        FakeProvider("This response stops,"),
        OfflineWritingConfig(),
    )

    with pytest.raises(OfflineWritingMalformedOutput):
        service.revise("This source sentence is complete and meaningful.")


def test_controller_routes_paraphrase_hotkey_to_separate_service():
    calls: list[str] = []

    class Service:
        def __init__(self, mode: str, output: str):
            self.mode = mode
            self.output = output

        def revise(self, text: str):
            calls.append(self.mode)
            return WritingRevisionResult(
                len(text), self.output, "fake", "gemma3:4b", 1.0
            )

    class Adapter:
        replacement = None

        def capture(self):
            return type("Capture", (), {"text": "Original sentence."})()

        def replace(self, _capture, replacement):
            self.replacement = replacement
            return True

    adapter = Adapter()
    controller = OfflineWritingController(
        Service("proofread", "Proofread."),
        adapter,
        paraphrase_service=Service("paraphrase", "Paraphrased."),
    )

    controller._run_revision("paraphrase")

    assert calls == ["paraphrase"]
    assert adapter.replacement == "Paraphrased."


def test_production_runtime_registers_proofread_and_paraphrase_hotkeys(
    monkeypatch,
):
    captured = []

    class Manager:
        def __init__(self, bindings, logger=None):
            self.bindings = bindings
            self.registered_count = 0
            self.all_registered = False
            captured.extend(bindings)

        def start(self):
            self.registered_count = len(self.bindings)
            self.all_registered = True

        def stop(self):
            pass

    monkeypatch.setattr(
        "offline_writing_reviser.windows.controller.WindowsHotkeyManager",
        Manager,
    )

    runtime = start_offline_writing_runtime(OfflineWritingConfig())
    try:
        assert [binding.shortcut for binding in captured] == [
            "Ctrl+Alt+W",
            "Ctrl+Alt+P",
        ]
        assert captured[0].callback == runtime.controller.trigger_proofread
        assert captured[1].callback == runtime.controller.trigger_paraphrase
        assert runtime.has_registered_hotkeys is True
    finally:
        runtime.stop()


def test_build_and_installer_require_windowed_startup_and_per_user_run_entry():
    root = Path(__file__).resolve().parents[1]
    build = (root / "scripts" / "build.ps1").read_text(encoding="utf-8")
    installer = (
        root / "installer" / "OfflineWritingReviser.iss"
    ).read_text(encoding="utf-8")

    assert "--windowed" in build
    assert "--console" not in build
    assert "Software\\Microsoft\\Windows\\CurrentVersion\\Run" in installer
    assert 'ValueName: "OfflineWritingReviser"' in installer
    assert "uninsdeletevalue" in installer
    assert "runhidden" in installer


def test_ollama_cli_commands_use_create_no_window(monkeypatch):
    import offline_writing_reviser.providers.ollama as ollama

    calls = []
    monkeypatch.setattr(ollama.shutil, "which", lambda _name: "ollama.exe")
    monkeypatch.setattr(
        ollama.subprocess,
        "run",
        lambda args, **kwargs: calls.append((args, kwargs))
        or subprocess.CompletedProcess(args, 0, "NAME\n", ""),
    )

    ollama.OllamaCliOfflineWritingProvider("gemma3:4b").list_installed_models()

    assert calls[0][1]["creationflags"] & getattr(
        subprocess, "CREATE_NO_WINDOW", 0x08000000
    )


def test_orphan_cleanup_stops_only_this_installations_bundled_java(tmp_path):
    owned_java = (
        tmp_path / "app" / "runtime" / "java" / "bin" / "java.exe"
    )
    unrelated_java = tmp_path / "another-product" / "java.exe"
    terminated = []

    stopped = cleanup_owned_languagetool_processes(
        owned_java,
        process_paths=[
            (101, owned_java),
            (102, unrelated_java),
            (103, Path("C:/Windows/System32/java.exe")),
        ],
        terminate=lambda process_id: terminated.append(process_id) or True,
    )

    assert stopped == [101]
    assert terminated == [101]


def test_exit_waits_for_controller_shutdown_and_cleans_owned_java(monkeypatch):
    import offline_writing_reviser.application as application

    calls = []
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(
        application, "send_control_command", lambda command: True
    )
    monkeypatch.setattr(
        application,
        "wait_for_control_server_stop",
        lambda: calls.append("controller_stopped") or True,
    )
    monkeypatch.setattr(
        application,
        "cleanup_owned_languagetool_processes",
        lambda _path: calls.append("java_cleaned") or [],
    )

    assert application.execute_control_command(
        application.ControlCommand.EXIT
    ) == 0
    assert calls == ["controller_stopped", "java_cleaned"]


def test_gated_production_acceptance_uses_shared_services_and_stops_runtime(
    monkeypatch,
    tmp_path,
):
    calls = []

    class Service:
        def __init__(self, mode):
            self.mode = mode

        def revise(self, text):
            calls.append((self.mode, text))
            return WritingRevisionResult(
                len(text),
                f"{self.mode}: {text}",
                "ollama_cli",
                "gemma3:4b",
                0.1,
                metadata={"mode": self.mode},
            )

    class LanguageTool:
        process = type("Process", (), {"pid": 42})()
        is_running = True

        def dependency_status(self):
            return {
                "java_path": "installed/app/runtime/java/bin/java.exe",
                "java_found": True,
                "running": True,
            }

        def stop(self):
            self.is_running = False
            calls.append(("language_tool", "stopped"))

    language_tool = LanguageTool()
    monkeypatch.setenv(ACCEPTANCE_ENVIRONMENT, "1")
    monkeypatch.setattr(
        "offline_writing_reviser.production_acceptance.SettingsStore.load",
        lambda _self: OfflineWritingConfig(),
    )
    monkeypatch.setattr(
        "offline_writing_reviser.production_acceptance.configure_logging",
        lambda _path: None,
    )
    monkeypatch.setattr(
        "offline_writing_reviser.production_acceptance.build_production_services",
        lambda _config, logger=None: (
            Service("proofread"),
            Service("paraphrase"),
            language_tool,
        ),
    )
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(
        json.dumps(
            {
                "cases": [
                    {"id": "one", "mode": "proofread", "input": "Text."},
                    {
                        "id": "two",
                        "mode": "paraphrase",
                        "input": "Other.",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    assert run_production_acceptance(request_path, response_path) == 0

    response = json.loads(response_path.read_text(encoding="utf-8"))
    assert [item["output"] for item in response["results"]] == [
        "proofread: Text.",
        "paraphrase: Other.",
    ]
    assert response["language_tool"]["process_id"] == 42
    assert response["language_tool"]["running_after_stop"] is False
    assert calls[-1] == ("language_tool", "stopped")


def test_production_acceptance_rejects_ungated_invocation(tmp_path):
    assert (
        run_production_acceptance(
            tmp_path / "missing-request.json",
            tmp_path / "response.json",
        )
        == 2
    )
    assert not (tmp_path / "response.json").exists()
