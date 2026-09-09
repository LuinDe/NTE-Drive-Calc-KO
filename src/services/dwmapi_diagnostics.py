# 采集最新版 nte-mods-plugin DLL、脚本工作区和命名管道诊断。
"""Read-only diagnostics for the in-game equipment proxy DLL."""

from __future__ import annotations

import ctypes
import hashlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.integrations.mod_loader import (
    ModLoaderRuntimeError,
    packaged_mod_loader,
)
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    GAME_EXECUTABLE_NAME,
    MOD_SDK_CACHE_FILES,
    MOD_WORKSPACE_FILES,
    PLUGIN_FILENAME,
    game_executable,
    is_mods_plugin_dll,
    packaged_mod_workspace,
    packaged_plugin_dll,
    registered_mod_workspace,
)
from src.services.mod_plugin_loading_service import (
    MSVC_RUNTIME_FILES,
    probe_mod_plugin_msvc_runtime,
)


# Kept in sync with upstream nte_mods_ipc.h.  WaitNamedPipe only observes
# availability and never sends an equipment request or mutates game state.
EQUIPMENT_PIPE_NAME = r"\\.\pipe\nte-mods-plugin-v7"

_PIPE_ERROR_NAMES = {
    2: "ERROR_FILE_NOT_FOUND (파이프 없음)",
    5: "ERROR_ACCESS_DENIED (접근 거부)",
    121: "ERROR_SEM_TIMEOUT (파이프 대기 시간 초과)",
    231: "ERROR_PIPE_BUSY (파이프 사용 중)",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_details(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return result
    try:
        result["size"] = path.stat().st_size
        result["sha256"] = _sha256(path)
    except OSError as exc:
        result["read_error"] = str(exc)
    return result


def probe_equipment_pipe() -> dict[str, Any]:
    """Inspect the fixed plugin pipe without claiming a connection instance.

    ``WaitNamedPipeW(..., 0)`` is non-mutating.  A busy response is useful: it
    means the pipe has been created by the game-side plugin, even though it is
    not currently free for a new connection.
    """

    result: dict[str, Any] = {"name": EQUIPMENT_PIPE_NAME, "supported": os.name == "nt"}
    if os.name != "nt":
        result.update({"state": "unsupported", "message": "Windows 명명된 파이프 진단만 지원합니다"})
        return result
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        wait_named_pipe = kernel32.WaitNamedPipeW
        wait_named_pipe.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32)
        wait_named_pipe.restype = ctypes.c_int
        if wait_named_pipe(EQUIPMENT_PIPE_NAME, 0):
            result.update({
                "state": "available",
                "message": "연결할 수 있는 장비 플러그인 명명된 파이프를 발견했습니다.",
            })
            return result
        error_code = ctypes.get_last_error()
    except OSError as exc:
        result.update({"state": "probe_error", "message": f"Windows 명명된 파이프 진단을 호출할 수 없습니다: {exc}"})
        return result

    error_name = _PIPE_ERROR_NAMES.get(error_code, f"Windows 오류 {error_code}")
    if error_code == 2:
        state = "missing"
        message = "장비 플러그인 명명된 파이프를 찾지 못했습니다: 현재 게임 프로세스가 DLL을 로드하지 않았거나, 플러그인 초기화에 실패했거나, DLL이 다른 IPC 버전을 사용합니다."
    elif error_code in {121, 231}:
        state = "busy"
        message = "장비 플러그인 명명된 파이프는 발견했지만 현재 빈 연결 인스턴스가 없습니다. 고속 장착 실행 로그와 함께 연결 시점 또는 시간 초과 문제인지 판단하세요."
    elif error_code == 5:
        state = "access_denied"
        message = "명명된 파이프 접근이 거부되었습니다: 앱과 게임이 같은 권한 수준으로 실행 중인지 확인하세요."
    else:
        state = "error"
        message = "명명된 파이프 탐지에 실패했습니다. 오류 코드와 전체 진단을 함께 제보해 주세요."
    result.update({"state": state, "error_code": error_code, "error_name": error_name, "message": message})
    return result


def collect_dwmapi_diagnostics(
    *,
    game_executable_path: str | Path,
    application_root: str | Path,
    recorded_deployed_sha256: str = "",
    recorded_workspace_path: str | Path = "",
    loading_method: str = "proxy",
    loader_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect file/deployment/pipe facts required to debug fast apply.

    This intentionally does not call any ``equipment.*`` RPC and does not load,
    copy, replace, or delete any DLL.
    """

    result: dict[str, Any] = {
        "ok": False,
        "game_executable_input": str(game_executable_path or ""),
        "expected_game_executable": GAME_EXECUTABLE_NAME,
        "pipe": probe_equipment_pipe(),
        "loading_method": (
            "loader" if str(loading_method).strip().casefold() == "loader" else "proxy"
        ),
        "msvc_runtime": probe_mod_plugin_msvc_runtime(),
        "loader": dict(loader_snapshot or {}),
    }
    if not result["loader"]:
        try:
            loader = packaged_mod_loader(application_root)
            result["loader"] = {
                "phase": "stopped",
                "loader_path": str(loader),
                "loader_present": True,
            }
        except ModLoaderRuntimeError as exc:
            result["loader"] = {
                "phase": "missing_loader",
                "loader_present": False,
                "detail": str(exc),
            }
    try:
        executable = game_executable(game_executable_path)
        bundled = packaged_plugin_dll(application_root)
        bundled_workspace = packaged_mod_workspace(application_root)
    except EquipmentPluginDeploymentError as exc:
        result["error"] = str(exc)
        return result

    registered_workspace = registered_mod_workspace()
    target = executable.parent / PLUGIN_FILENAME
    bundled_info = _file_details(bundled)
    target_info = _file_details(target)
    registered_workspace_ready = bool(
        registered_workspace
        and all(
            (registered_workspace / relative).is_file()
            for relative in MOD_WORKSPACE_FILES
        )
    )
    sdk_cache: dict[str, Any] = {}
    if registered_workspace:
        sdk_cache = {
            relative.name: _file_details(registered_workspace / relative)
            for relative in MOD_SDK_CACHE_FILES
        }
    recorded_workspace = (
        Path(recorded_workspace_path).expanduser()
        if str(recorded_workspace_path or "").strip()
        else None
    )
    result.update({
        "ok": True,
        "game_executable": str(executable),
        "target_plugin": target_info,
        "bundled_plugin": bundled_info,
        "target_plugin_is_mods": bool(target.is_file() and is_mods_plugin_dll(target)),
        "bundled_plugin_is_mods": is_mods_plugin_dll(bundled),
        "bundled_workspace": str(bundled_workspace),
        "registered_workspace": str(registered_workspace or ""),
        "recorded_workspace": str(recorded_workspace or ""),
        "registered_workspace_ready": registered_workspace_ready,
        "registered_workspace_sdk_cache": sdk_cache,
        "registered_workspace_matches_record": bool(
            registered_workspace
            and recorded_workspace
            and registered_workspace.resolve() == recorded_workspace.resolve()
        ),
        "configured_deployed_sha256": str(recorded_deployed_sha256 or "").strip().lower(),
    })
    target_hash = str(target_info.get("sha256") or "")
    bundled_hash = str(bundled_info.get("sha256") or "")
    configured_hash = str(recorded_deployed_sha256 or "").strip().lower()
    result["target_matches_bundled"] = bool(target_hash and target_hash == bundled_hash)
    result["target_matches_recorded_deployment"] = bool(
        target_hash and configured_hash and target_hash == configured_hash
    )
    return result


def format_dwmapi_diagnostics(result: Mapping[str, Any]) -> str:
    """Format a copyable diagnostic report without leaking unrelated settings."""

    lines = [
        "NTE Drive Calc · Mods 플러그인 로드 진단",
        "로드 방식:" + (
            "Mod Loader (예비)"
            if result.get("loading_method") == "loader"
            else "프록시 DLL (권장)"
        ),
    ]
    if not result.get("ok"):
        lines.extend(["", "검사 실패", f"원인: {result.get('error', '未选择有效的 HTGame.exe')}"])
    else:
        target = result.get("target_plugin") if isinstance(result.get("target_plugin"), Mapping) else {}
        bundled = result.get("bundled_plugin") if isinstance(result.get("bundled_plugin"), Mapping) else {}
        lines.extend([
            f"게임 실행 파일: {result.get('game_executable', '未知')}",
            f"게임 디렉터리 dwmapi.dll: {'存在' if target.get('exists') else '缺失'}",
            f"게임 디렉터리 SHA-256: {target.get('sha256', '无')}",
            f"패키지 DLL SHA-256: {bundled.get('sha256', '无')}",
            f"게임 디렉터리 DLL과 패키지 DLL: {'一致' if result.get('target_matches_bundled') else '不一致或无法读取'}",
            f"게임 디렉터리 DLL 유형: {'新版 nte-mods-plugin' if result.get('target_plugin_is_mods') else '缺失、旧版或非本插件'}",
            f"패키지 Mod 작업 공간: {result.get('bundled_workspace', '无')}",
            f"등록된 Mod 작업 공간: {result.get('registered_workspace') or '无'}",
            f"등록된 작업 공간 무결성: {'就绪' if result.get('registered_workspace_ready') else '文件不完整或未注册'}",
        ])
        if result.get("recorded_workspace"):
            lines.append(
                "등록된 작업 공간과 이 프로그램의 배포 기록:"
                + ("일치" if result.get("registered_workspace_matches_record") else "불일치")
            )
        sdk_cache = (
            result.get("registered_workspace_sdk_cache")
            if isinstance(result.get("registered_workspace_sdk_cache"), Mapping)
            else {}
        )
        sdk_binary = sdk_cache.get("NTE_SDK.bin")
        sdk_checksum = sdk_cache.get("NTE_SDK.checksum")
        sdk_binary_exists = isinstance(sdk_binary, Mapping) and bool(sdk_binary.get("exists"))
        sdk_checksum_exists = isinstance(sdk_checksum, Mapping) and bool(sdk_checksum.get("exists"))
        lines.extend([
            "런타임 SDK 캐시:" + ("생성됨" if sdk_binary_exists else "아직 생성되지 않음"),
            "SDK 검증 기록:" + ("있음" if sdk_checksum_exists else "아직 생성되지 않음"),
        ])
        configured_hash = str(result.get("configured_deployed_sha256") or "")
        if configured_hash:
            lines.append(
                "게임 디렉터리 DLL과 이 프로그램의 배포 기록:"
                + ("일치" if result.get("target_matches_recorded_deployment") else "불일치")
            )

    pipe = result.get("pipe") if isinstance(result.get("pipe"), Mapping) else {}
    loader = result.get("loader") if isinstance(result.get("loader"), Mapping) else {}
    runtime = (
        result.get("msvc_runtime")
        if isinstance(result.get("msvc_runtime"), Mapping)
        else {}
    )
    runtime_files = runtime.get("files") if isinstance(runtime.get("files"), Mapping) else {}
    lines.extend([
        "",
        "Mod Loader",
        f"상태: {loader.get('phase', '未知')}",
        f"파일: {loader.get('loader_path') or '未找到'}",
    ])
    if loader.get("detail"):
        lines.append(f"설명: {loader['detail']}")
    lines.extend([
        "",
        "Microsoft Visual C++ 런타임",
        f"상태: {'就绪' if runtime.get('ready') else '缺失或无法确认'}",
    ])
    for name in MSVC_RUNTIME_FILES:
        lines.append(f"{name}: {'存在' if runtime_files.get(name) else '缺失'}")
    lines.extend([
        "",
        "명명된 파이프 검사",
        f"파이프: {pipe.get('name', EQUIPMENT_PIPE_NAME)}",
        f"상태: {pipe.get('state', '未知')}",
        f"설명: {pipe.get('message', '无')}",
    ])
    if pipe.get("error_name"):
        lines.append(f"시스템 결과: {pipe['error_name']}")
    lines.append(
        "\n설명: 새 버전 플러그인은 현재 게임 이미지에 맞는 NTE_SDK.bin을 자동 생성하고 검증 기록이 일치하면 재사용합니다."
        "이 진단은 장비 작업을 실행하지 않습니다. 파이프 “있음”은 게임 내 플러그인이 IPC를 열었다는 뜻일 뿐 실제 장착 결과를 대신하지 않습니다."
    )
    return "\n".join(lines)
