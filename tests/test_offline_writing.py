import ctypes
import subprocess
import threading
import time

import pytest

from offline_writing_reviser.providers.base import (
    OfflineWritingModelMissing,
    OfflineWritingProvider,
    OfflineWritingProviderError,
    OfflineWritingProviderTimeout,
    OfflineWritingProviderUnavailable,
)
from offline_writing_reviser.core import (
    REVISION_INSTRUCTION,
    OfflineWritingBusy,
    OfflineWritingConfig,
    OfflineWritingInputError,
    OfflineWritingMalformedOutput,
    OfflineWritingService,
    sanitize_revision_output,
)
from offline_writing_reviser.windows.hotkeys import WM_HOTKEY, HotkeyBinding, WindowsHotkeyManager, parse_hotkey
from offline_writing_reviser.windows.controller import OfflineWritingController
from offline_writing_reviser.windows.single_instance import WindowsSingleInstance
from offline_writing_reviser.windows.text_selection import (
    ClipboardSnapshot,
    ClipboardFormatData,
    SelectedTextCapture,
    WindowsClipboard,
    WindowsSelectedTextAdapter,
)


class FakeOfflineWritingProvider(OfflineWritingProvider):
    provider_name = "fake_offline"
    model_identifier = "fake-local"

    def __init__(
        self,
        response: str = "Revised text.",
        available: bool = True,
        error: Exception | None = None,
        delay_seconds: float = 0,
    ):
        self.response = response
        self.available = available
        self.error = error
        self.delay_seconds = delay_seconds
        self.calls = []

    def is_available(self) -> bool:
        return self.available

    def revise(self, text: str, instruction: str, timeout_seconds: float) -> str:
        self.calls.append(
            {
                "text": text,
                "instruction": instruction,
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.error:
            raise self.error
        return self.response


def test_revision_prompt_contract_is_tightly_scoped():
    assert "Return only the revised text" in REVISION_INSTRUCTION
    assert "Do not include explanations" in REVISION_INSTRUCTION
    assert "minimum necessary edits" in REVISION_INSTRUCTION
    assert "Preserve tense" in REVISION_INSTRUCTION
    assert "Respect explicit time markers" in REVISION_INSTRUCTION
    assert "Preserve pronouns" in REVISION_INSTRUCTION
    assert "Preserve names" in REVISION_INSTRUCTION
    assert "numbers" in REVISION_INSTRUCTION
    assert "dates" in REVISION_INSTRUCTION
    assert "email addresses" in REVISION_INSTRUCTION
    assert "URLs" in REVISION_INSTRUCTION
    assert "Do not make wording more formal" in REVISION_INSTRUCTION
    assert "Do not add information" in REVISION_INSTRUCTION
    assert "Do not remove information" in REVISION_INSTRUCTION
    assert "I spoke with the client yesterday" in REVISION_INSTRUCTION


def test_empty_selection_is_rejected():
    service = OfflineWritingService(FakeOfflineWritingProvider())

    with pytest.raises(OfflineWritingInputError):
        service.revise("   ")


def test_multiline_text_is_sent_to_provider():
    provider = FakeOfflineWritingProvider(response="First line.\n\nSecond line.")
    service = OfflineWritingService(provider)

    result = service.revise("First line\n\nSecond line")

    assert result.revised_text == "First line.\n\nSecond line."
    assert provider.calls[0]["text"] == "First line\n\nSecond line"


def test_unicode_text_is_preserved_through_service():
    provider = FakeOfflineWritingProvider(response="Cafe resume - Jose paid EUR 5.")
    service = OfflineWritingService(provider)

    result = service.revise("Café résumé - José paid €5.")

    assert result.revised_text == "Cafe resume - Jose paid EUR 5."
    assert "Café résumé" in provider.calls[0]["text"]


def test_maximum_length_is_enforced():
    service = OfflineWritingService(
        FakeOfflineWritingProvider(),
        config=OfflineWritingConfig(max_characters=5),
    )

    with pytest.raises(OfflineWritingInputError):
        service.revise("too long")


def test_provider_unavailable_fails_locally():
    service = OfflineWritingService(FakeOfflineWritingProvider(available=False))

    with pytest.raises(OfflineWritingProviderUnavailable):
        service.revise("Fix this sentence.")


def test_provider_timeout_fails_locally():
    service = OfflineWritingService(
        FakeOfflineWritingProvider(error=OfflineWritingProviderTimeout("timeout"))
    )

    with pytest.raises(OfflineWritingProviderTimeout):
        service.revise("Fix this sentence.")


def test_malformed_provider_output_is_rejected():
    service = OfflineWritingService(FakeOfflineWritingProvider(response="Here is the revision: ok"))

    with pytest.raises(OfflineWritingMalformedOutput):
        service.revise("Fix this sentence.")


def test_sanitize_revision_output_removes_wrapping_quotes():
    assert sanitize_revision_output('"This is revised."') == "This is revised."


def test_sanitize_revision_output_strips_ansi_sequences():
    output = "Fixed\x1b[3D text\x1b[K and \x1b[31mclean\x1b[0m."

    assert sanitize_revision_output(output) == "Fixed text and clean."


def test_sanitize_revision_output_strips_osc_sequences():
    output = "\x1b]0;terminal title\x07Clean text."

    assert sanitize_revision_output(output) == "Clean text."


def test_sanitize_revision_output_rejects_null_bytes():
    with pytest.raises(OfflineWritingMalformedOutput):
        sanitize_revision_output("Clean\x00 text.")


def test_sanitize_revision_output_strips_other_control_characters():
    output = "Clean\x08 text.\x0b\nNext\tline."

    assert sanitize_revision_output(output) == "Clean text.\nNext\tline."


def test_sanitize_revision_output_removes_wrapping_markdown_fence():
    output = "```text\nClean text.\n```"

    assert sanitize_revision_output(output) == "Clean text."


def test_sanitize_revision_output_rejects_suspiciously_long_output():
    with pytest.raises(OfflineWritingMalformedOutput):
        sanitize_revision_output("x" * 1000, original_text="short")


def test_concurrency_guard_prevents_overlapping_revisions():
    service = OfflineWritingService(
        FakeOfflineWritingProvider(response="Done.", delay_seconds=0.2)
    )
    started = threading.Event()

    def run_first_revision():
        started.set()
        service.revise("First sentence.")

    thread = threading.Thread(target=run_first_revision)
    thread.start()
    started.wait(timeout=1)
    time.sleep(0.05)

    with pytest.raises(OfflineWritingBusy):
        service.revise("Second sentence.")

    thread.join(timeout=1)


def test_no_cloud_fallback_when_model_router_is_broken():
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in __import__("pathlib").Path("src/offline_writing_reviser").rglob("*.py")
    )
    assert "ModelRouter" not in sources
    service = OfflineWritingService(FakeOfflineWritingProvider(response="Offline only."))

    result = service.revise("Offline only")

    assert result.revised_text == "Offline only."


def test_sensitive_text_is_absent_from_logs(caplog):
    secret_text = "My password is swordfish@example.com and code 12345."
    provider = FakeOfflineWritingProvider(response="Revised private sentence.")
    service = OfflineWritingService(provider)

    with caplog.at_level("INFO", logger="offline-writing-reviser"):
        service.revise(secret_text)

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert secret_text not in log_text
    assert "Revised private sentence" not in log_text
    assert "chars=" in log_text


def test_configuration_defaults():
    config = OfflineWritingConfig()

    assert config.enabled is True
    assert config.provider == "ollama_cli"
    assert config.model == "llama3.2:3b"
    assert config.hotkey == "Ctrl+Alt+W"
    assert config.max_characters == 4000


class FakeCapture:
    text = "I has wrote this."


DEFAULT_CAPTURE = object()


class FakeTextAdapter:
    def __init__(self, replacement_succeeds: bool = True, capture=DEFAULT_CAPTURE):
        self.capture_value = FakeCapture() if capture is DEFAULT_CAPTURE else capture
        self.replacement_succeeds = replacement_succeeds
        self.replaced_with = None

    def capture(self):
        return self.capture_value

    def replace(self, capture, replacement: str) -> bool:
        self.replaced_with = replacement
        return self.replacement_succeeds


def test_replacement_success():
    adapter = FakeTextAdapter()
    service = OfflineWritingService(FakeOfflineWritingProvider(response="I wrote this."))
    controller = OfflineWritingController(service=service, text_adapter=adapter)

    controller._run_revision()

    assert adapter.replaced_with == "I wrote this."


def test_replacement_failure_leaves_original_unchanged():
    adapter = FakeTextAdapter(replacement_succeeds=False)
    service = OfflineWritingService(FakeOfflineWritingProvider(response="I wrote this."))
    controller = OfflineWritingController(service=service, text_adapter=adapter)

    controller._run_revision()

    assert adapter.replaced_with == "I wrote this."


def test_empty_capture_does_not_call_provider():
    provider = FakeOfflineWritingProvider(response="Should not run.")
    adapter = FakeTextAdapter(capture=None)
    service = OfflineWritingService(provider)
    controller = OfflineWritingController(service=service, text_adapter=adapter)

    controller._run_revision()

    assert provider.calls == []
    assert adapter.replaced_with is None


def test_hotkey_parser_supports_ctrl_alt_w():
    modifiers, key = parse_hotkey("Ctrl+Alt+W")

    assert modifiers == 0x0003
    assert key == ord("W")


class FakePoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class FakeMessage(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_ulong),
        ("pt", FakePoint),
    ]


