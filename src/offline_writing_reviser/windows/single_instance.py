from __future__ import annotations

import ctypes


ERROR_ALREADY_EXISTS = 183


class WindowsSingleInstance:
    def __init__(self, mutex_name: str):
        self.mutex_name = mutex_name
        self._handle = None

    def acquire(self) -> bool:
        try:
            kernel32 = ctypes.windll.kernel32
            self._handle = kernel32.CreateMutexW(None, False, self.mutex_name)
            if not self._handle:
                return True
            return kernel32.GetLastError() != ERROR_ALREADY_EXISTS
        except Exception:
            return True

    def release(self) -> None:
        if not self._handle:
            return
        try:
            ctypes.windll.kernel32.CloseHandle(self._handle)
        finally:
            self._handle = None
