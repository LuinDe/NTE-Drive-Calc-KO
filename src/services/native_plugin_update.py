# 在宿主身份不变时显式更新本方插件，确认卸载与新映像身份后才报告完成。
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile

from src.integrations.native_host_control import NativeHostClient, NativeHostError, NativeHostOutcomeUnknown
from src.integrations.native_plugin_bundle import HOT_PLUGIN_LAYOUT, HOT_PLUGIN_LAYOUTS, inspect_native_plugin_bundle
from src.integrations.operation_guard import require_operation
from src.services.native_plugin_deployment import _digest, _target, _replace_file


USER = 'NTE_PluginUser.dll'
COMBAT = 'NTE_PluginCombat.dll'


@dataclass(frozen=True)
class NativePluginUpdateResult:
    state: str
    detail: str
    managed_files: dict[str, str]
    layout: str = HOT_PLUGIN_LAYOUT


def update_native_plugins(*, application_root: Path, directory: Path, game_pid: int,
                          operation_guard, client=None) -> NativePluginUpdateResult:
    """A synchronous worker operation; caller must stop its native session first.

    This never terminates the game or an active capture. The resident host also
    rejects active pipe users. New loading is guarded separately from teardown.
    """
    def allowed():
        require_operation(operation_guard, 'native_load')

    allowed()
    root, directory = application_root.resolve(), directory.resolve()
    bundle = inspect_native_plugin_bundle(root)
    if not bundle.ready or bundle.layout not in HOT_PLUGIN_LAYOUTS:
        raise NativeHostError('현재 전체 패키지는 플러그인 핫 업데이트를 지원하지 않습니다.')
    paths = bundle.deployment_paths
    plugins = (USER, COMBAT) + (('NTE_PluginHUD.dll',) if 'hud_plugin' in paths else ()) + (('NTE_PluginPerformance.dll',) if 'performance_plugin' in paths else ())
    host = _target(directory, paths['host'])
    if not host.is_file() or _digest(host) != bundle.files[bundle.roles['host']]:
        return NativePluginUpdateResult('restart_required', 'D3D 호스트를 업데이트해야 합니다. 게임을 종료한 뒤 전체 세트를 함께 배포하세요.', {}, bundle.layout)
    control = client or NativeHostClient(root, game_pid)
    description = control.call('describe')
    if description.get('mode') != 'calc' or description.get('pluginPolicy') != 'calc-publisher-rsa3072-sha256-v1':
        raise NativeHostError('실제로 상주 중인 호스트가 이 프로그램의 제한된 플러그인 호스트가 아닙니다.')
    status = control.call('status')
    if not isinstance(status.get('runtimeDirectory'), str) or Path(status['runtimeDirectory']).resolve() != directory:
        raise NativeHostError('선택한 배포 디렉터리가 실제 호스트 디렉터리와 달라 파일을 수정하지 않았습니다.')
    before = control.call('list')
    if not isinstance(before, list) or {m.get('file') for m in before} != set(plugins) or any(
        m.get('state') != 'loaded' for m in before
    ):
        raise NativeHostError('플러그인이 완전하고 안정적인 로드 상태가 아닙니다. 먼저 컴포넌트를 복구하세요.')
    targets = {relative: _target(directory, relative) for role, relative in paths.items() if role != 'host'}
    expected = {relative: bundle.files[bundle.roles[role]] for role, relative in paths.items() if role != 'host'}
    original = {relative: _digest(path) for relative, path in targets.items()}
    # Disk files and resident images must agree before rollback bytes are accepted.
    if any(m.get('sha256') != original['plugins/' + m['file']] for m in before):
        raise NativeHostError('디스크의 플러그인이 상주 이미지와 달라 이번 업데이트의 롤백을 준비할 수 없습니다.')
    if original == expected:
        return NativePluginUpdateResult('current', '실제 상주 플러그인이 이미 현재 전체 패키지와 일치합니다.', {**original, 'd3d12.dll': _digest(host)}, bundle.layout)
    # A rollback is a production transaction, not a source backup. Keep it only
    # when outcome is unknown; normal completion removes the temporary directory.
    staging = Path(tempfile.mkdtemp(prefix='.nte-update-', dir=directory))
    written = {}
    stopped = []
    preserve_transaction = False
    try:
        for relative, target in targets.items():
            backup = staging / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            if _digest(backup) != original[relative]:
                raise NativeHostError('동결 중에 기존 컴포넌트가 변경되어 업데이트를 시작하지 않았습니다.')
        for plugin in reversed(plugins):
            allowed()
            control.change('disable', plugin)
            stopped.append(plugin)
        for role, relative in paths.items():
            if role == 'host':
                continue
            _replace_file(root / bundle.roles[role], targets[relative], expected[relative], allowed,
                          suffix='.new', expected_target=original[relative])
            written[relative] = expected[relative]
        for plugin in plugins:
            allowed()
            control.change('enable', plugin)
            stopped.remove(plugin)
        loaded = control.call('list')
        if {m.get('file'): (m.get('state'), m.get('sha256')) for m in loaded} != {
            plugin: ('loaded', expected['plugins/' + plugin]) for plugin in plugins
        }:
            raise NativeHostOutcomeUnknown('새 플러그인의 상주 이미지를 아직 확인하지 못해 업데이트 성공으로 보고할 수 없습니다.')
        return NativePluginUpdateResult('updated', '플러그인을 업데이트하고 상주 이미지를 확인했습니다; 동기화와 전투 리포트는 다시 연결해야 합니다.',
                                        {**expected, 'd3d12.dll': _digest(host)}, bundle.layout)
    except NativeHostOutcomeUnknown:
        # An in-flight unload/load must never be followed by an inferred rollback.
        preserve_transaction = True
        raise
    except Exception as error:
        try:
            # Restore only while both consumers are confirmed absent. Init failure
            # may retain an unload_pending image, so check the host first.
            modules = control.call('list')
            if written and any(m.get('state') != 'unloaded' for m in modules):
                raise NativeHostOutcomeUnknown('업데이트에 실패했고 상주 이미지가 아직 남아 있습니다. 게임을 종료한 뒤 복구해야 합니다.')
            for relative in reversed(tuple(written)):
                _replace_file(staging / relative, targets[relative], original[relative], allowed,
                              suffix='.restore', expected_target=written[relative])
            for plugin in plugins:
                if plugin in stopped:
                    allowed()
                    control.change('enable', plugin)
            written.clear()
        except Exception as recovery_error:
            preserve_transaction = True
            raise NativeHostOutcomeUnknown(f'업데이트가 완료되지 않았고 롤백도 확인되지 않았습니다. 트랜잭션 디렉터리를 보존했습니다: {staging}') from recovery_error
        raise NativeHostError('업데이트가 완료되지 않았습니다; 이전에 확인된 컴포넌트 상태로 복원했습니다. ' + str(error)) from error
    finally:
        # Only remove this exact, locally-created transaction after confirmed completion.
        if not preserve_transaction:
            if not staging.resolve().is_relative_to(directory) or not staging.name.startswith('.nte-update-'):
                raise NativeHostError('트랜잭션 디렉터리의 식별 정보가 바뀌어 정리하지 않았습니다.')
            shutil.rmtree(staging)
