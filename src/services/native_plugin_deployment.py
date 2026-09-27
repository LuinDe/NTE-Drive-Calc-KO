# 按正式布局直接替换原生采集组件，不保留旧文件备份，按固定文件名清理。
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Callable

from src.integrations.native_plugin_bundle import (
    NATIVE_PLUGIN_DEPLOYMENT_PATHS, inspect_native_plugin_bundle,
)
from src.integrations.operation_guard import require_operation
from src.integrations.legacy_game_proxy import remove_legacy_game_proxy
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    GAME_EXECUTABLE_NAME, game_executable, game_process_running,
)


@dataclass(frozen=True)
class NativePluginDeployment:
    game_executable: Path
    target_path: Path
    deployed_sha256: str
    workspace_path: Path
    backup_path: Path | None
    managed_files: dict[str, str]
    loading_method: str = 'native-capture'
    deployment_layout: str = 'native-capture-v1'


class PluginDeploymentPendingCleanup(EquipmentPluginDeploymentError):
    """Persist partially deployed native files for cleanup after the game exits."""
    def __init__(self, message: str, *, deployment: NativePluginDeployment):
        super().__init__(message)
        self.deployment = deployment


@dataclass(frozen=True)
class NativePluginCleanupResult:
    status: str
    detail: str


@dataclass(frozen=True)
class NativeComponentFilesDeployment:
    directory: Path
    backup_path: Path | None
    managed_files: dict[str, str]


class NativeComponentFilesPendingCleanup(EquipmentPluginDeploymentError):
    def __init__(self, message: str, *, deployment: NativeComponentFilesDeployment):
        super().__init__(message)
        self.deployment = deployment


def _digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _target(directory: Path, relative: str) -> Path:
    if relative not in NATIVE_PLUGIN_DEPLOYMENT_PATHS.values():
        raise EquipmentPluginDeploymentError('컴포넌트 기록에 정식 레이아웃 외의 파일이 포함되어 있습니다.')
    target = directory / relative
    if target.is_symlink() or not target.resolve().is_relative_to(directory):
        raise EquipmentPluginDeploymentError('컴포넌트 대상이 관리 가능한 게임 디렉터리의 일반 파일이 아닙니다.')
    if target.exists() and not target.is_file():
        raise EquipmentPluginDeploymentError('컴포넌트 대상 위치가 일반 파일이 아닙니다.')
    return target


