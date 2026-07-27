from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
ERROR_HOTKEY_ALREADY_REGISTERED = 1409


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_ulong),
        ("pt", POINT),
    ]


@dataclass(frozen=True)
class HotkeyBinding:
    identifier: int
    shortcut: str
    callback: Callable[[], None]


class WindowsHotkeyManager:
    def __init__(
        self,
        bindings: list[HotkeyBinding],
        logger: logging.Logger | None = None,
    ):
        self.bindings = bindings
        self.logger = logger or logging.getLogger("offline-writing-reviser")
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._started = threading.Event()
        self._stop_requested = threading.Event()
        self._registered_ids: set[int] = set()
        self.registration_errors: dict[int, int] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_requested.clear()
        self._started.clear()
        self._thread = threading.Thread(
            target=self._message_loop,
            name="offline-writing-hotkeys",
            daemon=True,
        )
        self._thread.start()
        self._started.wait(timeout=2)

    def stop(self) -> None:
        self._stop_requested.set()
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self._thread_id = None

    @property
    def registered_count(self) -> int:
        return len(self._registered_ids)

    @property
    def all_registered(self) -> bool:
        return len(self._registered_ids) == len(self.bindings)

    def _message_loop(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        callbacks = {binding.identifier: binding.callback for binding in self.bindings}

        try:
            for binding in self.bindings:
                modifiers, vk = parse_hotkey(binding.shortcut)
                if binding.identifier in self._registered_ids:
                    continue
                self.logger.info(
                    "Offline writing hotkey registration started shortcut=%s identifier=%s",
                    binding.shortcut,
                    binding.identifier,
                )
                if not user32.RegisterHotKey(None, binding.identifier, modifiers, vk):
                    error_code = int(kernel32.GetLastError())
                    self.registration_errors[binding.identifier] = error_code
                    self.logger.error(
                        "Offline writing hotkey registration failed shortcut=%s "
                        "identifier=%s error_code=%s",
                        binding.shortcut,
                        binding.identifier,
                        error_code,
                    )
                    continue
                self._registered_ids.add(binding.identifier)
                self.logger.info(
                    "Offline writing hotkey registered shortcut=%s identifier=%s",
                    binding.shortcut,
                    binding.identifier,
                )
            self._started.set()

            message = MSG()
            while not self._stop_requested.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1):
                    break
                if message.message == WM_HOTKEY:
                    callback = callbacks.get(int(message.wParam))
                    if callback:
                        self.logger.info(
                            "Offline writing hotkey received identifier=%s",
                            int(message.wParam),
                        )
                        callback()
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception:
            self.logger.exception("Desktop hotkey manager failed")
        finally:
            for hotkey_id in list(self._registered_ids):
                user32.UnregisterHotKey(None, hotkey_id)
                self._registered_ids.discard(hotkey_id)
            self._started.set()


def parse_hotkey(shortcut: str) -> tuple[int, int]:
    modifiers = 0
    key = None
    for part in shortcut.split("+"):
        normalized = part.strip().lower()
        if normalized == "ctrl":
            modifiers |= MOD_CONTROL
        elif normalized == "alt":
            modifiers |= MOD_ALT
        elif len(normalized) == 1:
            key = ord(normalized.upper())
        else:
            raise ValueError(f"Unsupported hotkey component: {part}")
    if key is None:
        raise ValueError("Hotkey must include a key")
    return modifiers, key
