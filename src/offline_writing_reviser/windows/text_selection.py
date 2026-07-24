from __future__ import annotations

import ctypes
import logging
from pathlib import Path
import time
from dataclasses import dataclass
from ctypes import wintypes


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
INPUT_KEYBOARD = 1
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_C = 0x43
VK_V = 0x56
VK_W = 0x57
MAPVK_VK_TO_VSC = 0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

HCURSOR = wintypes.HANDLE
HGLOBAL = wintypes.HANDLE
LRESULT = ctypes.c_ssize_t
ULONG_PTR = wintypes.WPARAM


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


class WindowsClipboard:
    def __init__(self, retry_timeout_seconds: float = 0.5) -> None:
        _configure_clipboard_ctypes()
        self.retry_timeout_seconds = retry_timeout_seconds

    def snapshot(self) -> ClipboardSnapshot:
        formats: list[ClipboardFormatData] = []
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not self._open_clipboard():
            return ClipboardSnapshot(tuple())
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
            return
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
            return
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
            return ""
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
            return
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
        copy_wait_seconds: float = 0.35,
        paste_restore_delay_seconds: float = 0.2,
        modifier_release_wait_seconds: float = 1.0,
        logger: logging.Logger | None = None,
    ):
        self.clipboard = clipboard or WindowsClipboard()
        self.copy_wait_seconds = copy_wait_seconds
        self.paste_restore_delay_seconds = paste_restore_delay_seconds
        self.modifier_release_wait_seconds = modifier_release_wait_seconds
        self.logger = logger or logging.getLogger("offline-writing-reviser")

    def capture(self) -> SelectedTextCapture | None:
        self.logger.info("Offline writing capture started")
        snapshot: ClipboardSnapshot | None = None
        try:
            foreground = get_foreground_window()
            pid, process_name = get_window_process_identity(foreground)
            self.logger.info(
                "Offline writing foreground hwnd=%s pid=%s process=%s",
                foreground,
                pid,
                process_name,
            )
            if not _wait_for_modifier_release(
                timeout_seconds=self.modifier_release_wait_seconds,
                logger=self.logger,
            ):
                self.logger.warning("Offline writing capture failed stage=modifier_release")
                return None
            self.logger.info("Offline writing capture stage=clipboard_snapshot")
            snapshot = self.clipboard.snapshot()
            sequence_before = self.clipboard.get_sequence_number()
            self.logger.info("Offline writing clipboard sequence before=%s", sequence_before)
            self.logger.info("Offline writing capture stage=send_copy")
            if not _send_ctrl_key(VK_C, logger=self.logger):
                self.clipboard.restore(snapshot)
                self.logger.warning("Offline writing capture failed stage=send_copy")
                return None
            self.logger.info("Offline writing capture stage=wait_for_clipboard_sequence")
            text = self._wait_for_clipboard_sequence_change(sequence_before)
            self.logger.info("Offline writing capture stage=clipboard_restore")
            self.clipboard.restore(snapshot)
        except Exception:
            self.logger.exception("Offline writing capture failed stage=win32_ctypes")
            if snapshot is not None:
                self.clipboard.restore(snapshot)
            raise
        if not text or not text.strip():
            self.logger.warning("Offline writing capture failed stage=empty_clipboard")
            return None
        self.logger.info("Offline writing capture succeeded chars=%s", len(text))
        return SelectedTextCapture(
            text=text,
            foreground_window=foreground,
            foreground_pid=pid,
            foreground_process=process_name,
            clipboard_snapshot=snapshot,
        )

    def replace(self, capture: SelectedTextCapture, replacement: str) -> bool:
        self.logger.info("Offline writing replacement started chars=%s", len(replacement))
        current_foreground = get_foreground_window()
        if current_foreground != capture.foreground_window:
            self.logger.warning(
                "Offline writing replacement failed stage=focus_changed "
                "original_hwnd=%s current_hwnd=%s",
                capture.foreground_window,
                current_foreground,
            )
            return False
        self.clipboard.set_unicode_text(replacement)
        try:
            if not _send_ctrl_key(VK_V, logger=self.logger):
                self.logger.warning("Offline writing replacement failed stage=send_paste")
                return False
            _wait_for_foreground_stability(
                capture.foreground_window,
                timeout_seconds=self.paste_restore_delay_seconds,
            )
            self.logger.info("Offline writing replacement succeeded chars=%s", len(replacement))
            return True
        finally:
            self.clipboard.restore(capture.clipboard_snapshot)
            self.logger.info("Offline writing clipboard restoration completed")

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


def _wait_for_modifier_release(
    timeout_seconds: float,
    logger: logging.Logger | None = None,
) -> bool:
    deadline = time.perf_counter() + timeout_seconds
    user32 = ctypes.windll.user32
    if logger:
        logger.info("Offline writing modifier release wait started")
    while time.perf_counter() < deadline:
        ctrl_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        alt_down = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        w_down = bool(user32.GetAsyncKeyState(VK_W) & 0x8000)
        if not ctrl_down and not alt_down and not w_down:
            if logger:
                logger.info("Offline writing modifiers released")
            return True
        time.sleep(0.01)
    if logger:
        logger.warning("Offline writing modifier release wait timed out")
    return False


def _wait_for_foreground_stability(hwnd: int, timeout_seconds: float) -> bool:
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        if get_foreground_window() != hwnd:
            return False
        time.sleep(0.02)
    return get_foreground_window() == hwnd
