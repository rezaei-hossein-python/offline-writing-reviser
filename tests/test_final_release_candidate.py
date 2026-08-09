from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

import pytest

from offline_writing_reviser.config import OfflineWritingConfig
from offline_writing_reviser.core.service import OfflineWritingService
from offline_writing_reviser.proofreading.semantic import (
    validate_semantic_preservation,
)
from offline_writing_reviser.windows.controller import (
    start_offline_writing_runtime,
)
from offline_writing_reviser.windows.text_selection import (
    ClipboardSnapshot,
    SelectedTextCapture,
    WindowsSelectedTextAdapter,
)


class MappingProvider:
    provider_name = "ollama_cli"
    model_identifier = "gemma3:4b"

    def __init__(self, responses=None):
        self.responses = deque(responses or [])
        self.calls = []

    def is_available(self):
        return True

    def revise(self, text, instruction, timeout_seconds):
        self.calls.append(
            {
                "text": text,
                "instruction": instruction,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.responses.popleft() if self.responses else text


@pytest.mark.parametrize(
    ("source", "candidate", "expected"),
    [
        (
            "The meeting starts at nine tomorrow morning.",
            "The meeting starts at nine tomorrow morning.",
            "The meeting starts at nine tomorrow morning.",
        ),
        (
            "I recieved the adress yesterday.",
            "I received the address yesterday.",
            "I received the address yesterday.",
        ),
        (
            "He go to work every day.",
            "He goes to work every day.",
            "He goes to work every day.",
        ),
        (
            "I am writing this email for informing you about the issue.",
            "I am writing this email to inform you about the issue.",
            "I am writing this email to inform you about the issue.",
        ),
        (
            "The meeting was very good and we discussed about many important things.",
            "The meeting went very well, and we discussed many important things.",
            "The meeting went very well, and we discussed many important things.",
        ),
        (
            "The meeting is on September 15 at 9:30 AM and costs $125.",
            "The meeting is on September 16 at 9:30 AM and costs $125.",
            "The meeting is on September 15 at 9:30 AM and costs $125.",
        ),
        (
            "I do not approve this request.",
            "I approve this request.",
            "I do not approve this request.",
        ),
        (
            "Could you please send the report by Friday?",
            "Please send the report by Friday.",
            "Could you please send the report by Friday?",
        ),
        (
            "Email ops@example.com and review https://example.com/a.",
            "Email sales@example.com and review https://example.com/b.",
            "Email ops@example.com and review https://example.com/a.",
        ),
        (
            'Keep the exact setting "retry=false" for API-42.',
            'Keep the exact setting "retry=true" for API-43.',
            'Keep the exact setting "retry=false" for API-42.',
        ),
    ],
)
def test_release_acceptance_semantic_matrix(source, candidate, expected):
    result = OfflineWritingService(MappingProvider([candidate])).revise(source)
    assert result.revised_text == expected


@pytest.mark.parametrize("target_words", [100, 500, 1000, 2000])
def test_long_text_chunking_is_complete_and_semantically_safe(target_words):
    sentence = (
        "The review is on September 15 at 9:30 AM, costs $125, and does not "
        "change API-42."
    )
    repeats = max(1, target_words // len(sentence.split()))
    source = "\n\n".join(sentence for _ in range(repeats))
    provider = MappingProvider()
    service = OfflineWritingService(
        provider,
        OfflineWritingConfig(
            max_characters=100_000,
            chunk_characters=2000,
        ),
    )

    result = service.revise(source)

    assert result.revised_text == source
    assert len(provider.calls) >= 1
    assert all(call["text"] in source for call in provider.calls)
    assert all(call["text"].strip() == call["text"] for call in provider.calls)
    assert validate_semantic_preservation(source, result.revised_text).accepted


def test_fifty_repeated_revision_cycles_without_restart():
    responses = []
    sources = []
    for index in range(50):
        if index % 4 == 0:
            source = f"Correct note number {index}."
            revised = source
        elif index % 4 == 1:
            source = f"I recieved item {index} yesterday."
            revised = f"I received item {index} yesterday."
        elif index % 4 == 2:
            source = f"He go to office {index} every day."
            revised = f"He goes to office {index} every day."
        else:
            source = f"We discussed about project API-{index}."
            revised = f"We discussed project API-{index}."
        sources.append(source)
        responses.append(revised)
    provider = MappingProvider(responses)
    service = OfflineWritingService(provider)

    outputs = [service.revise(source).revised_text for source in sources]

    assert len(outputs) == 50
    assert len(provider.calls) == 50
    assert all(
        validate_semantic_preservation(source, output).accepted
        for source, output in zip(sources, outputs, strict=True)
    )


def test_runtime_registers_only_primary_ctrl_alt_p(monkeypatch):
    captured = []

    class Manager:
        registered_count = 1
        all_registered = True

        def __init__(self, bindings, logger=None):
            captured.extend(bindings)

        def start(self):
            return None

        def stop(self):
            return None

    monkeypatch.setattr(
        "offline_writing_reviser.windows.controller.WindowsHotkeyManager",
        Manager,
    )
    runtime = start_offline_writing_runtime(OfflineWritingConfig())
    try:
        assert len(captured) == 1
        assert captured[0].shortcut == "Ctrl+Alt+P"
    finally:
        runtime.stop()


class SequenceClipboard:
    def __init__(self):
        self.sequence = 10
        self.snapshots = deque(
            [ClipboardSnapshot(tuple()), ClipboardSnapshot(tuple())]
        )
        self.restored = []

    def snapshot(self):
        return self.snapshots.popleft()

    def set_unicode_text(self, _text):
        self.sequence += 1

    def get_sequence_number(self):
        return self.sequence

    def restore(self, snapshot):
        self.restored.append(snapshot)


def test_external_clipboard_change_during_paste_is_not_overwritten(monkeypatch):
    import offline_writing_reviser.windows.text_selection as selection

    clipboard = SequenceClipboard()
    monkeypatch.setattr(selection, "get_foreground_window", lambda: 100)
    monkeypatch.setattr(selection, "is_standard_edit_control", lambda _hwnd: False)

    def paste(_vk, logger=None):
        clipboard.sequence += 1
        return True

    monkeypatch.setattr(selection, "_send_ctrl_key", paste)
    monkeypatch.setattr(
        selection, "_wait_for_foreground_stability", lambda *_a, **_k: True
    )
    adapter = WindowsSelectedTextAdapter(clipboard=clipboard)
    capture = SelectedTextCapture(
        text="Original.",
        foreground_window=100,
        foreground_pid=1,
        foreground_process="editor.exe",
        clipboard_snapshot=ClipboardSnapshot(tuple()),
    )

    assert adapter.replace(capture, "Revised.") is True
    assert clipboard.restored == []


def test_logs_never_include_selected_or_revised_content(caplog):
    source = "SECRET-SOURCE-4f77 should be revised."
    candidate = "SECRET-OUTPUT-9a31 should be revised."
    service = OfflineWritingService(MappingProvider([candidate]))

    with caplog.at_level(logging.INFO, logger="offline-writing-reviser"):
        service.revise(source)

    assert source not in caplog.text
    assert candidate not in caplog.text
    assert "SECRET-SOURCE-4f77" not in caplog.text
    assert "SECRET-OUTPUT-9a31" not in caplog.text


def test_packaging_contains_private_runtime_without_removed_hybrid_engine():
    root = Path(__file__).resolve().parents[1]
    build = (root / "scripts" / "build.ps1").read_text(encoding="utf-8")
    installer = (
        root / "installer" / "OfflineWritingReviser.iss"
    ).read_text(encoding="utf-8")
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src" / "offline_writing_reviser").rglob("*.py")
    )

    assert "runtime\\java" in build
    assert "runtime\\languagetool" in build
    assert "vendor\\java" in build
    assert "vendor\\languagetool" in build
    assert "bin\\javaw.exe" in build
    preparation = (
        root / "scripts" / "prepare-languagetool-runtime.ps1"
    ).read_text(encoding="utf-8")
    assert "testrules.bat" in preparation
    assert "testrules.sh" in preparation
    assert "languagetool-core-tests.jar" in preparation
    assert "gemma3:4b" not in build
    assert ".gguf" not in build.casefold()
    assert "runtime\\java" not in installer
    assert "runtime\\languagetool" not in installer
    assert "vendor\\java" not in installer
    assert "vendor\\languagetool" not in installer
    assert "gemma3:4b" not in installer
    assert ".gguf" not in installer.casefold()
    assert "HybridProofreadingService" not in production
    assert "hybrid_service" not in production
    assert "proofreading.policy" not in production
    assert "ParaphraseService" not in production
    assert "Ctrl+Alt+W" not in installer


def test_build_and_installer_paths_are_relocatable():
    root = Path(__file__).resolve().parents[1]
    build = (root / "scripts" / "build.ps1").read_text(encoding="utf-8")
    installer = (
        root / "installer" / "OfflineWritingReviser.iss"
    ).read_text(encoding="utf-8")

    assert "$PSScriptRoot" in build
    assert "$ProjectRoot" in build
    assert str(root) not in build
    assert '#define ProjectRoot AddBackslash(SourcePath) + ".."' in installer
    assert (
        '#define AppBuildDir ProjectRoot + "\\dist\\OfflineWritingReviser"'
        in installer
    )
    assert str(root) not in installer
