from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from enum import Enum


CONTROL_WINDOW_CLASS = "OfflineWritingReviserControlWindow"
CONTROL_WINDOW_TITLE = "Offline Writing Reviser Background Control"

WM_APP = 0x8000
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_CONTROL_SETTINGS = WM_APP + 101
WM_CONTROL_EXIT = WM_APP + 102
WM_CONTROL_RESTART = WM_APP + 103

WS_POPUP = 0x80000000
WS_EX_TOOLWINDOW = 0x00000080
ERROR_CLASS_ALREADY_EXISTS = 1410


class ControlCommand(str, Enum):
    SETTINGS = "settings"
    EXIT = "exit"
    RESTART = "restart"


COMMAND_MESSAGES = {
    ControlCommand.SETTINGS: WM_CONTROL_SETTINGS,
    ControlCommand.EXIT: WM_CONTROL_EXIT,
    ControlCommand.RESTART: WM_CONTROL_RESTART,
}


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


WNDPROC_TYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    wintypes.LPARAM,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


def _configure_user32(user32) -> None:
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t


class WindowsControlServer:
    """A hidden Win32 window used only for same-user instance commands."""

    def __init__(
        self,
        *,
        on_settings: Callable[[], None],
        on_exit: Callable[[], None],
        on_restart: Callable[[], None],
        logger: logging.Logger | None = None,
    ):
        self.callbacks = {
            WM_CONTROL_SETTINGS: on_settings,
            WM_CONTROL_EXIT: on_exit,
            WM_CONTROL_RESTART: on_restart,
        }
        self.logger = logger or logging.getLogger("offline-writing-reviser")
        self._thread: threading.Thread | None = None
        self._window_handle: int | None = None
        self._started = threading.Event()
        self._startup_error: BaseException | None = None
        self._window_procedure = WNDPROC_TYPE(self._handle_message)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._started.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._message_loop,
            name="offline-writing-instance-control",
            daemon=True,
        )
        self._thread.start()
        self._started.wait(timeout=3)
        if self._startup_error:
            raise RuntimeError("Background control endpoint failed") from self._startup_error
        if not self._window_handle:
            raise RuntimeError("Background control endpoint did not start")

    def stop(self) -> None:
        handle = self._window_handle
        if handle:
            ctypes.windll.user32.PostMessageW(handle, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self._window_handle = None

    @property
    def is_running(self) -> bool:
        return bool(self._window_handle and self._thread and self._thread.is_alive())

    def _message_loop(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        _configure_user32(user32)
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        instance = kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSW(
            style=0,
            lpfnWndProc=ctypes.cast(self._window_procedure, ctypes.c_void_p).value,
            cbClsExtra=0,
            cbWndExtra=0,
            hInstance=instance,
            hIcon=None,
            hCursor=None,
            hbrBackground=None,
            lpszMenuName=None,
            lpszClassName=CONTROL_WINDOW_CLASS,
        )
        atom = 0
        try:
            atom = user32.RegisterClassW(ctypes.byref(window_class))
            if not atom and int(kernel32.GetLastError()) != ERROR_CLASS_ALREADY_EXISTS:
                raise ctypes.WinError()
            handle = user32.CreateWindowExW(
                WS_EX_TOOLWINDOW,
                CONTROL_WINDOW_CLASS,
                CONTROL_WINDOW_TITLE,
                WS_POPUP,
                0,
                0,
                0,
                0,
                None,
                None,
                instance,
                None,
            )
            if not handle:
                raise ctypes.WinError()
            self._window_handle = int(handle)
            self.logger.info("Background control endpoint started")
            self._started.set()

            message = wintypes.MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except BaseException as exc:
            self._startup_error = exc
            self.logger.exception("Background control endpoint failed")
        finally:
            self._window_handle = None
            self._started.set()
            if atom:
                user32.UnregisterClassW(CONTROL_WINDOW_CLASS, instance)
            self.logger.info("Background control endpoint stopped")

    def _handle_message(self, hwnd, message, wparam, lparam):
        user32 = ctypes.windll.user32
        callback = self.callbacks.get(int(message))
        if callback:
            try:
                callback()
            except Exception:
                self.logger.exception(
                    "Background control command failed message=%s", int(message)
                )
            return 0
        if message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)


def send_control_command(command: ControlCommand) -> bool:
    user32 = ctypes.windll.user32
    _configure_user32(user32)
    handle = user32.FindWindowW(CONTROL_WINDOW_CLASS, CONTROL_WINDOW_TITLE)
    if not handle:
        return False
    return bool(user32.PostMessageW(handle, COMMAND_MESSAGES[command], 0, 0))


def wait_for_control_server(timeout_seconds: float = 10.0) -> bool:
    _configure_user32(ctypes.windll.user32)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if ctypes.windll.user32.FindWindowW(
            CONTROL_WINDOW_CLASS, CONTROL_WINDOW_TITLE
        ):
            return True
        time.sleep(0.1)
    return False
