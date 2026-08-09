from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest

from offline_writing_reviser.windows.controller import OfflineWritingController
from offline_writing_reviser.windows.text_selection import (
    CaptureFailure,
    CaptureState,
    ClipboardSnapshot,
    SelectionCaptureError,
    SelectionTarget,
    VK_P,
    WM_COPY,
    WM_PASTE,
    WindowsSelectedTextAdapter,
)


class DeterministicClipboard:
    def __init__(self) -> None:
        self.sequence = 10
        self.text = ""
        self.available = False
        self.restore_count = 0
        self.writes: list[str] = []

    def snapshot(self) -> ClipboardSnapshot:
        return ClipboardSnapshot(tuple())

    def clear(self) -> None:
        self.sequence += 1
        self.text = ""
        self.available = False

    def get_sequence_number(self) -> int:
        return self.sequence

    def has_unicode_text(self) -> bool:
        return self.available

    def get_unicode_text(self) -> str:
        return self.text

    def restore(self, _snapshot: ClipboardSnapshot) -> None:
        self.restore_count += 1

    def set_unicode_text(self, text: str) -> None:
        self.writes.append(text)


def target(mode: str = "revision") -> SelectionTarget:
    return SelectionTarget(
        foreground_window=100,
        focused_window=101,
        foreground_pid=55,
        foreground_process="editor.exe",
        mode=mode,
        action_key=VK_P,
        operation_id="operation123",
    )


def test_standard_editor_uses_synchronous_copy_and_paste(monkeypatch):
    import offline_writing_reviser.windows.text_selection as selection

    clipboard = DeterministicClipboard()
    messages: list[int] = []
    monkeypatch.setattr(selection, "get_foreground_window", lambda: 100)
    monkeypatch.setattr(
        selection, "_wait_for_modifier_release", lambda **_kwargs: True
    )
    monkeypatch.setattr(selection, "is_standard_edit_control", lambda _hwnd: True)
    monkeypatch.setattr(selection, "control_has_selection", lambda _hwnd: True)
    monkeypatch.setattr(
        selection,
        "_wait_for_foreground_stability",
        lambda *_args, **_kwargs: True,
    )

    def send_message(_hwnd: int, message: int) -> bool:
        messages.append(message)
        if message == WM_COPY:
            clipboard.text = "Selected text."
            clipboard.available = True
            clipboard.sequence += 1
        return True

    monkeypatch.setattr(selection, "send_control_message", send_message)
    adapter = WindowsSelectedTextAdapter(
        clipboard=clipboard, copy_wait_seconds=0.01
    )

    capture = adapter.capture(target())

    assert capture is not None
    assert capture.text == "Selected text."
    assert capture.operation is not None
    assert capture.operation.captured_character_count == 14
    assert adapter.replace(capture, "Revised text.") is True
    assert messages == [WM_COPY, WM_PASTE]
    assert capture.operation.state is CaptureState.COMPLETED
    assert capture.operation.paste_success is True


def test_actual_empty_standard_selection_has_distinct_failure(monkeypatch):
    import offline_writing_reviser.windows.text_selection as selection

    clipboard = DeterministicClipboard()
    monkeypatch.setattr(selection, "get_foreground_window", lambda: 100)
    monkeypatch.setattr(
        selection, "_wait_for_modifier_release", lambda **_kwargs: True
    )
    monkeypatch.setattr(selection, "is_standard_edit_control", lambda _hwnd: True)
    monkeypatch.setattr(selection, "control_has_selection", lambda _hwnd: False)
    adapter = WindowsSelectedTextAdapter(clipboard=clipboard)

    with pytest.raises(SelectionCaptureError) as caught:
        adapter.capture(target())

    assert caught.value.failure is CaptureFailure.NO_SELECTION
    assert clipboard.restore_count == 1