class FakeUser32:
    def __init__(self):
        self.registered = []
        self.unregistered = []
        self.messages = [WM_HOTKEY, 0]

    def RegisterHotKey(self, hwnd, identifier, modifiers, key):
        self.registered.append((identifier, modifiers, key))
        return 1

    def UnregisterHotKey(self, hwnd, identifier):
        self.unregistered.append(identifier)
        return 1

    def GetMessageW(self, message_pointer, hwnd, minimum, maximum):
        next_message = self.messages.pop(0)
        if next_message == 0:
            return 0
        message = message_pointer._obj
        message.message = next_message
        message.wParam = 77
        return 1

    def TranslateMessage(self, message_pointer):
        return 1

    def DispatchMessageW(self, message_pointer):
        return 1

    def PostThreadMessageW(self, thread_id, message, wparam, lparam):
        return 1


class FakeKernel32:
    def GetCurrentThreadId(self):
        return 123


class FakeWindll:
    def __init__(self):
        self.user32 = FakeUser32()
        self.kernel32 = FakeKernel32()


def test_hotkey_registration_lifecycle(monkeypatch):
    fake_windll = FakeWindll()
    monkeypatch.setattr("offline_writing_reviser.windows.hotkeys.ctypes.windll", fake_windll)
    callback_calls = []
    manager = WindowsHotkeyManager(
        [
            HotkeyBinding(
                identifier=77,
                shortcut="Ctrl+Alt+W",
                callback=lambda: callback_calls.append("called"),
            )
        ]
    )

    manager.start()
    manager.stop()

    assert fake_windll.user32.registered == [(77, 0x0003, ord("W"))]
    assert fake_windll.user32.unregistered == [77]
    assert callback_calls == ["called"]


