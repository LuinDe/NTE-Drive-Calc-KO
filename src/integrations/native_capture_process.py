# 只读定位唯一游戏进程，为增强战报建立明确的连接目标。
"""Resolve the native capture target without attaching to or modifying it."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os

from src.integrations.nte_core_protocol import NteCoreProcessError


def native_capture_game_pid() -> int | None:
    if os.name != "nt":
        return None

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    snapshot.restype = wintypes.HANDLE
    first, following = kernel.Process32FirstW, kernel.Process32NextW
    for function in (first, following):
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        function.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise NteCoreProcessError("게임 프로세스 목록을 읽을 수 없습니다.")
    matches: list[int] = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        found = first(handle, ctypes.byref(entry))
        while found:
            if entry.szExeFile.casefold() == "htgame.exe":
                matches.append(int(entry.th32ProcessID))
            found = following(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise NteCoreProcessError("게임 프로세스 목록 읽기에 실패했습니다.")
    finally:
        close(handle)
    if not matches:
        return None
    if len(matches) != 1:
        raise NteCoreProcessError("여러 게임 프로세스가 감지되었습니다. 하나만 남긴 후 강화 수집을 시작해 주세요.")
    pid = matches[0]
    open_process = kernel.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    process = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        raise NteCoreProcessError("게임 프로세스 신원을 확인할 수 없어 이번 수집을 시작하지 않았습니다.")
    times = kernel.GetProcessTimes
    times.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    times.restype = wintypes.BOOL
    values = [wintypes.FILETIME() for _ in range(4)]
    try:
        if not times(process, *(ctypes.byref(value) for value in values)):
            raise NteCoreProcessError("게임 프로세스 생성 시간을 확인할 수 없어 이번 수집을 시작하지 않았습니다.")
    finally:
        close(process)
    creation = (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
    wait = kernel.WaitNamedPipeW
    wait.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    wait.restype = wintypes.BOOL
    if wait(rf"\\.\pipe\nte.capture.v1.{pid}.{creation}", 1):
        return pid
    error = ctypes.get_last_error()
    if error == 2:  # ERROR_FILE_NOT_FOUND: no native provider for this process.
        return None
    if error in (5, 121, 231):  # Exists but denied, timed out or busy: surface handshake failure.
        return pid
    raise NteCoreProcessError("강화 수집 컴포넌트 상태를 확인할 수 없어 이번 전투 수집을 시작하지 않았습니다.")
