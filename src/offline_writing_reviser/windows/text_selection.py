from __future__ import annotations

import ctypes
import logging
import uuid
from pathlib import Path
import time
from dataclasses import dataclass
from ctypes import wintypes
from enum import Enum


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
INPUT_KEYBOARD = 1
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_C = 0x43
VK_V = 0x56
VK_P = 0x50
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C
MAPVK_VK_TO_VSC = 0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WM_COPY = 0x0301
WM_PASTE = 0x0302
EM_GETSEL = 0x00B0
SMTO_ABORTIFHUNG = 0x0002

HCURSOR = wintypes.HANDLE
HGLOBAL = wintypes.HANDLE
LRESULT = ctypes.c_ssize_t
ULONG_PTR = wintypes.WPARAM


class CaptureState(str, Enum):
    IDLE = "IDLE"
    HOTKEY_RECEIVED = "HOTKEY_RECEIVED"
    TARGET_CAPTURED = "TARGET_CAPTURED"
    WAITING_FOR_MODIFIER_RELEASE = "WAITING_FOR_MODIFIER_RELEASE"
    CLIPBOARD_SNAPSHOTTED = "CLIPBOARD_SNAPSHOTTED"
    COPY_SENT = "COPY_SENT"
    WAITING_FOR_CLIPBOARD_CHANGE = "WAITING_FOR_CLIPBOARD_CHANGE"
    TEXT_CAPTURED = "TEXT_CAPTURED"
    PROCESSING = "PROCESSING"
    TARGET_REFOCUSED = "TARGET_REFOCUSED"
    PASTE_SENT = "PASTE_SENT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CaptureFailure(str, Enum):
    NO_SELECTION = "no_selection"
    MODIFIER_RELEASE_TIMEOUT = "modifier_release_timeout"
    CLIPBOARD_BUSY = "clipboard_busy"
    COPY_SEND_FAILED = "copy_send_failed"
    COPY_TIMEOUT = "copy_timeout"
    FOREGROUND_CHANGED = "foreground_changed"
    TARGET_UNAVAILABLE = "target_unavailable"
    UNSUPPORTED_TARGET = "unsupported_target"
    PASTE_FAILED = "paste_failed"


class SelectionCaptureError(RuntimeError):
    def __init__(self, failure: CaptureFailure, state: CaptureState):
        self.failure = failure
        self.state = state
        super().__init__(failure.value)


class ClipboardBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class SelectionTarget:
    foreground_window: int
    focused_window: int
    foreground_pid: int
    foreground_process: str
    mode: str
    action_key: int
    operation_id: str


@dataclass
class OperationTelemetry:
    operation_id: str
    mode: str
    process: str
    foreground_window: int
    focused_window: int
    foreground_before_copy: int | None = None
    foreground_before_paste: int | None = None
    state: CaptureState = CaptureState.IDLE
    clipboard_sequence_before: int | None = None
    clipboard_sequence_after: int | None = None
    modifier_release_ms: float | None = None
    copy_send_success: bool = False
    clipboard_wait_ms: float | None = None
    captured_character_count: int = 0
    processing_ms: float | None = None
    paste_success: bool = False
    failure_code: str = ""


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


@dataclass(frozen=True)
class ClipboardFormatData:
    format_id: int
    data: bytes


@dataclass(frozen=True)
class ClipboardSnapshot:
    formats: tuple[ClipboardFormatData, ...]


@dataclass(frozen=True)
class SelectedTextCapture:
    text: str
    foreground_window: int
    foreground_pid: int
    foreground_process: str
    clipboard_snapshot: ClipboardSnapshot
    focused_window: int = 0
    operation: OperationTelemetry | None = None