def test_provider_error_does_not_replace_selection():
    adapter = FakeTextAdapter()
    service = OfflineWritingService(
        FakeOfflineWritingProvider(error=OfflineWritingProviderError("local failure"))
    )
    controller = OfflineWritingController(service=service, text_adapter=adapter)

    controller._run_revision()

    assert adapter.replaced_with is None


def test_invalid_model_output_does_not_replace_selection():
    adapter = FakeTextAdapter()
    service = OfflineWritingService(FakeOfflineWritingProvider(response="Broken\x00 output."))
    controller = OfflineWritingController(service=service, text_adapter=adapter)

    controller._run_revision()

    assert adapter.replaced_with is None


def test_duplicate_hotkey_press_is_ignored_by_controller_lock():
    provider = FakeOfflineWritingProvider(response="Done.", delay_seconds=0.2)
    adapter = FakeTextAdapter()
    service = OfflineWritingService(provider)
    controller = OfflineWritingController(service=service, text_adapter=adapter)
    first = threading.Thread(target=controller._run_revision)
    second = threading.Thread(target=controller._run_revision)

    first.start()
    time.sleep(0.05)
    second.start()
    first.join(timeout=1)
    second.join(timeout=1)

    assert len(provider.calls) == 1


class FakeClipboard:
    def __init__(self):
        self.snapshot_value = ClipboardSnapshot(tuple())
        self.restore_calls = 0
        self.text = ""
        self.set_values = []
        self.sequence = 1
        self.unicode_available = False

    def snapshot(self):
        return self.snapshot_value

    def clear(self):
        self.text = ""

    def get_sequence_number(self):
        return self.sequence

    def has_unicode_text(self):
        return self.unicode_available

    def get_unicode_text(self):
        return self.text

    def restore(self, snapshot):
        self.restore_calls += 1

    def set_unicode_text(self, text):
        self.set_values.append(text)


def test_clipboard_capture_and_replace_preserves_clipboard(monkeypatch):
    import offline_writing_reviser.windows.text_selection as text_selection

    clipboard = FakeClipboard()
    send_keys = []
    monkeypatch.setattr(text_selection, "get_foreground_window", lambda: 100)
    monkeypatch.setattr(text_selection, "get_window_process_identity", lambda hwnd: (55, "notepad.exe"))
    monkeypatch.setattr(text_selection, "_wait_for_modifier_release", lambda *args, **kwargs: True)

    def fake_send_ctrl_key(vk, logger=None):
        send_keys.append(vk)
        clipboard.text = "Selected text."
        clipboard.unicode_available = True
        clipboard.sequence += 1
        return True

    monkeypatch.setattr(text_selection, "_send_ctrl_key", fake_send_ctrl_key)
    monkeypatch.setattr(text_selection, "_wait_for_foreground_stability", lambda *args, **kwargs: True)
    adapter = WindowsSelectedTextAdapter(clipboard=clipboard, copy_wait_seconds=0.01)

    capture = adapter.capture()
    assert capture is not None
    assert capture.text == "Selected text."
    assert capture.foreground_process == "notepad.exe"

    assert adapter.replace(capture, "Revised text.") is True
    assert clipboard.set_values == ["Revised text."]
    assert clipboard.restore_calls == 2
    assert send_keys


