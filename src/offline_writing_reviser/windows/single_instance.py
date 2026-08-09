from __future__ import annotations

import ctypes
import time


ERROR_ALREADY_EXISTS = 183
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080


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


def wait_for_single_instance_stop(
    mutex_name: str, timeout_seconds: float = 15.0
) -> bool:
    """Wait until the owning process releases its named mutex."""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenMutexW.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.OpenMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel32.ReleaseMutex.restype = ctypes.c_bool
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
    except Exception:
        return True
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        handle = kernel32.OpenMutexW(SYNCHRONIZE, False, mutex_name)
        if not handle:
            return True
        try:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            result = kernel32.WaitForSingleObject(handle, remaining_ms)
            if result in {WAIT_OBJECT_0, WAIT_ABANDONED}:
                kernel32.ReleaseMutex(handle)
                return True
        finally:
            kernel32.CloseHandle(handle)
    return False