class WindowsClipboard:
    def __init__(self, retry_timeout_seconds: float = 0.5) -> None:
        _configure_clipboard_ctypes()
        self.retry_timeout_seconds = retry_timeout_seconds

    def snapshot(self) -> ClipboardSnapshot:
        formats: list[ClipboardFormatData] = []
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not self._open_clipboard():
            raise ClipboardBusyError("clipboard_snapshot")
        try:
            format_id = 0
            while True:
                format_id = user32.EnumClipboardFormats(format_id)
                if not format_id:
                    break
                handle = user32.GetClipboardData(wintypes.UINT(format_id))
                if not handle:
                    continue
                size = int(kernel32.GlobalSize(handle))
                pointer = kernel32.GlobalLock(handle)
                if not pointer or size <= 0:
                    if pointer:
                        kernel32.GlobalUnlock(handle)
                    continue
                try:
                    formats.append(
                        ClipboardFormatData(
                            format_id=format_id,
                            data=ctypes.string_at(pointer, size),
                        )
                    )
                finally:
                    kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
        return ClipboardSnapshot(tuple(formats))

    def restore(self, snapshot: ClipboardSnapshot) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not self._open_clipboard():
            raise ClipboardBusyError("clipboard_restore")
        try:
            user32.EmptyClipboard()
            for item in snapshot.formats:
                handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(item.data))
                if not handle:
                    continue
                pointer = kernel32.GlobalLock(handle)
                if not pointer:
                    kernel32.GlobalFree(handle)
                    continue
                ctypes.memmove(_void_pointer(pointer), item.data, len(item.data))
                kernel32.GlobalUnlock(handle)
                if not user32.SetClipboardData(item.format_id, handle):
                    kernel32.GlobalFree(handle)
        finally:
            user32.CloseClipboard()

    def clear(self) -> None:
        user32 = ctypes.windll.user32
        if not self._open_clipboard():
            raise ClipboardBusyError("clipboard_clear")
        try:
            user32.EmptyClipboard()
        finally:
            user32.CloseClipboard()

    def get_sequence_number(self) -> int:
        return int(ctypes.windll.user32.GetClipboardSequenceNumber())

    def has_unicode_text(self) -> bool:
        return bool(ctypes.windll.user32.IsClipboardFormatAvailable(CF_UNICODETEXT))

    def get_unicode_text(self) -> str:
        user32 = ctypes.windll.user32
        if not self._open_clipboard():
            raise ClipboardBusyError("clipboard_read")
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = ctypes.windll.kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.cast(pointer, wintypes.LPWSTR).value or ""
            finally:
                ctypes.windll.kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def set_unicode_text(self, text: str) -> None:
        encoded = (text + "\0").encode("utf-16-le")
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not self._open_clipboard():
            raise ClipboardBusyError("clipboard_write")
        try:
            user32.EmptyClipboard()
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
            if not handle:
                return
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                kernel32.GlobalFree(handle)
                return
            ctypes.memmove(_void_pointer(pointer), encoded, len(encoded))
            kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)
        finally:
            user32.CloseClipboard()

    def _open_clipboard(self) -> bool:
        deadline = time.perf_counter() + self.retry_timeout_seconds
        while True:
            if ctypes.windll.user32.OpenClipboard(None):
                return True
            if time.perf_counter() >= deadline:
                return False
            time.sleep(0.01)