def test_copy_timeout_is_not_mislabeled_as_no_selection(monkeypatch):
    import offline_writing_reviser.windows.text_selection as selection

    clipboard = DeterministicClipboard()
    monkeypatch.setattr(selection, "get_foreground_window", lambda: 100)
    monkeypatch.setattr(
        selection, "_wait_for_modifier_release", lambda **_kwargs: True
    )
    monkeypatch.setattr(selection, "is_standard_edit_control", lambda _hwnd: True)
    monkeypatch.setattr(selection, "control_has_selection", lambda _hwnd: True)
    monkeypatch.setattr(selection, "send_control_message", lambda *_args: True)
    adapter = WindowsSelectedTextAdapter(
        clipboard=clipboard, copy_wait_seconds=0.001
    )

    with pytest.raises(SelectionCaptureError) as caught:
        adapter.capture(target())

    assert caught.value.failure is CaptureFailure.COPY_TIMEOUT
    assert caught.value.failure is not CaptureFailure.NO_SELECTION


def test_target_is_captured_synchronously_before_worker_starts():
    calls: list[tuple[str, str]] = []
    completed = threading.Event()

    class Adapter:
        def capture_target(self, mode):
            calls.append(("target", threading.current_thread().name))
            return target(mode)

        def capture(self, captured_target, mode):
            calls.append(("capture", threading.current_thread().name))
            raise SelectionCaptureError(
                CaptureFailure.COPY_TIMEOUT, CaptureState.FAILED
            )

    class Service:
        def revise(self, _text):
            raise AssertionError("capture failure must not reach service")

    controller = OfflineWritingController(
        Service(),
        Adapter(),
        notification_callback=lambda _message: completed.set(),
    )

    controller.trigger()

    assert completed.wait(2)
    controller.stop()
    assert calls[0] == ("target", threading.current_thread().name)
    assert calls[1][0] == "capture"
    assert calls[1][1] != calls[0][1]


def test_telemetry_is_metadata_only(monkeypatch, caplog):
    import offline_writing_reviser.windows.text_selection as selection

    secret = "Confidential selected sentence."
    clipboard = DeterministicClipboard()
    monkeypatch.setattr(selection, "get_foreground_window", lambda: 100)
    monkeypatch.setattr(
        selection, "_wait_for_modifier_release", lambda **_kwargs: True
    )
    monkeypatch.setattr(selection, "is_standard_edit_control", lambda _hwnd: True)
    monkeypatch.setattr(selection, "control_has_selection", lambda _hwnd: True)

    def copy(_hwnd, _message):
        clipboard.text = secret
        clipboard.available = True
        clipboard.sequence += 1
        return True

    monkeypatch.setattr(selection, "send_control_message", copy)
    adapter = WindowsSelectedTextAdapter(clipboard=clipboard)

    with caplog.at_level(logging.INFO, logger="offline-writing-reviser"):
        capture = adapter.capture(target())
        assert capture is not None
        adapter.complete_without_replacement(capture)

    assert secret not in caplog.text
    assert "captured_chars=31" in caplog.text
    assert "failure_code=none" in caplog.text


def test_installer_uses_one_quoted_canonical_windowless_entry_point():
    root = Path(__file__).resolve().parents[1]
    installer = (
        root / "installer" / "OfflineWritingReviser.iss"
    ).read_text(encoding="utf-8")
    build = (root / "scripts" / "build.ps1").read_text(encoding="utf-8")

    assert 'ValueData: """{app}\\{#AppExeName}"""' in installer
    assert (
        'Filename: "{app}\\{#AppExeName}"; Description: '
        '"Start Offline Writing Reviser"; Flags: nowait runhidden'
    ) in installer
    assert 'Filename: "{app}\\{#AppExeName}"; Parameters: "--exit"' in installer
    assert 'Type: filesandordirs; Name: "{app}"' in installer
    assert "--windowed" in build
    assert 'bin\\javaw.exe' in build
    assert "LanguageTool" in build
    assert ".cmd" not in installer
    assert ".bat" not in installer
    assert ".ps1" not in installer