def test_capture_waits_for_hotkey_modifiers_to_release(monkeypatch):
    import offline_writing_reviser.windows.text_selection as text_selection

    states = iter([0x8000, 0x8000, 0])
    sleeps = []

    class FakeUser32:
        def GetAsyncKeyState(self, _vk):
            return next(states, 0)

    class FakeWindll:
        user32 = FakeUser32()

    monkeypatch.setattr(text_selection.ctypes, "windll", FakeWindll())
    monkeypatch.setattr(text_selection.time, "sleep", lambda seconds: sleeps.append(seconds))

    text_selection._wait_for_modifier_release(timeout_seconds=1)

    assert sleeps


def test_capture_fails_when_hotkey_modifiers_still_down(monkeypatch):
    import offline_writing_reviser.windows.text_selection as text_selection

    clipboard = FakeClipboard()
    send_calls = []
    monkeypatch.setattr(text_selection, "get_foreground_window", lambda: 100)
    monkeypatch.setattr(text_selection, "get_window_process_identity", lambda hwnd: (55, "notepad.exe"))
    monkeypatch.setattr(text_selection, "_wait_for_modifier_release", lambda *args, **kwargs: False)
    monkeypatch.setattr(text_selection, "_send_ctrl_key", lambda *args, **kwargs: send_calls.append(args) or True)
    adapter = WindowsSelectedTextAdapter(clipboard=clipboard, copy_wait_seconds=0.01)

    assert adapter.capture() is None
    assert send_calls == []
    assert clipboard.restore_calls == 0


def test_send_input_uses_input_array_pointer_not_array_byref(monkeypatch):
    import offline_writing_reviser.windows.text_selection as text_selection

    calls = []

    class StrictSendInput:
        def __call__(self, count, input_pointer, input_size):
            if not isinstance(input_pointer, ctypes.Array):
                raise ctypes.ArgumentError("expected LPINPUT-compatible array")
            calls.append((count, input_size))
            return count

    class FakeUser32:
        def MapVirtualKeyW(self, vk, _map_type):
            return vk

        SendInput = StrictSendInput()

    class FakeWindll:
        user32 = FakeUser32()

    monkeypatch.setattr(text_selection.ctypes, "windll", FakeWindll())

    assert text_selection._send_ctrl_key(text_selection.VK_C) is True
    assert calls == [(4, ctypes.sizeof(text_selection.INPUT))]


def test_send_input_structures_match_64_bit_windows_layout():
    import offline_writing_reviser.windows.text_selection as text_selection

    assert ctypes.sizeof(text_selection.KEYBDINPUT) == 24
    assert ctypes.sizeof(text_selection.INPUT_UNION) == 32
    assert ctypes.sizeof(text_selection.INPUT) == 40
    assert text_selection.INPUT.union.offset == 8


def test_send_input_returns_false_when_incomplete(monkeypatch):
    import offline_writing_reviser.windows.text_selection as text_selection

    calls = []

    class FakeUser32:
        def MapVirtualKeyW(self, vk, _map_type):
            return vk

        def SendInput(self, count, input_pointer, input_size):
            calls.append((count, input_pointer, input_size))
            return count - 1

    class FakeWindll:
        user32 = FakeUser32()

    monkeypatch.setattr(text_selection.ctypes, "windll", FakeWindll())
    monkeypatch.setattr(text_selection.ctypes, "get_last_error", lambda: 5)

    assert text_selection._send_ctrl_key(text_selection.VK_C) is False
    assert calls[0][0] == 4


def test_send_input_uses_scancode_ctrl_key_sequence(monkeypatch):
    import offline_writing_reviser.windows.text_selection as text_selection

    captured = []

    class FakeUser32:
        def MapVirtualKeyW(self, vk, _map_type):
            return {text_selection.VK_CONTROL: 0x1D, text_selection.VK_C: 0x2E}[vk]

        def SendInput(self, count, input_pointer, input_size):
            for item in input_pointer:
                captured.append(item.union.ki)
            return count

    class FakeWindll:
        user32 = FakeUser32()

    monkeypatch.setattr(text_selection.ctypes, "windll", FakeWindll())

    assert text_selection._send_ctrl_key(text_selection.VK_C) is True
    assert [(item.wVk, item.wScan, item.dwFlags) for item in captured] == [
        (0, 0x1D, text_selection.KEYEVENTF_SCANCODE),
        (0, 0x2E, text_selection.KEYEVENTF_SCANCODE),
        (0, 0x2E, text_selection.KEYEVENTF_SCANCODE | text_selection.KEYEVENTF_KEYUP),
        (0, 0x1D, text_selection.KEYEVENTF_SCANCODE | text_selection.KEYEVENTF_KEYUP),
    ]


