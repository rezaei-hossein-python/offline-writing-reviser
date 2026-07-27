from __future__ import annotations

import ctypes
import logging
import os
import sys
from collections.abc import Callable, Iterable
from ctypes import wintypes
from pathlib import Path


TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def cleanup_owned_languagetool_processes(
    java_path: Path,
    *,
    process_paths: Iterable[tuple[int, Path]] | None = None,
    terminate: Callable[[int], bool] | None = None,
    logger: logging.Logger | None = None,
) -> list[int]:
    """Stop only Java processes using this installation's bundled executable."""
    if sys.platform != "win32" and process_paths is None:
        return []

    target = _normalized_path(java_path)
    candidates = process_paths if process_paths is not None else _process_paths()
    terminate_process = terminate or _terminate_process
    stopped: list[int] = []
    for process_id, executable in candidates:
        if process_id == os.getpid():
            continue
        if _normalized_path(executable) != target:
            continue
        if terminate_process(process_id):
            stopped.append(process_id)

    if logger and stopped:
        logger.info(
            "LanguageTool orphan cleanup stopped_count=%s bundled_java=true",
            len(stopped),
        )
    return stopped


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _process_paths() -> Iterable[tuple[int, Path]]:
    kernel32 = ctypes.windll.kernel32
    _configure_kernel32(kernel32)
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            process_id = int(entry.th32ProcessID)
            executable = _query_process_path(process_id)
            if executable is not None:
                yield process_id, executable
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)


def _query_process_path(process_id: int) -> Path | None:
    kernel32 = ctypes.windll.kernel32
    _configure_kernel32(kernel32)
    process = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, process_id
    )
    if not process:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(
            process, 0, buffer, ctypes.byref(length)
        ):
            return None
        return Path(buffer.value)
    finally:
        kernel32.CloseHandle(process)


def _terminate_process(process_id: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    _configure_kernel32(kernel32)
    process = kernel32.OpenProcess(
        PROCESS_TERMINATE | SYNCHRONIZE, False, process_id
    )
    if not process:
        return False
    try:
        if not kernel32.TerminateProcess(process, 0):
            return False
        kernel32.WaitForSingleObject(process, 5000)
        return True
    finally:
        kernel32.CloseHandle(process)


def _configure_kernel32(kernel32) -> None:
    kernel32.CreateToolhelp32Snapshot.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
