# 管理 nte-mod-loader 的受控提权进程和命名停止事件。
"""Windows integration for the optional upstream NTE Mod Loader."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import threading


MOD_LOADER_FILENAME = "nte-mod-loader.exe"
MOD_LOADER_ENV = "NTE_MOD_LOADER_EXE"
MOD_LOADER_LAUNCHER_ENV = "NTE_MOD_LOADER_LAUNCHER"
PACKAGED_MOD_LOADER_RELATIVE_PATH = (
    Path("third_party") / "mod-loader" / "bin" / MOD_LOADER_FILENAME
)

_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SW_HIDE = 0
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_ERROR_CANCELLED = 1223
MOD_LOADER_STOP_TIMEOUT_MS = 15_000
_LAUNCHER_NAMES = ("NTELauncher.exe", "NTEGlobalLauncher.exe")
_GAME_EXECUTABLE_RELATIVE_PARTS = (
    "Client",
    "WindowsNoEditor",
    "HT",
    "Binaries",
    "Win64",
    "HTGame.exe",
)


class ModLoaderRuntimeError(RuntimeError):
    """The loader session could not be started or stopped safely."""


@dataclass(frozen=True)
class ModLoaderRuntimeSnapshot:
    phase: str
    loader_path: Path
    payload_path: Path
    process_id: int | None = None
    detail: str = ""


class _ShellExecuteInfoW(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    )


def _managed_stop_event_name(
    *,
    process_id: int,
    session_suffix: int | None = None,
) -> str:
    """Build the exact managed-control name accepted by upstream 0.4.1."""

    suffix = secrets.randbits(32) if session_suffix is None else session_suffix
    return (
        "Local\\NTE-DPS-TOOL-ModLoader-"
        f"{process_id & 0xFFFFFFFF:08x}{suffix & 0xFFFFFFFF:08x}"
    )


def packaged_mod_loader(application_root: str | Path) -> Path:
    """Resolve a replaceable loader in source and PyInstaller layouts."""

    root = Path(application_root).resolve()
    configured = os.environ.get(MOD_LOADER_ENV)
    candidates = (
        Path(configured).expanduser().resolve() if configured else None,
        root / PACKAGED_MOD_LOADER_RELATIVE_PATH,
        root / MOD_LOADER_FILENAME,
    )
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    checked = "、".join(str(candidate) for candidate in candidates if candidate)
    raise ModLoaderRuntimeError(
        f"{MOD_LOADER_FILENAME}을(를) 찾을 수 없습니다. 확인한 위치: {checked}."
        "신뢰할 수 있는 출처의 같은 이름 Loader를 앱 디렉터리에 다시 넣은 뒤 재시도할 수 있습니다"
    )


def game_launcher_executable(game_executable_path: str | Path) -> Path:
    """Resolve a trusted launcher from the user-selected HTGame installation."""

    game = Path(game_executable_path).expanduser().resolve()
    if not game.is_file() or game.name.casefold() != "htgame.exe":
        raise ModLoaderRuntimeError("유효한 HTGame.exe를 선택하지 않아 공식 런처를 찾을 수 없습니다")
    expected_tail = tuple(
        part.casefold() for part in _GAME_EXECUTABLE_RELATIVE_PARTS
    )
    actual_tail = tuple(
        part.casefold() for part in game.parts[-len(expected_tail):]
    )
    if actual_tail != expected_tail:
        raise ModLoaderRuntimeError(
            "선택한 HTGame.exe가 공식 Client 디렉터리 구조와 맞지 않아 신뢰 런처 루트를 만들 수 없습니다"
        )
    install_root = game.parents[len(_GAME_EXECUTABLE_RELATIVE_PARTS) - 1]
    candidates = [install_root / name for name in _LAUNCHER_NAMES]
    candidates.extend(
        install_root / "NTELauncher" / name
        for name in _LAUNCHER_NAMES
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ModLoaderRuntimeError(
        "선택한 HTGame.exe의 설치 루트에서 공식 런처를 찾을 수 없습니다. 게임 설치를 복구하거나 게임을 다시 선택하세요"
    )


class ModLoaderRuntime:
    """Own one elevated Loader process and its cooperative stop event."""

    def __init__(self, *, application_root: str | Path) -> None:
        self._application_root = Path(application_root).resolve()
        self._lock = threading.Lock()
        self._process_handle: int | None = None
        self._stop_event_handle: int | None = None
        self._process_id: int | None = None
        self._loader_path: Path | None = None
        self._payload_path: Path | None = None

    def snapshot(self, *, payload_path: str | Path) -> ModLoaderRuntimeSnapshot:
        payload = Path(payload_path).resolve()
        try:
            loader = packaged_mod_loader(self._application_root)
        except ModLoaderRuntimeError as exc:
            return ModLoaderRuntimeSnapshot(
                "missing_loader",
                self._application_root / MOD_LOADER_FILENAME,
                payload,
                detail=str(exc),
            )
        if not payload.is_file():
            return ModLoaderRuntimeSnapshot(
                "missing_payload",
                loader,
                payload,
                detail="패키지된 dwmapi.dll이 없습니다",
            )
        if os.name != "nt":
            return ModLoaderRuntimeSnapshot(
                "unsupported",
                loader,
                payload,
                detail="Mod Loader는 Windows만 지원합니다",
            )
        with self._lock:
            running = self._refresh_running_locked()
            return ModLoaderRuntimeSnapshot(
                "running" if running else "stopped",
                loader,
                payload,
                self._process_id if running else None,
            )

    def start(
        self,
        *,
        payload_path: str | Path,
        launcher_path: str | Path,
    ) -> ModLoaderRuntimeSnapshot:
        if os.name != "nt":
            raise ModLoaderRuntimeError("Mod Loader는 Windows만 지원합니다")
        loader = packaged_mod_loader(self._application_root)
        payload = Path(payload_path).resolve()
        if not payload.is_file():
            raise ModLoaderRuntimeError(f"Mod Loader payload가 없습니다: {payload}")
        launcher = Path(launcher_path).expanduser().resolve()
        if (
            not launcher.is_file()
            or launcher.name.casefold()
            not in {name.casefold() for name in _LAUNCHER_NAMES}
        ):
            raise ModLoaderRuntimeError("Mod Loader에 넘길 공식 런처를 찾을 수 없습니다")

        with self._lock:
            if self._refresh_running_locked():
                return ModLoaderRuntimeSnapshot(
                    "running",
                    self._loader_path or loader,
                    self._payload_path or payload,
                    self._process_id,
                )
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            create_event = kernel32.CreateEventW
            create_event.argtypes = (
                ctypes.c_void_p,
                wintypes.BOOL,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            )
            create_event.restype = wintypes.HANDLE
            event_name = _managed_stop_event_name(process_id=os.getpid())
            event_handle = create_event(None, True, False, event_name)
            if not event_handle:
                raise ModLoaderRuntimeError(
                    f"Loader 중지 이벤트를 만들 수 없습니다. Windows 오류 {ctypes.get_last_error()}"
                )

            parameters = (
                f'--dll "{payload}" --monitor-timeout 0 '
                f'--stop-event "{event_name}" --owner-pid {os.getpid()}'
            )
            execution = _ShellExecuteInfoW()
            execution.cbSize = ctypes.sizeof(_ShellExecuteInfoW)
            execution.fMask = _SEE_MASK_NOCLOSEPROCESS
            execution.lpVerb = "runas"
            execution.lpFile = str(loader)
            execution.lpParameters = parameters
            execution.lpDirectory = str(loader.parent)
            execution.nShow = _SW_HIDE
            shell_execute = shell32.ShellExecuteExW
            shell_execute.argtypes = (ctypes.POINTER(_ShellExecuteInfoW),)
            shell_execute.restype = wintypes.BOOL
            previous_launcher = os.environ.get(MOD_LOADER_LAUNCHER_ENV)
            os.environ[MOD_LOADER_LAUNCHER_ENV] = str(launcher)
            launch_error = 0
            try:
                launched = bool(shell_execute(ctypes.byref(execution)))
                if not launched or not execution.hProcess:
                    launch_error = ctypes.get_last_error()
            finally:
                if previous_launcher is None:
                    os.environ.pop(MOD_LOADER_LAUNCHER_ENV, None)
                else:
                    os.environ[MOD_LOADER_LAUNCHER_ENV] = previous_launcher
            if not launched or not execution.hProcess:
                close_handle = kernel32.CloseHandle
                close_handle.argtypes = (wintypes.HANDLE,)
                close_handle.restype = wintypes.BOOL
                close_handle(event_handle)
                if launch_error == _ERROR_CANCELLED:
                    raise ModLoaderRuntimeError("사용자가 Mod Loader 관리자 권한 부여를 취소했습니다")
                raise ModLoaderRuntimeError(
                    f"Mod Loader를 시작할 수 없습니다. Windows 오류 {launch_error}."
                    f"신뢰할 수 있는 출처의 {MOD_LOADER_FILENAME}(으)로 기존 파일을 덮어쓴 뒤 재시도할 수 있습니다"
                )

            get_process_id = kernel32.GetProcessId
            get_process_id.argtypes = (wintypes.HANDLE,)
            get_process_id.restype = wintypes.DWORD
            self._process_handle = int(execution.hProcess)
            self._stop_event_handle = int(event_handle)
            self._process_id = int(get_process_id(execution.hProcess)) or None
            self._loader_path = loader
            self._payload_path = payload
            return ModLoaderRuntimeSnapshot(
                "running",
                loader,
                payload,
                self._process_id,
            )

    def stop(self, *, timeout_ms: int = MOD_LOADER_STOP_TIMEOUT_MS) -> bool:
        if os.name != "nt":
            return False
        with self._lock:
            if not self._refresh_running_locked():
                return False
            process_handle = self._process_handle
            stop_event_handle = self._stop_event_handle
            if process_handle is None or stop_event_handle is None:
                self._close_handles_locked()
                raise ModLoaderRuntimeError("Mod Loader 세션 핸들이 불완전해 해당 세션 관리를 중단했습니다")
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            set_event = kernel32.SetEvent
            set_event.argtypes = (wintypes.HANDLE,)
            set_event.restype = wintypes.BOOL
            wait_for_single_object = kernel32.WaitForSingleObject
            wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            wait_for_single_object.restype = wintypes.DWORD
            if not set_event(wintypes.HANDLE(stop_event_handle)):
                raise ModLoaderRuntimeError(
                    f"Mod Loader에 중지를 알릴 수 없습니다. Windows 오류 {ctypes.get_last_error()}"
                )
            result = wait_for_single_object(
                wintypes.HANDLE(process_handle),
                max(0, int(timeout_ms)),
            )
            if result == _WAIT_TIMEOUT:
                raise ModLoaderRuntimeError(
                    "Mod Loader가 제한 시간 안에 종료되지 않았습니다. 이중 로드를 피하기 위해 로드 방식을 전환하지 않았습니다"
                )
            if result == _WAIT_FAILED:
                raise ModLoaderRuntimeError(
                    "Mod Loader 종료 대기 실패. Windows 오류 "
                    f"{ctypes.get_last_error()}"
                )
            if result != _WAIT_OBJECT_0:
                raise ModLoaderRuntimeError(
                    f"Mod Loader 종료 대기 실패. Windows 결과 {result}"
                )
            self._close_handles_locked()
            return True

    def close(self) -> None:
        self.stop(timeout_ms=MOD_LOADER_STOP_TIMEOUT_MS)

    def _refresh_running_locked(self) -> bool:
        if self._process_handle is None:
            return False
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        wait_for_single_object.restype = wintypes.DWORD
        result = wait_for_single_object(
            wintypes.HANDLE(self._process_handle), 0
        )
        if result == _WAIT_TIMEOUT:
            return True
        if result == _WAIT_OBJECT_0:
            self._close_handles_locked()
            return False
        if result == _WAIT_FAILED:
            raise ModLoaderRuntimeError(
                "Mod Loader 프로세스 상태를 확인할 수 없습니다. Windows 오류 "
                f"{ctypes.get_last_error()}"
            )
        raise ModLoaderRuntimeError(
            f"Mod Loader 프로세스 상태를 확인할 수 없습니다. Windows 결과 {result}"
        )

    def _close_handles_locked(self) -> None:
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            for handle in (self._process_handle, self._stop_event_handle):
                if handle is not None:
                    close_handle(wintypes.HANDLE(handle))
        self._process_handle = None
        self._stop_event_handle = None
        self._process_id = None
        self._loader_path = None
        self._payload_path = None