def test_capture_waits_for_clipboard_sequence_change(monkeypatch):
    import offline_writing_reviser.windows.text_selection as text_selection

    clipboard = FakeClipboard()
    monkeypatch.setattr(text_selection, "get_foreground_window", lambda: 100)
    monkeypatch.setattr(text_selection, "get_window_process_identity", lambda hwnd: (55, "notepad.exe"))
    monkeypatch.setattr(text_selection, "_wait_for_modifier_release", lambda *args, **kwargs: True)

    def fake_send_ctrl_key(vk, logger=None):
        clipboard.text = "Copied after sequence."
        clipboard.unicode_available = True
        clipboard.sequence += 1
        return True

    monkeypatch.setattr(text_selection, "_send_ctrl_key", fake_send_ctrl_key)
    adapter = WindowsSelectedTextAdapter(clipboard=clipboard, copy_wait_seconds=0.01)

    capture = adapter.capture()

    assert capture is not None
    assert capture.text == "Copied after sequence."
    assert clipboard.restore_calls == 1


def test_capture_times_out_without_clipboard_sequence_change(monkeypatch):
    import offline_writing_reviser.windows.text_selection as text_selection

    clipboard = FakeClipboard()
    clipboard.text = "Old clipboard."
    clipboard.unicode_available = True
    monkeypatch.setattr(text_selection, "get_foreground_window", lambda: 100)
    monkeypatch.setattr(text_selection, "get_window_process_identity", lambda hwnd: (55, "notepad.exe"))
    monkeypatch.setattr(text_selection, "_wait_for_modifier_release", lambda *args, **kwargs: True)
    monkeypatch.setattr(text_selection, "_send_ctrl_key", lambda *args, **kwargs: True)
    adapter = WindowsSelectedTextAdapter(clipboard=clipboard, copy_wait_seconds=0.01)

    assert adapter.capture() is None
    assert clipboard.restore_calls == 1


def test_capture_supports_unicode_after_sequence_change(monkeypatch):
    import offline_writing_reviser.windows.text_selection as text_selection

    clipboard = FakeClipboard()
    monkeypatch.setattr(text_selection, "get_foreground_window", lambda: 100)
    monkeypatch.setattr(text_selection, "get_window_process_identity", lambda hwnd: (55, "notepad.exe"))
    monkeypatch.setattr(text_selection, "_wait_for_modifier_release", lambda *args, **kwargs: True)

    def fake_send_ctrl_key(vk, logger=None):
        clipboard.text = "Café résumé - José paid €5."
        clipboard.unicode_available = True
        clipboard.sequence += 1
        return True

    monkeypatch.setattr(text_selection, "_send_ctrl_key", fake_send_ctrl_key)
    adapter = WindowsSelectedTextAdapter(clipboard=clipboard, copy_wait_seconds=0.01)

    capture = adapter.capture()

    assert capture is not None
    assert capture.text == "Café résumé - José paid €5."


class FakeWin32Function:
    def __init__(self, implementation):
        self.implementation = implementation
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.implementation(*args)


def _handle_value(handle):
    return handle.value if hasattr(handle, "value") else int(handle)


def test_clipboard_unicode_read_write_uses_pointer_safe_handles(monkeypatch):
    import offline_writing_reviser.windows.text_selection as text_selection

    class FakeKernel32:
        def __init__(self):
            self.buffers = {}
            self.next_handle = 0x100000001
            self.set_clipboard_handle = None
            self.GlobalAlloc = FakeWin32Function(self._global_alloc)
            self.GlobalLock = FakeWin32Function(self._global_lock)
            self.GlobalUnlock = FakeWin32Function(lambda handle: 1)
            self.GlobalSize = FakeWin32Function(self._global_size)
            self.GlobalFree = FakeWin32Function(lambda handle: None)
            self.OpenProcess = FakeWin32Function(lambda *_args: None)
            self.CloseHandle = FakeWin32Function(lambda _handle: 1)
            self.QueryFullProcessImageNameW = FakeWin32Function(lambda *_args: 0)

        def add_unicode_clipboard_text(self, text):
            data = (text + "\0").encode("utf-16-le")
            handle = self.next_handle
            self.next_handle += 1
            buffer = ctypes.create_string_buffer(data)
            self.buffers[handle] = buffer
            return ctypes.c_void_p(handle)

        def _global_alloc(self, _flags, size):
            handle = self.next_handle
            self.next_handle += 1
            self.buffers[handle] = ctypes.create_string_buffer(size)
            return ctypes.c_void_p(handle)

        def _global_lock(self, handle):
            buffer = self.buffers[_handle_value(handle)]
            return ctypes.c_void_p(ctypes.addressof(buffer))

        def _global_size(self, handle):
            return ctypes.sizeof(self.buffers[_handle_value(handle)])

    class FakeUser32:
        def __init__(self, kernel32):
            self.kernel32 = kernel32
            self.current_text_handle = kernel32.add_unicode_clipboard_text("Copied.")
            self.OpenClipboard = FakeWin32Function(lambda _hwnd: 1)
            self.CloseClipboard = FakeWin32Function(lambda: 1)
            self.EmptyClipboard = FakeWin32Function(lambda: 1)
            self.EnumClipboardFormats = FakeWin32Function(lambda _format_id: 0)
            self.GetClipboardData = FakeWin32Function(self._get_clipboard_data)
            self.SetClipboardData = FakeWin32Function(self._set_clipboard_data)
            self.GetForegroundWindow = FakeWin32Function(lambda: None)
            self.GetWindowThreadProcessId = FakeWin32Function(lambda *_args: 0)
            self.GetAsyncKeyState = FakeWin32Function(lambda _vk: 0)
            self.MapVirtualKeyW = FakeWin32Function(lambda vk, _map_type: vk)
            self.SendInput = FakeWin32Function(lambda count, _inputs, _size: count)
            self.GetClipboardSequenceNumber = FakeWin32Function(lambda: 1)
            self.IsClipboardFormatAvailable = FakeWin32Function(lambda _format_id: 1)

        def _get_clipboard_data(self, format_id):
            if int(format_id) == text_selection.CF_UNICODETEXT:
                return self.current_text_handle
            return None

        def _set_clipboard_data(self, format_id, handle):
            assert int(format_id) == text_selection.CF_UNICODETEXT
            self.kernel32.set_clipboard_handle = handle
            return handle

    class FakeWindll:
        def __init__(self):
            self.kernel32 = FakeKernel32()
            self.user32 = FakeUser32(self.kernel32)

    fake_windll = FakeWindll()
    monkeypatch.setattr(text_selection.ctypes, "windll", fake_windll)
    clipboard = WindowsClipboard(retry_timeout_seconds=0.01)

    assert clipboard.get_unicode_text() == "Copied."

    clipboard.set_unicode_text("Revised.")
    handle = fake_windll.kernel32.set_clipboard_handle
    assert handle is not None
    pointer = fake_windll.kernel32.GlobalLock(handle)
    assert ctypes.string_at(pointer, len("Revised.\0".encode("utf-16-le"))) == (
        "Revised.\0".encode("utf-16-le")
    )