class WindowsSelectedTextAdapter:
    def __init__(
        self,
        clipboard: WindowsClipboard | None = None,
        copy_wait_seconds: float = 0.75,
        paste_restore_delay_seconds: float = 0.35,
        word_paste_restore_delay_seconds: float = 1.5,
        modifier_release_wait_seconds: float = 2.0,
        logger: logging.Logger | None = None,
    ):
        self.clipboard = clipboard or WindowsClipboard()
        self.copy_wait_seconds = copy_wait_seconds
        self.paste_restore_delay_seconds = paste_restore_delay_seconds
        self.word_paste_restore_delay_seconds = word_paste_restore_delay_seconds
        self.modifier_release_wait_seconds = modifier_release_wait_seconds
        self.logger = logger or logging.getLogger("offline-writing-reviser")

    def capture_target(self, mode: str = "revision") -> SelectionTarget:
        foreground = get_foreground_window()
        if not foreground:
            raise SelectionCaptureError(
                CaptureFailure.TARGET_UNAVAILABLE, CaptureState.HOTKEY_RECEIVED
            )
        pid, process_name = get_window_process_identity(foreground)
        focused = get_focused_window(foreground) or foreground
        target = SelectionTarget(
            foreground_window=foreground,
            focused_window=focused,
            foreground_pid=pid,
            foreground_process=process_name,
            mode=mode,
            action_key=VK_P,
            operation_id=uuid.uuid4().hex[:12],
        )
        self.logger.info(
            "Selection state=%s operation=%s mode=%s hwnd=%s",
            CaptureState.HOTKEY_RECEIVED.value,
            target.operation_id,
            mode,
            foreground,
        )
        self.logger.info(
            "Selection state=%s operation=%s mode=%s process=%s hwnd=%s "
            "focused_hwnd=%s",
            CaptureState.TARGET_CAPTURED.value,
            target.operation_id,
            mode,
            process_name,
            foreground,
            focused,
        )
        return target

    def capture(
        self,
        target: SelectionTarget | None = None,
        mode: str = "revision",
    ) -> SelectedTextCapture | None:
        legacy_call = target is None
        target = target or self.capture_target(mode)
        telemetry = OperationTelemetry(
            operation_id=target.operation_id,
            mode=target.mode,
            process=target.foreground_process,
            foreground_window=target.foreground_window,
            focused_window=target.focused_window,
            state=CaptureState.HOTKEY_RECEIVED,
        )
        self._transition(telemetry, CaptureState.WAITING_FOR_MODIFIER_RELEASE)
        snapshot: ClipboardSnapshot | None = None
        modifier_started = time.perf_counter()
        try:
            if not _wait_for_modifier_release(
                timeout_seconds=self.modifier_release_wait_seconds,
                action_key=target.action_key,
                logger=self.logger,
            ):
                telemetry.modifier_release_ms = (
                    time.perf_counter() - modifier_started
                ) * 1000
                self._fail(
                    telemetry, CaptureFailure.MODIFIER_RELEASE_TIMEOUT
                )
            telemetry.modifier_release_ms = (
                time.perf_counter() - modifier_started
            ) * 1000
            telemetry.foreground_before_copy = get_foreground_window()
            if telemetry.foreground_before_copy != target.foreground_window:
                self._fail(telemetry, CaptureFailure.FOREGROUND_CHANGED)

            snapshot = self.clipboard.snapshot()
            self._transition(telemetry, CaptureState.CLIPBOARD_SNAPSHOTTED)
            self.clipboard.clear()
            sequence_before = self.clipboard.get_sequence_number()
            telemetry.clipboard_sequence_before = sequence_before

            standard_control = is_standard_edit_control(target.focused_window)
            if standard_control and not control_has_selection(target.focused_window):
                self._fail(telemetry, CaptureFailure.NO_SELECTION)
            wait_started = time.perf_counter()
            text = ""
            for attempt in range(1, 3):
                copy_sent = (
                    send_control_message(target.focused_window, WM_COPY)
                    if standard_control
                    else _send_ctrl_key(VK_C, logger=self.logger)
                )
                telemetry.copy_send_success = copy_sent
                self._transition(telemetry, CaptureState.COPY_SENT)
                if not copy_sent:
                    if attempt == 2:
                        self._fail(
                            telemetry, CaptureFailure.COPY_SEND_FAILED
                        )
                    continue
                self._transition(
                    telemetry, CaptureState.WAITING_FOR_CLIPBOARD_CHANGE
                )
                text = self._wait_for_clipboard_sequence_change(
                    sequence_before
                )
                if text:
                    break
                if get_foreground_window() != target.foreground_window:
                    self._fail(
                        telemetry, CaptureFailure.FOREGROUND_CHANGED
                    )
                self.logger.info(
                    "Selection copy retry operation=%s attempt=%s",
                    telemetry.operation_id,
                    attempt + 1,
                )
            telemetry.clipboard_wait_ms = (
                time.perf_counter() - wait_started
            ) * 1000
            telemetry.clipboard_sequence_after = (
                self.clipboard.get_sequence_number()
            )
            if not text:
                failure = (
                    CaptureFailure.COPY_TIMEOUT
                    if standard_control
                    else CaptureFailure.COPY_TIMEOUT
                )
                self._fail(telemetry, failure)
            telemetry.captured_character_count = len(text)
            self._transition(telemetry, CaptureState.TEXT_CAPTURED)
            self.clipboard.restore(snapshot)
        except ClipboardBusyError:
            if snapshot is not None:
                try:
                    self.clipboard.restore(snapshot)
                except ClipboardBusyError:
                    pass
            self._fail(telemetry, CaptureFailure.CLIPBOARD_BUSY)
        except SelectionCaptureError:
            if snapshot is not None:
                try:
                    self.clipboard.restore(snapshot)
                except ClipboardBusyError:
                    pass
            if legacy_call:
                return None
            raise
        except Exception:
            self.logger.exception(
                "Selection operation=%s failed exception_type=win32",
                telemetry.operation_id,
            )
            if snapshot is not None:
                try:
                    self.clipboard.restore(snapshot)
                except ClipboardBusyError:
                    pass
            self._fail(telemetry, CaptureFailure.COPY_SEND_FAILED)
        if not text or not text.strip():
            self._fail(telemetry, CaptureFailure.NO_SELECTION)
        return SelectedTextCapture(
            text=text,
            foreground_window=target.foreground_window,
            foreground_pid=target.foreground_pid,
            foreground_process=target.foreground_process,
            clipboard_snapshot=snapshot,
            focused_window=target.focused_window,
            operation=telemetry,
        )

    def replace(self, capture: SelectedTextCapture, replacement: str) -> bool:
        telemetry = capture.operation
        current_foreground = get_foreground_window()
        if current_foreground != capture.foreground_window:
            restore_foreground_window(capture.foreground_window)
            current_foreground = get_foreground_window()
        if telemetry:
            telemetry.foreground_before_paste = current_foreground
        if current_foreground != capture.foreground_window:
            if telemetry:
                self._fail(
                    telemetry,
                    CaptureFailure.FOREGROUND_CHANGED,
                    raise_error=False,
                )
            return False
        if telemetry:
            self._transition(telemetry, CaptureState.TARGET_REFOCUSED)
        try:
            current_snapshot = self.clipboard.snapshot()
            self.clipboard.set_unicode_text(replacement)
            replacement_sequence = self.clipboard.get_sequence_number()
        except ClipboardBusyError:
            if telemetry:
                self._fail(
                    telemetry,
                    CaptureFailure.CLIPBOARD_BUSY,
                    raise_error=False,
                )
            return False
        try:
            paste_sent = (
                send_control_message(capture.focused_window, WM_PASTE)
                if is_standard_edit_control(capture.focused_window)
                else _send_ctrl_key(VK_V, logger=self.logger)
            )
            if telemetry:
                telemetry.paste_success = paste_sent
                self._transition(telemetry, CaptureState.PASTE_SENT)
            if not paste_sent:
                if telemetry:
                    self._fail(
                        telemetry,
                        CaptureFailure.PASTE_FAILED,
                        raise_error=False,
                    )
                return False
            _wait_for_foreground_stability(
                capture.foreground_window,
                timeout_seconds=(
                    self.word_paste_restore_delay_seconds
                    if capture.foreground_process.casefold() == "winword.exe"
                    else self.paste_restore_delay_seconds
                ),
            )
            if telemetry:
                telemetry.paste_success = True
                self._transition(telemetry, CaptureState.COMPLETED)
                self._log_summary(telemetry)
            return True
        finally:
            if self.clipboard.get_sequence_number() == replacement_sequence:
                try:
                    self.clipboard.restore(current_snapshot)
                except ClipboardBusyError:
                    self.logger.warning(
                        "Clipboard restore failed operation=%s category=busy",
                        telemetry.operation_id if telemetry else "unknown",
                    )
            else:
                self.logger.info(
                    "Clipboard restore skipped operation=%s "
                    "category=external_change",
                    telemetry.operation_id if telemetry else "unknown",
                )

    def mark_processing(
        self, capture: SelectedTextCapture, duration_ms: float | None = None
    ) -> None:
        if capture.operation:
            if duration_ms is None:
                self._transition(capture.operation, CaptureState.PROCESSING)
            else:
                capture.operation.processing_ms = duration_ms

    def complete_without_replacement(self, capture: SelectedTextCapture) -> None:
        if capture.operation:
            self._transition(capture.operation, CaptureState.COMPLETED)
            self._log_summary(capture.operation)

    def _transition(
        self, telemetry: OperationTelemetry, state: CaptureState
    ) -> None:
        telemetry.state = state
        self.logger.info(
            "Selection state=%s operation=%s mode=%s hwnd=%s",
            state.value,
            telemetry.operation_id,
            telemetry.mode,
            telemetry.foreground_window,
        )

    def _fail(
        self,
        telemetry: OperationTelemetry,
        failure: CaptureFailure,
        *,
        raise_error: bool = True,
    ) -> None:
        telemetry.state = CaptureState.FAILED
        telemetry.failure_code = failure.value
        self._log_summary(telemetry)
        if raise_error:
            raise SelectionCaptureError(failure, CaptureState.FAILED)

    def _log_summary(self, telemetry: OperationTelemetry) -> None:
        self.logger.info(
            "Selection operation=%s mode=%s process=%s hwnd=%s "
            "focused_hwnd=%s state=%s sequence_before=%s sequence_after=%s "
            "foreground_before_copy=%s foreground_before_paste=%s "
            "modifier_release_ms=%s copy_send_success=%s clipboard_wait_ms=%s "
            "captured_chars=%s processing_ms=%s paste_success=%s failure_code=%s",
            telemetry.operation_id,
            telemetry.mode,
            telemetry.process,
            telemetry.foreground_window,
            telemetry.focused_window,
            telemetry.state.value,
            telemetry.clipboard_sequence_before,
            telemetry.clipboard_sequence_after,
            telemetry.foreground_before_copy,
            telemetry.foreground_before_paste,
            _rounded(telemetry.modifier_release_ms),
            telemetry.copy_send_success,
            _rounded(telemetry.clipboard_wait_ms),
            telemetry.captured_character_count,
            _rounded(telemetry.processing_ms),
            telemetry.paste_success,
            telemetry.failure_code or "none",
        )

    def _wait_for_clipboard_text(self) -> str:
        deadline = time.perf_counter() + self.copy_wait_seconds
        while time.perf_counter() < deadline:
            text = self.clipboard.get_unicode_text()
            if text:
                return text
            time.sleep(0.03)
        return self.clipboard.get_unicode_text()

    def _wait_for_clipboard_sequence_change(self, sequence_before: int) -> str:
        deadline = time.perf_counter() + self.copy_wait_seconds
        sequence_after = sequence_before
        while time.perf_counter() < deadline:
            sequence_after = self.clipboard.get_sequence_number()
            if sequence_after != sequence_before and self.clipboard.has_unicode_text():
                text = self.clipboard.get_unicode_text()
                self.logger.info(
                    "Offline writing clipboard sequence changed before=%s after=%s",
                    sequence_before,
                    sequence_after,
                )
                if text:
                    self.logger.info("Offline writing capture succeeded chars=%s", len(text))
                return text
            time.sleep(0.02)
        sequence_after = self.clipboard.get_sequence_number()
        self.logger.warning(
            "Offline writing clipboard sequence timeout before=%s after=%s",
            sequence_before,
            sequence_after,
        )
        return ""