def _manual_cleanup_target(directory: Path, relative: str) -> Path:
    target = _target(directory, relative)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return target
    if (not stat.S_ISREG(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
        raise EquipmentPluginDeploymentError('컴포넌트 대상이 관리 가능한 게임 디렉터리의 일반 파일이 아닙니다.')
    return target


def _replace_file(source: Path, target: Path, digest: str, require_idle, *, suffix: str, expected_target: str | None) -> None:
    require_idle()
    descriptor, temporary_name = tempfile.mkstemp(prefix='.nte-deploy-', suffix=suffix, dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        if _digest(temporary) != digest:
            raise EquipmentPluginDeploymentError('컴포넌트 임시 파일 검증에 실패하여, 대상 파일을 교체하지 않았습니다.')
        require_idle()
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise EquipmentPluginDeploymentError('컴포넌트 대상의 유형이 스테이징 중에 바뀌어, 실제 파일을 덮어쓰지 않았습니다.')
        current = _digest(target) if target.exists() else None
        if current != expected_target:
            raise EquipmentPluginDeploymentError('컴포넌트 대상이 스테이징 중에 변경되어, 실제 파일을 덮어쓰지 않았습니다.')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

def deploy_native_component_files(
    *, application_root: str | Path, directory_path: str | Path,
    operation_guard: Callable[[str], None] | None,
    game_running: Callable[[], bool] | None = None,
    component_roles: tuple[str, ...] = ('capture_plugin', 'host'),
    expected_existing_files: Mapping[str, str | None] | None = None,
    cleanup_legacy_proxy: bool = False,
) -> NativeComponentFilesDeployment:
    probe = game_running or game_process_running

    def require_idle() -> None:
        require_operation(operation_guard, 'native_load')
        if probe():
            raise EquipmentPluginDeploymentError('게임이 실행 중입니다. 전체 컴포넌트 배포는 게임이 완전히 종료된 후에 진행할 수 있습니다.')

    require_idle()
    root = Path(application_root).expanduser().resolve()
    bundle = inspect_native_plugin_bundle(root)
    if not bundle.ready:
        raise EquipmentPluginDeploymentError('；'.join(bundle.issues))
    directory = Path(directory_path).expanduser().resolve()
    if (not component_roles or len(set(component_roles)) != len(component_roles)
            or any(role not in {'capture_plugin', 'host'} for role in component_roles)):
        raise EquipmentPluginDeploymentError('배포 요청에 잘못된 수집 컴포넌트 역할이 포함되어 있습니다.')
    order = tuple(role for role in ('capture_plugin', 'host') if role in component_roles)
    sources, targets, expected = {}, {}, {}
    for role in order:
        relative = NATIVE_PLUGIN_DEPLOYMENT_PATHS[role]
        source = root / bundle.roles[role]
        target = _target(directory, relative)
        if source.resolve() == target.resolve():
            raise EquipmentPluginDeploymentError('동봉 컴포넌트가 게임 배포 위치와 동일하여 배포 트랜잭션을 만들 수 없습니다.')
        sources[relative], targets[relative] = source, target
        expected[relative] = bundle.files[bundle.roles[role]]
    if expected_existing_files is not None:
        if set(expected_existing_files) != set(targets):
            raise EquipmentPluginDeploymentError('자동 배포에 완전한 대상 파일 대조 기록이 없습니다.')
        for relative, target in targets.items():
            previous = _digest(target) if target.exists() else None
            if previous != expected_existing_files[relative]:
                raise EquipmentPluginDeploymentError('컴포넌트 대상이 자동 검사 후 변경되어, 실제 파일을 덮어쓰지 않았습니다.')
    require_idle()
    originals: dict[str, str | None] = {}
    written: dict[str, str] = {}

    def result() -> NativeComponentFilesDeployment:
        return NativeComponentFilesDeployment(directory, None, dict(written))

    try:
        for relative, source in sources.items():
            require_idle()
            if _digest(source) != expected[relative]:
                raise EquipmentPluginDeploymentError('동봉 컴포넌트가 배포 전에 변경되어 배포를 중지했습니다.')
            target = targets[relative]
            previous = _digest(target) if target.exists() else None
            if expected_existing_files is not None and previous != expected_existing_files[relative]:
                raise EquipmentPluginDeploymentError('컴포넌트 대상이 자동 검사 후 변경되어, 실제 파일을 덮어쓰지 않았습니다.')
            originals[relative] = previous
        if cleanup_legacy_proxy and 'host' in order:
            remove_legacy_game_proxy(game_directory=directory, require_idle=require_idle)
        for relative, target in targets.items():
            require_idle()
            target = _target(directory, relative)
            previous = originals[relative]
            if (_digest(target) if target.exists() else None) != previous:
                raise EquipmentPluginDeploymentError('게임 폴더의 컴포넌트가 배포 전에 변경되어 배포를 중단했습니다.')
            target.parent.mkdir(parents=True, exist_ok=True)
            _replace_file(sources[relative], target, expected[relative], require_idle, suffix='.new', expected_target=previous)
            written[relative] = expected[relative]
            if _digest(target) != expected[relative]:
                raise EquipmentPluginDeploymentError('컴포넌트 쓰기 후 검증에 실패했습니다.')
        require_idle()
        return result()
    except Exception as error:
        if written:
            try:
                require_idle()
                for relative in reversed(tuple(written)):
                    require_idle()
                    target = _target(directory, relative)
                    if not target.is_file() or _digest(target) != written[relative]:
                        raise EquipmentPluginDeploymentError('이번에 기록할 컴포넌트가 이미 변경되어 기존 파일을 덮어쓰지 않았습니다.')
                    require_idle()
                    target.unlink()
                    written.pop(relative)
            except Exception as rollback_error:
                raise NativeComponentFilesPendingCleanup(
                    '컴포넌트 배포가 완료되지 않았습니다; 이번에 실제로 기록한 내역은 보존했으며, 게임 종료 후 정리를 기다립니다.',
                    deployment=result(),
                ) from rollback_error
        if isinstance(error, (EquipmentPluginDeploymentError, PermissionError)):
            raise
        raise EquipmentPluginDeploymentError('네이티브 컴포넌트 배포에 실패하여 이번에 기록한 새 컴포넌트를 제거했습니다; 이전 컴포넌트는 복원하지 않았으니 다시 배포하세요.') from error


def deploy_native_plugin(
    *, application_root: str | Path, game_executable_path: str | Path,
    operation_guard: Callable[[str], None] | None,
    game_running: Callable[[], bool] | None = None,
    expected_existing_files: Mapping[str, str | None] | None = None,
    cleanup_legacy_proxy: bool = False,
) -> NativePluginDeployment:
    require_operation(operation_guard, 'native_load')
    executable = game_executable(game_executable_path)

    def wrap(record: NativeComponentFilesDeployment) -> NativePluginDeployment:
        return NativePluginDeployment(
            executable, record.directory / NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'],
            record.managed_files.get(NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'], ''),
            record.directory, record.backup_path, dict(record.managed_files),
        )

    try:
        return wrap(deploy_native_component_files(
            application_root=application_root, directory_path=executable.parent,
            operation_guard=operation_guard,
            game_running=game_running,
            expected_existing_files=expected_existing_files,
            cleanup_legacy_proxy=cleanup_legacy_proxy,
        ))
    except NativeComponentFilesPendingCleanup as error:
        raise PluginDeploymentPendingCleanup(str(error), deployment=wrap(error.deployment)) from error


def cleanup_native_component_files(
    *, directory_path: str | Path, managed_files: dict[str, str],
    game_running: Callable[[], bool] | None = None,
) -> NativePluginCleanupResult:
    probe = game_running or game_process_running
    if probe():
        return NativePluginCleanupResult('waiting_game_exit', '게임이 종료되지 않아 지금은 컴포넌트를 정리할 수 없습니다. 게임을 완전히 종료한 후 다시 검사하세요.')
    directory = Path(directory_path).expanduser()
    if not directory.is_absolute():
        raise EquipmentPluginDeploymentError('컴포넌트 정리 디렉터리는 기록된 절대 경로여야 합니다.')
    directory = directory.resolve()
    files = dict(managed_files)
    try:
        # Upgrades may replace a managed DLL without updating an older ownership record.
        # Validate the fixed filenames and paths, not the historical contents.
        for relative in files:
            _target(directory, relative)
        # Remove the automatic loading entry first; never restore transaction backups.
        ordered = sorted(files, key=lambda relative: relative != NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'])
        for relative in ordered:
            if probe():
                return NativePluginCleanupResult('waiting_game_exit', '정리 도중 게임이 시작되어 남은 컴포넌트가 아직 정리되지 않았습니다. 게임을 완전히 종료한 후 다시 검사하세요.')
            target = _target(directory, relative)
            if target.exists():
                target.unlink()
    except EquipmentPluginDeploymentError as error:
        return NativePluginCleanupResult('conflict', str(error))
    except OSError as error:
        raise EquipmentPluginDeploymentError('기록된 컴포넌트를 정리할 수 없습니다. 게임을 종료한 상태로 다시 시도하세요.') from error
    if probe():
        return NativePluginCleanupResult('waiting_game_exit', '컴포넌트 파일은 정리되었지만, 이미 로드된 세션을 끝내려면 게임을 종료해야 합니다.')
    return NativePluginCleanupResult('cleaned', '고정 파일명 기준으로 이 프로그램이 기록한 네이티브 컴포넌트를 정리했습니다.')


def cleanup_native_plugin(
    *, game_executable_path: str | Path, managed_files: dict[str, str],
    game_running: Callable[[], bool] | None = None,
) -> NativePluginCleanupResult:
    executable = Path(str(game_executable_path).strip().strip('"')).expanduser()
    if not executable.is_absolute() or executable.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError('정리 기록에 있는 게임 실행 파일 경로가 유효하지 않습니다.')
    return cleanup_native_component_files(directory_path=executable.parent,
                                          managed_files=managed_files, game_running=game_running)


def cleanup_manual_native_plugin(
    *, application_root: str | Path, game_executable_path: str | Path,
    managed_files: dict[str, str], game_running: Callable[[], bool] | None = None,
) -> NativePluginCleanupResult:
    """Explicit cleanup may adopt only bundled or reviewed predecessor DLLs."""
    probe = game_running or game_process_running
    if probe():
        return NativePluginCleanupResult('waiting_game_exit', '게임이 종료되지 않아 지금은 컴포넌트를 정리할 수 없습니다. 게임을 완전히 종료한 후 다시 검사하세요.')
    executable = Path(str(game_executable_path).strip().strip('"')).expanduser()
    if not executable.is_absolute() or executable.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError('정리 기록에 있는 게임 실행 파일 경로가 유효하지 않습니다.')
    directory = executable.parent.resolve()
    recorded = dict(managed_files)
    bundle = inspect_native_plugin_bundle(application_root)
    allowed: dict[str, set[str]] = {}
    if bundle.ready:
        for role, relative in NATIVE_PLUGIN_DEPLOYMENT_PATHS.items():
            allowed[relative] = {
                bundle.files[bundle.roles[role]], *bundle.upgrade_from.get(relative, ()),
            }
    observed: dict[str, str] = {}
    try:
        for relative in recorded:
            _manual_cleanup_target(directory, relative)
        for relative in NATIVE_PLUGIN_DEPLOYMENT_PATHS.values():
            target = _manual_cleanup_target(directory, relative)
            if not target.exists():
                continue
            digest = _digest(target)
            if relative not in recorded and digest not in allowed.get(relative, set()):
                return NativePluginCleanupResult(
                    'conflict', f'게임 폴더에 있는 {relative}의 출처가 확인되지 않아 파일을 남겨 두었습니다; 컴포넌트의 소속을 확인하세요.',
                )
            observed[relative] = digest
        ordered = sorted(observed, key=lambda relative: relative != NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'])
        for relative in ordered:
            if probe():
                return NativePluginCleanupResult('waiting_game_exit', '정리 도중 게임이 시작되어 남은 컴포넌트가 아직 정리되지 않았습니다. 게임을 완전히 종료한 후 다시 검사하세요.')
            target = _manual_cleanup_target(directory, relative)
            if not target.exists():
                continue
            if _digest(target) != observed[relative]:
                return NativePluginCleanupResult('conflict', f'{relative}이(가) 대조 후 변경되어 남은 구성 요소는 유지했습니다.')
            target.unlink()
    except EquipmentPluginDeploymentError as error:
        return NativePluginCleanupResult('conflict', str(error))
    except OSError as error:
        raise EquipmentPluginDeploymentError('게임 디렉터리의 네이티브 컴포넌트를 정리할 수 없습니다. 게임을 종료한 상태로 다시 시도하세요.') from error
    if probe():
        return NativePluginCleanupResult('waiting_game_exit', '컴포넌트 파일은 정리되었지만, 이미 로드된 세션을 끝내려면 게임을 종료해야 합니다.')
    return NativePluginCleanupResult('cleaned', '배포 기록이 있거나 전체 패키지 해시로 확인된 게임 디렉터리의 네이티브 컴포넌트를 정리했습니다.')