def test_clipboard_restore_preserves_non_text_format_with_pointer_safe_handles(monkeypatch):
    import offline_writing_reviser.windows.text_selection as text_selection

    class FakeKernel32:
        def __init__(self):
            self.buffers = {}
            self.next_handle = 0x200000001
            self.restored = {}
            self.GlobalAlloc = FakeWin32Function(self._global_alloc)
            self.GlobalLock = FakeWin32Function(self._global_lock)
            self.GlobalUnlock = FakeWin32Function(lambda handle: 1)
            self.GlobalSize = FakeWin32Function(lambda handle: ctypes.sizeof(self.buffers[_handle_value(handle)]))
            self.GlobalFree = FakeWin32Function(lambda handle: None)
            self.OpenProcess = FakeWin32Function(lambda *_args: None)
            self.CloseHandle = FakeWin32Function(lambda _handle: 1)
            self.QueryFullProcessImageNameW = FakeWin32Function(lambda *_args: 0)

        def _global_alloc(self, _flags, size):
            handle = self.next_handle
            self.next_handle += 1
            self.buffers[handle] = ctypes.create_string_buffer(size)
            return ctypes.c_void_p(handle)

        def _global_lock(self, handle):
            return ctypes.c_void_p(ctypes.addressof(self.buffers[_handle_value(handle)]))

    class FakeUser32:
        def __init__(self, kernel32):
            self.kernel32 = kernel32
            self.OpenClipboard = FakeWin32Function(lambda _hwnd: 1)
            self.CloseClipboard = FakeWin32Function(lambda: 1)
            self.EmptyClipboard = FakeWin32Function(lambda: 1)
            self.EnumClipboardFormats = FakeWin32Function(lambda _format_id: 0)
            self.GetClipboardData = FakeWin32Function(lambda _format_id: None)
            self.SetClipboardData = FakeWin32Function(self._set_clipboard_data)
            self.GetForegroundWindow = FakeWin32Function(lambda: None)
            self.GetWindowThreadProcessId = FakeWin32Function(lambda *_args: 0)
            self.GetAsyncKeyState = FakeWin32Function(lambda _vk: 0)
            self.MapVirtualKeyW = FakeWin32Function(lambda vk, _map_type: vk)
            self.SendInput = FakeWin32Function(lambda count, _inputs, _size: count)
            self.GetClipboardSequenceNumber = FakeWin32Function(lambda: 1)
            self.IsClipboardFormatAvailable = FakeWin32Function(lambda _format_id: 1)

        def _set_clipboard_data(self, format_id, handle):
            self.kernel32.restored[int(format_id)] = ctypes.string_at(
                self.kernel32.GlobalLock(handle),
                self.kernel32.GlobalSize(handle),
            )
            return handle

    class FakeWindll:
        def __init__(self):
            self.kernel32 = FakeKernel32()
            self.user32 = FakeUser32(self.kernel32)

    fake_windll = FakeWindll()
    monkeypatch.setattr(text_selection.ctypes, "windll", fake_windll)
    clipboard = WindowsClipboard(retry_timeout_seconds=0.01)
    snapshot = ClipboardSnapshot(
        (ClipboardFormatData(format_id=49161, data=b"custom-format-bytes"),)
    )

    clipboard.restore(snapshot)

    assert fake_windll.kernel32.restored[49161] == b"custom-format-bytes"


