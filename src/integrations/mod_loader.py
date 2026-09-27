# 管理 nte-mod-loader 的受控提权进程和命名停止事件。
"""Windows integration for the optional upstream NTE Mod Loader."""

from __future__ import annotations

from collections.abc import Callable
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
import json
import subprocess
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


def _unique_capability_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate capability key')
        value[key] = item
    return value


def probe_mod_loader_capabilities(loader_path: str | Path) -> frozenset[str]:
    """Query the explicit dry-run protocol; the Loader checks its own embedded shim."""
    loader = Path(loader_path).resolve()
    try:
        completed = subprocess.run(
            [str(loader), '--capabilities-json', '--dry-run', '--once'],
            capture_output=True, text=True, encoding='utf-8-sig', errors='strict',
            timeout=5, check=False, cwd=str(loader.parent),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
    except OSError as error:
        if getattr(error, 'winerror', None) == 740:
            raise ModLoaderRuntimeError('현재 실행 환경이 권한 상승되지 않아 Loader 능력을 읽을 수 없습니다; 앱의 정상적인 권한 상승 방식으로 시작하세요.') from error
        raise ModLoaderRuntimeError('Loader의 읽기 전용 기능 검사를 실행할 수 없습니다.') from error
    except (subprocess.TimeoutExpired, UnicodeError) as error:
        raise ModLoaderRuntimeError('Loader 기능 검사가 정상적으로 완료되지 않아 로드를 시작하지 않았습니다.') from error
    try:
        if completed.returncode != 0 or len(completed.stdout) > 16384:
            raise ValueError('query failed')
        value = json.loads(completed.stdout, object_pairs_hook=_unique_capability_pairs)
        if not isinstance(value, dict):
            raise ValueError('capability object required')
        modes = value.get('payload_load_modes')
        kinds = value.get('payload_kinds')
        if (type(value.get('schema_version')) is not int or value['schema_version'] != 1
                or value.get('component') != 'nte-mod-loader'
                or value.get('embedded_shim_compatible') is not True
                or value.get('managed_session') is not True
                or type(value.get('shim_protocol_version')) is not int or value['shim_protocol_version'] != 3
                or not isinstance(modes, list) or not modes
                or any(not isinstance(mode, str) for mode in modes) or len(modes) != len(set(modes))
                or not isinstance(kinds, list) or any(not isinstance(kind, str) for kind in kinds)
                or len(kinds) != len(set(kinds)) or 'nte_capture_runtime_v1' not in kinds):
            raise ValueError('capability protocol mismatch')
    except (ValueError, TypeError, AttributeError, RecursionError) as error:
        raise ModLoaderRuntimeError('현재 Loader와 내장 shim이 호환되는 표준 로드 기능을 선언하지 않았습니다. 호환 Loader로 교체해 주세요.') from error
    return frozenset(modes)


def mod_loader_arguments(*, payload_path: Path, event_name: str, owner_pid: int,
                         payload_load_mode: str = "loadlibrary") -> str:
    if payload_load_mode != 'loadlibrary':
        raise ModLoaderRuntimeError('지원하지 않는 Loader payload 로드 방식입니다.')
    mode = ' --payload-load-mode loadlibrary'
    return (f'--dll "{payload_path}"{mode} --monitor-timeout 0 '
            f'--stop-event "{event_name}" --owner-pid {owner_pid}')


def game_launcher_candidates(game_executable_path: str | Path) -> tuple[Path, ...]:
    """List only launcher images in the selected HTGame installation."""
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
    found = tuple(candidate.resolve() for candidate in candidates if candidate.is_file())
    if found:
        return found
    raise ModLoaderRuntimeError(
        "선택한 HTGame.exe의 설치 루트에서 공식 런처를 찾을 수 없습니다. 게임 설치를 복구하거나 게임을 다시 선택하세요"
    )


def game_launcher_executable(game_executable_path: str | Path) -> Path:
    """Resolve the primary trusted launcher used by the Loader."""
    return game_launcher_candidates(game_executable_path)[0]


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

    def require_payload_load_mode(self, mode: str) -> None:
        if mode != 'loadlibrary':
            raise ModLoaderRuntimeError('이 검사는 네이티브 수집 DLL의 표준 로드에만 사용됩니다.')
        loader = packaged_mod_loader(self._application_root)
        if mode not in probe_mod_loader_capabilities(loader):
            raise ModLoaderRuntimeError('현재 Loader는 표준 수집 로드를 지원하지 않습니다. 호환 Loader로 교체해 주세요.')

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
                detail="Loader의 payload 파일이 아직 준비되지 않았습니다.",
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
        payload_load_mode: str = "loadlibrary",
        launch_guard: Callable[[], None] | None = None,
    ) -> ModLoaderRuntimeSnapshot:
        if os.name != "nt":
            raise ModLoaderRuntimeError("Mod Loader는 Windows만 지원합니다")
        if payload_load_mode != 'loadlibrary':
            raise ModLoaderRuntimeError('지원하지 않는 Loader payload 로드 방식입니다.')
        self.require_payload_load_mode(payload_load_mode)
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
            if launch_guard is not None:
                launch_guard()
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

            parameters = mod_loader_arguments(payload_path=payload, event_name=event_name,
                                               owner_pid=os.getpid(), payload_load_mode=payload_load_mode)
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
                if launch_guard is not None:
                    launch_guard()
                launched = bool(shell_execute(ctypes.byref(execution)))
                if not launched or not execution.hProcess:
                    launch_error = ctypes.get_last_error()
            except Exception:
                close_handle = kernel32.CloseHandle
                close_handle.argtypes = (wintypes.HANDLE,)
                close_handle.restype = wintypes.BOOL
                close_handle(event_handle)
                raise
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