def _send_ctrl_key(vk: int, logger: logging.Logger | None = None) -> bool:
    user32 = ctypes.windll.user32
    ctrl_scan = int(user32.MapVirtualKeyW(VK_CONTROL, MAPVK_VK_TO_VSC))
    key_scan = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
    inputs = (
        _keyboard_input(0, ctrl_scan, KEYEVENTF_SCANCODE),
        _keyboard_input(0, key_scan, KEYEVENTF_SCANCODE),
        _keyboard_input(0, key_scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP),
        _keyboard_input(0, ctrl_scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP),
    )
    input_array = (INPUT * len(inputs))(*inputs)
    expected = len(inputs)
    if logger:
        logger.info("Offline writing send_copy started inputs=%s", expected)
        logger.info("Offline writing SendInput expected count=%s", expected)
    sent = user32.SendInput(
        expected,
        input_array,
        ctypes.sizeof(INPUT),
    )
    sent_count = int(sent)
    if logger:
        logger.info("Offline writing SendInput returned count=%s", sent_count)
    if sent_count != expected:
        last_error = ctypes.get_last_error()
        if logger:
            logger.warning(
                "Offline writing SendInput incomplete expected=%s returned=%s last_error=%s",
                expected,
                sent_count,
                last_error,
            )
        return False
    return True