def test_focus_change_before_replacement_leaves_clipboard_unchanged(monkeypatch):
    import offline_writing_reviser.windows.text_selection as text_selection

    clipboard = FakeClipboard()
    monkeypatch.setattr(text_selection, "get_foreground_window", lambda: 200)
    adapter = WindowsSelectedTextAdapter(clipboard=clipboard)
    capture = SelectedTextCapture(
        text="Original.",
        foreground_window=100,
        foreground_pid=55,
        foreground_process="notepad.exe",
        clipboard_snapshot=clipboard.snapshot_value,
    )

    assert adapter.replace(capture, "Revised.") is False
    assert clipboard.set_values == []
    assert clipboard.restore_calls == 0


def test_ollama_cli_request(monkeypatch):
    import offline_writing_reviser.providers.ollama as ollama_cli

    calls = []

    def fake_which(executable):
        assert executable == "ollama"
        return "C:\\Ollama\\ollama.exe"

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[1] == "list":
            return subprocess.CompletedProcess(args, 0, stdout="NAME ID SIZE MODIFIED\nllama3.2:3b abc 2 GB now\n", stderr="")
        assert args[:3] == ["C:\\Ollama\\ollama.exe", "run", "llama3.2:3b"]
        assert "Text to revise:\nBad." in args[3]
        return subprocess.CompletedProcess(args, 0, stdout="Fixed.", stderr="")

    monkeypatch.setattr(ollama_cli.shutil, "which", fake_which)
    monkeypatch.setattr(ollama_cli.subprocess, "run", fake_run)
    provider = ollama_cli.OllamaCliOfflineWritingProvider(model="llama3.2:3b")

    assert provider.is_available() is True
    assert provider.revise("Bad.", "Fix.", timeout_seconds=1) == "Fixed."
    assert calls[0][0][1] == "list"
    assert calls[-1][0][1] == "run"


def test_ollama_unavailable_when_executable_missing(monkeypatch):
    import offline_writing_reviser.providers.ollama as ollama_cli

    monkeypatch.setattr(ollama_cli.shutil, "which", lambda _executable: None)
    monkeypatch.setattr(ollama_cli, "_default_windows_ollama_paths", lambda: [])
    provider = ollama_cli.OllamaCliOfflineWritingProvider(model="llama3.2:3b")

    assert provider.is_available() is False
    with pytest.raises(OfflineWritingProviderUnavailable):
        provider.revise("Bad.", "Fix.", timeout_seconds=1)


def test_ollama_model_missing_fails_locally(monkeypatch):
    import offline_writing_reviser.providers.ollama as ollama_cli

    monkeypatch.setattr(ollama_cli.shutil, "which", lambda _executable: "ollama")
    monkeypatch.setattr(
        ollama_cli.subprocess,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args,
            0,
            stdout="NAME ID SIZE MODIFIED\nother:latest abc 2 GB now\n",
            stderr="",
        ),
    )
    provider = ollama_cli.OllamaCliOfflineWritingProvider(model="llama3.2:3b")

    assert provider.is_available() is False
    with pytest.raises(OfflineWritingModelMissing):
        provider.revise("Bad.", "Fix.", timeout_seconds=1)


def test_ollama_timeout_fails_locally(monkeypatch):
    import offline_writing_reviser.providers.ollama as ollama_cli

    monkeypatch.setattr(ollama_cli.shutil, "which", lambda _executable: "ollama")

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ollama", timeout=1)

    monkeypatch.setattr(ollama_cli.subprocess, "run", fake_run)
    provider = ollama_cli.OllamaCliOfflineWritingProvider(model="llama3.2:3b")

    with pytest.raises(OfflineWritingProviderTimeout):
        provider.ensure_model_available(timeout_seconds=1)


def test_ollama_run_failure_is_provider_error(monkeypatch):
    import offline_writing_reviser.providers.ollama as ollama_cli

    calls = []
    monkeypatch.setattr(ollama_cli.shutil, "which", lambda _executable: "ollama")

    def fake_run(args, **_kwargs):
        calls.append(args)
        if args[1] == "list":
            return subprocess.CompletedProcess(args, 0, stdout="NAME\nllama3.2:3b abc\n", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="failed")

    monkeypatch.setattr(ollama_cli.subprocess, "run", fake_run)
    provider = ollama_cli.OllamaCliOfflineWritingProvider(model="llama3.2:3b")

    with pytest.raises(OfflineWritingProviderError):
        provider.revise("Bad.", "Fix.", timeout_seconds=1)


