# 在独立运行目录仅准备无界面采集 DLL，复用正式文件事务并按记录核对清理。
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from src.integrations.native_plugin_bundle import NATIVE_PLUGIN_DEPLOYMENT_PATHS, inspect_native_plugin_bundle
from src.services.native_plugin_deployment import NativeComponentFilesDeployment, deploy_native_component_files
from src.integrations.operation_guard import require_operation
from src.services.equipment_plugin_deployment import EquipmentPluginDeploymentError, game_process_running

NATIVE_LOADER_COMPONENT_ROLES = ('capture_plugin',)
NATIVE_LOADER_PAYLOAD_RELATIVE_PATH = NATIVE_PLUGIN_DEPLOYMENT_PATHS['capture_plugin']


@dataclass(frozen=True)
class NativeLoaderWorkspaceInspection:
    workspace_path: Path
    managed_files: dict[str, str]
    issues: tuple[str, ...]

    @property
    def files_compatible(self) -> bool:
        return not self.issues


def inspect_native_loader_workspace(*, application_root, workspace_path) -> NativeLoaderWorkspaceInspection:
    root, directory = Path(application_root).resolve(), Path(workspace_path).resolve()
    bundle = inspect_native_plugin_bundle(root)
    if not bundle.ready:
        return NativeLoaderWorkspaceInspection(directory, {}, tuple(bundle.issues))
    expected = {NATIVE_PLUGIN_DEPLOYMENT_PATHS[role]: bundle.files[bundle.roles[role]]
                for role in NATIVE_LOADER_COMPONENT_ROLES}
    issues = []
    for relative, digest in expected.items():
        target = directory / relative
        try:
            if target.is_symlink() or not target.resolve().is_relative_to(directory) or not target.is_file():
                issues.append(f'Loader 실행 디렉터리에 아직 준비되지 않은 구성 요소: {relative}')
                continue
            with target.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                    issues.append(f'Loader 실행 디렉터리 구성 요소 불일치: {relative}')
        except OSError:
            issues.append(f'Loader 실행 디렉터리 컴포넌트를 읽을 수 없습니다: {relative}')
    return NativeLoaderWorkspaceInspection(directory, expected, tuple(issues))


def prepare_native_loader_workspace(*, application_root, workspace_path, operation_guard, game_running):
    require_operation(operation_guard, 'native_load')
    if (game_running or game_process_running)():
        raise EquipmentPluginDeploymentError('게임이 종료되지 않아 지금은 Loader 실행 컴포넌트를 업데이트할 수 없습니다. 게임을 완전히 종료한 후 다시 시도하세요.')
    current = inspect_native_loader_workspace(application_root=application_root, workspace_path=workspace_path)
    if current.files_compatible:
        return NativeComponentFilesDeployment(current.workspace_path, None, current.managed_files)
    return deploy_native_component_files(
        application_root=application_root, directory_path=workspace_path,
        operation_guard=operation_guard, game_running=game_running,
        component_roles=NATIVE_LOADER_COMPONENT_ROLES,
    )