def _keyboard_input(vk: int, scan_code: int, flags: int) -> INPUT:
    return INPUT(
        type=INPUT_KEYBOARD,
        union=INPUT_UNION(
            ki=KEYBDINPUT(
                wVk=vk,
                wScan=scan_code,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )


def _configure_clipboard_ctypes() -> None:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    if hasattr(user32, "GetGUIThreadInfo"):
        user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.c_void_p]
        user32.GetGUIThreadInfo.restype = wintypes.BOOL
    if hasattr(user32, "GetClassNameW"):
        user32.GetClassNameW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        user32.GetClassNameW.restype = ctypes.c_int
    if hasattr(user32, "SendMessageW"):
        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = LRESULT
    if hasattr(user32, "SendMessageTimeoutW"):
        user32.SendMessageTimeoutW.restype = LRESULT
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = wintypes.SHORT
    user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    user32.MapVirtualKeyW.restype = wintypes.UINT
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
    user32.EnumClipboardFormats.restype = wintypes.UINT
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = HGLOBAL
    user32.SetClipboardData.argtypes = [wintypes.UINT, HGLOBAL]
    user32.SetClipboardData.restype = HGLOBAL
    user32.GetClipboardSequenceNumber.argtypes = []
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.SendInput.argtypes = [
        wintypes.UINT,
        ctypes.POINTER(INPUT),
        ctypes.c_int,
    ]
    user32.SendInput.restype = wintypes.UINT
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = HGLOBAL
    kernel32.GlobalLock.argtypes = [HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalSize.argtypes = [HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalFree.argtypes = [HGLOBAL]
    kernel32.GlobalFree.restype = HGLOBAL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        wintypes.LPDWORD,
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL


def _void_pointer(pointer) -> ctypes.c_void_p:
    if isinstance(pointer, ctypes.c_void_p):
        return pointer
    return ctypes.c_void_p(pointer)


def get_foreground_window() -> int:
    return int(ctypes.windll.user32.GetForegroundWindow() or 0)


def get_window_process_identity(hwnd: int) -> tuple[int, str]:
    if not hwnd:
        return 0, "unknown"
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    pid = wintypes.DWORD(0)
    try:
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        process_handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid.value,
        )
        if not process_handle:
            return int(pid.value), "unknown"
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_ulong(len(buffer))
            if kernel32.QueryFullProcessImageNameW(
                process_handle,
                0,
                buffer,
                ctypes.byref(size),
            ):
                return int(pid.value), Path(buffer.value).name
        finally:
            kernel32.CloseHandle(process_handle)
    except Exception:
        return int(pid.value), "unknown"
    return int(pid.value), "unknown"


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def get_focused_window(foreground_hwnd: int) -> int:
    if not foreground_hwnd:
        return 0
    pid = wintypes.DWORD()
    thread_id = ctypes.windll.user32.GetWindowThreadProcessId(
        wintypes.HWND(foreground_hwnd), ctypes.byref(pid)
    )
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(info)
    if thread_id and ctypes.windll.user32.GetGUIThreadInfo(
        thread_id, ctypes.byref(info)
    ):
        return int(info.hwndFocus or 0)
    return 0


def get_window_class(hwnd: int) -> str:
    if not hwnd:
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    if ctypes.windll.user32.GetClassNameW(hwnd, buffer, len(buffer)):
        return buffer.value
    return ""


def is_standard_edit_control(hwnd: int) -> bool:
    class_name = get_window_class(hwnd).casefold()
    return (
        class_name == "edit"
        or class_name.startswith("richedit")
        or ".edit." in class_name
    )


def control_has_selection(hwnd: int) -> bool:
    selection = int(
        ctypes.windll.user32.SendMessageW(hwnd, EM_GETSEL, 0, 0)
    )
    start = selection & 0xFFFF
    end = (selection >> 16) & 0xFFFF
    return start != end


def send_control_message(hwnd: int, message: int) -> bool:
    if not hwnd:
        return False
    result = ctypes.c_size_t()
    delivered = ctypes.windll.user32.SendMessageTimeoutW(
        hwnd,
        message,
        0,
        0,
        SMTO_ABORTIFHUNG,
        1000,
        ctypes.byref(result),
    )
    return bool(delivered)


def restore_foreground_window(hwnd: int, timeout_seconds: float = 0.5) -> bool:
    if not hwnd:
        return False
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        if get_foreground_window() == hwnd:
            return True
        time.sleep(0.01)
    return get_foreground_window() == hwnd


def _wait_for_modifier_release(
    timeout_seconds: float,
    action_key: int = VK_P,
    logger: logging.Logger | None = None,
) -> bool:
    deadline = time.perf_counter() + timeout_seconds
    if logger:
        down = _physical_key_state(action_key)
        logger.info(
            "Offline writing modifier release wait started "
            "ctrl=%s alt=%s shift=%s win=%s action=%s",
            down["ctrl"],
            down["alt"],
            down["shift"],
            down["win"],
            down["action"],
        )
    else:
        down = {}
    while time.perf_counter() < deadline:
        down = _physical_key_state(action_key)
        if not any(down.values()):
            if logger:
                logger.info(
                    "Offline writing modifiers released ctrl=false alt=false "
                    "shift=false win=false action=false"
                )
            return True
        time.sleep(0.01)
    if logger:
        logger.warning(
            "Offline writing modifier release wait timed out "
            "ctrl=%s alt=%s shift=%s win=%s action=%s",
            down["ctrl"],
            down["alt"],
            down["shift"],
            down["win"],
            down["action"],
        )
    return False


def _physical_key_state(action_key: int) -> dict[str, bool]:
    user32 = ctypes.windll.user32
    return {
        "ctrl": bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000),
        "alt": bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000),
        "shift": bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000),
        "win": bool(
            user32.GetAsyncKeyState(VK_LWIN) & 0x8000
            or user32.GetAsyncKeyState(VK_RWIN) & 0x8000
        ),
        "action": bool(user32.GetAsyncKeyState(action_key) & 0x8000),
    }


def _wait_for_foreground_stability(hwnd: int, timeout_seconds: float) -> bool:
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        if get_foreground_window() != hwnd:
            return False
        time.sleep(0.02)
    return get_foreground_window() == hwnd


def _rounded(value: float | None) -> str:
    return "none" if value is None else f"{value:.2f}"