def test_single_instance_detects_existing_mutex(monkeypatch):
    class FakeKernel32:
        def CreateMutexW(self, *_args):
            return 123

        def GetLastError(self):
            return 183

        def CloseHandle(self, _handle):
            return 1

    class FakeWindll:
        kernel32 = FakeKernel32()

    monkeypatch.setattr("offline_writing_reviser.windows.single_instance.ctypes.windll", FakeWindll())

    instance = WindowsSingleInstance("Local\\Test")

    assert instance.acquire() is False
    instance.release()


def test_headless_entry_point_initializes_without_chat_stack(monkeypatch, tmp_path):
    import offline_writing_reviser.application as writing_main
    from offline_writing_reviser.config import OfflineWritingConfig

    started = []

    class FakeRuntime:
        has_registered_hotkeys = True

        def stop(self):
            started.append("stopped")

    class FakeInstance:
        def acquire(self):
            return True

        def release(self):
            started.append("released")

    monkeypatch.setattr(writing_main.sys, "platform", "win32")
    monkeypatch.setattr(writing_main, "start_offline_writing_runtime", lambda config, logger=None: FakeRuntime())
    stop_event = threading.Event()
    stop_event.set()
    app = writing_main.OfflineWritingReviserApplication(
        config=OfflineWritingConfig(log_file=tmp_path / "writing-reviser.log"),
        stop_event=stop_event,
        instance=FakeInstance(),
    )

    assert app.run() == 0
    assert started == ["stopped", "released"]


def test_normal_startup_enters_application_loop(monkeypatch, tmp_path):
    import offline_writing_reviser.application as writing_main
    from offline_writing_reviser.config import OfflineWritingConfig

    stopped = []
    released = []

    class FakeRuntime:
        has_registered_hotkeys = True

        def stop(self):
            stopped.append(True)

    class FakeInstance:
        def acquire(self):
            return True

        def release(self):
            released.append(True)

    monkeypatch.setattr(writing_main.sys, "platform", "win32")
    monkeypatch.setattr(writing_main, "_install_shutdown_handlers", lambda _event: None)
    monkeypatch.setattr(writing_main, "start_offline_writing_runtime", lambda config, logger=None: FakeRuntime())

    stop_event = threading.Event()
    result = []
    app = writing_main.OfflineWritingReviserApplication(
        config=OfflineWritingConfig(log_file=tmp_path / "writing-reviser.log"),
        stop_event=stop_event,
        instance=FakeInstance(),
    )
    thread = threading.Thread(target=lambda: result.append(app.run()))

    thread.start()
    time.sleep(0.05)

    assert thread.is_alive()
    assert result == []

    stop_event.set()
    thread.join(timeout=1)

    assert result == [0]
    assert stopped == [True]
    assert released == [True]


def test_validate_startup_entrypoint_is_non_blocking(monkeypatch):
    import offline_writing_reviser.__main__ as main_module

    calls = []
    monkeypatch.setattr(main_module, "validate_startup", lambda: calls.append("validated") or 0)

    assert main_module.main(["--validate-startup"]) == 0
    assert calls == ["validated"]


def test_startup_failure_is_logged_and_surfaced(monkeypatch, tmp_path, capsys):
    import offline_writing_reviser.application as writing_main
    from offline_writing_reviser.config import OfflineWritingConfig

    released = []

    class FakeRuntime:
        has_registered_hotkeys = False

        def stop(self):
            pass

    class FakeInstance:
        def acquire(self):
            return True

        def release(self):
            released.append(True)

    log_file = tmp_path / "writing-reviser.log"
    monkeypatch.setattr(writing_main.sys, "platform", "win32")
    monkeypatch.setattr(writing_main, "start_offline_writing_runtime", lambda config, logger=None: FakeRuntime())
    app = writing_main.OfflineWritingReviserApplication(
        config=OfflineWritingConfig(log_file=log_file),
        instance=FakeInstance(),
    )

    assert app.run() == 1

    captured = capsys.readouterr()
    assert "could not register Ctrl+Alt+W" in captured.err
    assert "hotkey_unavailable" in log_file.read_text(encoding="utf-8")
    assert released == [True]


def test_duplicate_instance_is_surfaced(capsys):
    import offline_writing_reviser.application as writing_main
    from offline_writing_reviser.config import OfflineWritingConfig

    class FakeInstance:
        def acquire(self):
            return False

        def release(self):
            raise AssertionError("duplicate instance should not release unowned mutex")

    app = writing_main.OfflineWritingReviserApplication(
        config=OfflineWritingConfig(),
        instance=FakeInstance(),
    )

    assert app.run() == 0
    assert "already running" in capsys.readouterr().err


def test_headless_entry_source_does_not_import_chat_or_ui_stack():
    source = __import__("pathlib").Path("src/offline_writing_reviser/application.py").read_text(encoding="utf-8")

    assert "desktop.lifecycle" not in source
    assert "app.main" not in source
    assert "webview" not in source
    assert "ModelRouter" not in source
