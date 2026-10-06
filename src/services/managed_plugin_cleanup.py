# 默认只清理拥有的加载登记，手动授权时额外清理旧代理文件。
"""Managed-file lifecycle facts; never infer pipe or business readiness."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    GAME_EXECUTABLE_NAME,
    PLUGIN_FILENAME,
    cleanup_mod_workspace,
    game_process_running,
    mod_workspace_registry_snapshot,
)
from src.integrations.legacy_game_proxy import ko_is_foreign_proxy as _ko_is_foreign_proxy


@dataclass(frozen=True)
class ManagedPluginInspection:
    target_path: Path
    dll_state: Literal["missing", "managed", "conflict", "unmanaged"]
    registry_state: Literal["absent", "owned", "conflict"]
    registered_workspace: str | None
    game_running: bool | None


@dataclass(frozen=True)
class ManagedPluginCleanupResult:
    status: Literal["cleaned", "waiting_game_exit", "conflict"]
    inspection: ManagedPluginInspection
    detail: str


def _target_path(game_executable_path: str | Path) -> Path:
    # A removed game executable must not prevent cleaning its owned proxy.
    executable = Path(str(game_executable_path).strip().strip('"')).expanduser()
    if not executable.is_absolute() or executable.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError("정리 기록에 있는 게임 실행 파일 경로가 유효하지 않습니다.")
    parent = executable.parent.resolve()
    return parent / PLUGIN_FILENAME


def inspect_managed_plugin(
    *,
    game_executable_path: str | Path,
    mod_workspace_path: str | Path | None = None,
    game_running: Callable[[], bool | None] | None = None,
    inspect_legacy_proxy: bool = True,
) -> ManagedPluginInspection:
    """Inspect the game-local legacy filename and registration without hashing."""
    target = _target_path(game_executable_path)
    process_running = (game_running or game_process_running)()
    if not inspect_legacy_proxy:
        dll_state = "unmanaged"
    elif target.is_symlink() or (target.exists() and not target.is_file()):
        dll_state = "conflict"
    elif not target.exists():
        dll_state = "missing"
    else:
        dll_state = "managed"
    registered, current = mod_workspace_registry_snapshot()
    if not registered:
        registry_state = "absent"
    elif mod_workspace_path and current and (
        Path(current).expanduser().resolve() == Path(mod_workspace_path).expanduser().resolve()
    ):
        registry_state = "owned"
    else:
        registry_state = "conflict"
    return ManagedPluginInspection(target, dll_state, registry_state, current, process_running)


def cleanup_managed_plugin(
    *,
    game_executable_path: str | Path,
    mod_workspace_path: str | Path | None = None,
    game_running: Callable[[], bool | None] | None = None,
    allow_unrecorded_workspace_adoption: bool = False,
    cleanup_legacy_proxy: bool = False,
) -> ManagedPluginCleanupResult:
    """Clean owned registration; remove dwmapi.dll only for an explicit manual action."""
    probe = game_running or game_process_running
    facts = inspect_managed_plugin(
        game_executable_path=game_executable_path,
        mod_workspace_path=mod_workspace_path, game_running=probe,
        inspect_legacy_proxy=cleanup_legacy_proxy,
    )
    if facts.game_running:
        return ManagedPluginCleanupResult(
            "waiting_game_exit", facts, "게임이 종료되지 않아 지금은 컴포넌트를 정리할 수 없습니다. 게임을 완전히 종료한 후 다시 검사하세요.",
        )
    if facts.dll_state == "conflict":
        return ManagedPluginCleanupResult(
            "conflict", facts, "컴포넌트 경로가 일반 파일이 아닙니다: dwmapi.dll. 게임 디렉터리에 같은 이름의 디렉터리나 링크가 있는지 확인하세요.",
        )
    cleanup_workspace = mod_workspace_path
    if facts.registry_state == "conflict":
        if (
            not allow_unrecorded_workspace_adoption
            or bool(mod_workspace_path)
            or not facts.registered_workspace
        ):
            return ManagedPluginCleanupResult(
                "conflict", facts, "로드 설정이 배포 기록과 일치하지 않아 정리하지 않았습니다. 현재 등록된 Mod 작업 영역을 확인하세요.",
            )
        # The explicit cleanup/deploy action adopts only this application's exact
        # legacy registry value. cleanup_mod_workspace rechecks it before deletion.
        cleanup_workspace = facts.registered_workspace
    if probe():
        return ManagedPluginCleanupResult("waiting_game_exit", facts, "정리 전에 게임이 시작되었습니다. 게임을 완전히 종료한 후 다시 검사하세요.")
    kept_foreign = False
    if facts.dll_state == "managed":
        try:
            if facts.target_path.is_symlink() or (
                facts.target_path.exists() and not facts.target_path.is_file()
            ):
                return ManagedPluginCleanupResult("conflict", facts, "컴포넌트 파일이 변경되었습니다: dwmapi.dll이 정리 전에 수정되었습니다. 정리를 중지했으니, 해당 컴포넌트를 점검하세요.")
            if _ko_is_foreign_proxy(facts.target_path):
                kept_foreign = True   # KO patch: not the calculator's old proxy (e.g. UE4SS) - never delete it
            else:
                facts.target_path.unlink(missing_ok=True)
        except OSError as exc:
            raise EquipmentPluginDeploymentError("컴포넌트 정리에 실패했습니다. 게임을 닫은 상태로 유지하고 다시 검사하세요.") from exc
    if probe():
        return ManagedPluginCleanupResult("waiting_game_exit", facts, "정리 도중 게임이 시작되어 로드 설정이 아직 정리되지 않았습니다. 게임을 종료한 후 다시 검사하세요.")
    cleanup_mod_workspace(workspace_path=cleanup_workspace)
    final = inspect_managed_plugin(
        game_executable_path=game_executable_path,
        mod_workspace_path=mod_workspace_path, game_running=probe,
        inspect_legacy_proxy=cleanup_legacy_proxy,
    )
    if final.game_running:
        return ManagedPluginCleanupResult("waiting_game_exit", final, "로드 등록은 처리되었지만, 이미 로드된 세션을 끝내려면 게임을 종료해야 합니다.")
    if (cleanup_legacy_proxy and final.dll_state != "missing" and not kept_foreign) or final.registry_state != "absent":
        return ManagedPluginCleanupResult("conflict", final, "정리 후 컴포넌트 또는 로드 설정이 변경되었습니다. 다시 확인해 주세요.")
    if kept_foreign:
        return ManagedPluginCleanupResult("cleaned", final, '이 프로그램의 옛 로더 등록을 정리했습니다. 게임 폴더의 {0}은(는) 이 프로그램의 옛 로더 파일이 아니어서(UE4SS 등 다른 프로그램의 파일일 수 있음) 그대로 두었습니다.'.format('dwmapi.dll'))
    return ManagedPluginCleanupResult("cleaned", final,
        "이전 프록시와 이 프로그램이 소유한 로드 등록을 정리했습니다." if cleanup_legacy_proxy else
        "이 프로그램이 소유한 이전 로드 등록을 정리했습니다; 현재 컴포넌트가 아닌 파일은 그대로 유지됩니다.")
