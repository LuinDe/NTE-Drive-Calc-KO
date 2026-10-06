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
    NATIVE_PLUGIN_DEPLOYMENT_PATHS, HOT_PLUGIN_DEPLOYMENT_PATHS, PERFORMANCE_DEPLOYMENT_PATHS, HUD_DEPLOYMENT_PATHS, inspect_native_plugin_bundle,
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
    if relative not in {*NATIVE_PLUGIN_DEPLOYMENT_PATHS.values(), *HOT_PLUGIN_DEPLOYMENT_PATHS.values(), *PERFORMANCE_DEPLOYMENT_PATHS.values(), *HUD_DEPLOYMENT_PATHS.values()}:
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


# ── KO patch (fix_protect_foreign_231): never overwrite, move or delete a game-folder file this app did not ship
#    (e.g. ReShade installed as d3d12.dll).  Known = this release's bundle and its declared predecessors,
#    every official build of these files in upstream history, and what this app recorded when it deployed.
_KO_OFFICIAL_COMPONENT_SHA256 = {
    'd3d12.dll': frozenset({
        '15fb3dfed69aedde7efe5a645af325abff8973301fd292c85b6981b0eb5f208c', '30d79dca9e944d21c7170f327d326fce3ea4e26762948bcfd7dd37321cc58cf6',
        '4c765cd4f01277c983aff4503e3c2b8f38923e65d5ee665ca712c703789e2123', '6a714f2e633b22942f0efade16a5f0a623c435d98446d681e7182fa5683a5293',
        '7e350c0494d16a91b25c3955cb3550c8c7fc3508e3814764829dcf5be95101d2', '8974a4a3490edadb64d383ceb568d9b7f055f6f32ede61e9ef001a50d2af87d0',
        'a89e672b4f3bc953c1bfa3118a13867a4f173a7fc502a864cad2d5fc640007f4',
    }),
    'NTE_Capture.dll': frozenset({
        '00475c16d421dfb8bfbfa895aecad0368cdff233daf155b0942365847307ccd5', '03defb59e6a602f0023d91a76834ac01c931b3dd28e49e6a18a181a9a5e937d3',
        '0b09eaa90a818be9d05415a886c028a4204a6f2b8a83b134e4657ea17e056011', '0ce6ade560b6188803a0b5d9608db91c2135d3498f3b36e94513cec5b3794aa8',
        '134001d7b458520ff08f639fcffa29a57162d209680c90f7546e24094ada88e0', '13c9e06015b15f5d83499735889ae1eeb18d135de699d2d0e3034abdacd86a1b',
        '13dd55f821da73b75d134a3c209105dfcaf3d47110dcc1fb474d9eb126418a5d', '1c039f042d6d96a53775dc1288e1fd01488b829f83d2cf7d6b733419348ba04a',
        '207c80aa895613172846997b0e3dc2b0367eeaa68afbec2737242093e54576a8', '22c0ebcaa89ea2977aa67ee4b80361944de0168e95d01461060c3476f6273246',
        '25ce1309de85fb0bb559ceb52a3e26609dcf7b9cae80d99e2c6322a6df2b0156', '27b231243ed4de3f6df625af164feee68b5ecb5d2b683ddaf155700536b1cada',
        '28d3b28e59a0be040edf8c5243b0b30d1d4a57329da84f25984f29ae7c1dec0e', '2fc7611bad96cdf663d5e83831433c4dff5eff160ea2819e24016a5676f6984a',
        '3c1f5ac635b162423039fd13163bf538a1f7424be83afe9f79caf49c0068dcb6', '4086b932295633f17aa6385e543480cbc13069accf244e0f1b97e789ff4c2b94',
        '43108688302220d65d46f74e5124774a7373dbb028a8f5c285e6595e9bf1a6e6', '434784327938b1c28f45d864d3d64f828b7ef84627399d08a6784e08c5937487',
        '44bd5896d32eef8414813af58be50f2447b0f88cadf9c0d2b5122ab487af984b', '468181c4591a02978446b5df9cd29126424e9ee3b6d0fb45e0a95895f85511fd',
        '482ac407151ce48d0f6fe3ed1fb63ce74695b2edda0e161f078cf709a9de01a1', '4acc370fd7c40ae9bfc5d7d63bba2b2610dc969035d3918da2f3ba582db662f1',
        '4e0dead50988a3f84fb080ae7126e42c3375fb1e8edfbf3067b904602e866c86', '5939882606423fa40fffa5b39156c49c3404ea8e42798da293a1481935f5cd26',
        '5cd66a19147e510c65407887002219cbe0227420d416496c76e2fcf402304e56', '5fa57cbaa1b1be281d51ab5d6281ed5a83a496f64fccebb4986632a42c2951b8',
        '686f0143beb570cd6808f0ebae7b580d469b83d781b725134996c368da0bc900', '6ab58a1f57cd8ddb44ba58b67be21c07bcf8d2de192d8a0d8d630ba0ef750cc1',
        '6ff0cefbcf61c519e7734bb102b868def96d8f078f7594c9fc8bf56d3eec8fed', '74e0deef5b19c512af4cdfde8b41d6932b2a73a83d8e89a06b65c6c9f9267bae',
        '7b46bd1ddd26e68f7fb216e87ec0f2afa96e6dc8369b93a48f8e8867b667fe00', '7f8cb53ea24d6147bcba9683f246c8a0eaf721af557c8ff59ab6d6575f8e11fb',
        '83c907df4153b9bab05866c15076da3df7eb0ae2363bb70ec30271d3da06e39e', '85d857ee5001f7c8d28f56b24ef458a27a756268feeb25a92f77c1932a026b22',
        '88de429915ebc973815d78a6e3519d9934b46eb80a2e73a1c6f56414373872ff', '8b8e270889828dcf38e7082c81b71231c5a7292bbcc4181ebb394a1531a010ab',
        '99d8c774456666417e36fa332d6615a0f7c6c827362d0e1454de4c9a1947987e', 'ac67a4cb8ccf03211e1331d9078629dd9fde0f9a717b9c7168b4b505c6a0c819',
        'b313b972e1881a545d76ae92d154b9ee2cbc972799f5b660cd9f0ba35cdbd355', 'b33e1126be8aa5f0e488f13db6a5dbef659e28265008fe01d1f72dae616858c4',
        'b697178931e7c50d6679578f6a792a7dfc0534b3a62599b6fdc4b8a01236ccc0', 'bf24bd32a86564b0b93fea35922642fda96336fb5da52170bd98015ed9b4baa8',
        'c6a32ad7e9e297116b4c5f4ea0a802f96ad2b669f3889de829fda6820e21fc56', 'e3711292bbeba7ce08d43cc5998383098c95b2e64b744cb97da6b8a29947330f',
        'e7fc1ffb11e72f1551324b97505c151518166df5fc027406721d522d3daf8118',
    }),
    'plugins/NTE_PluginCombat.dll': frozenset({
        '024c39726c229ababe2f180e66430936752b5e1991a1772fb321d6d505157485', '5105f4fa6042f7235aec6757c2f22559ad0690c5f950e8b8a4dddb9cf74155c9',
        'a065eb0e30cc1f51a9e205ebafa6a049f72ce04eb93ca255c86043b51851c6bd',
    }),
    'plugins/NTE_PluginCombat.dll.sig': frozenset({
        '9cc4555f55a5c1270f304a290f8b9b4ec76095e0aa6cad23fcfccf036e964e34', 'cc67a966c29950fa71d8b19a926bc80076676446898abd45eca586f534eab3f9',
        'd1cfc43b5086fcc78d8e9d55aed7649480762ddd374423e1f0063ba674e5667d',
    }),
    'plugins/NTE_PluginHUD.dll': frozenset({
        '3b44220d87b77e618281aec5124476d65c54ad05a9162a34719d172c9c543b68', '4520e4918a41054890a6c72e8947540bf86369bc3c91da15acac3333049e5a6c',
        'd948727ab09aa2c572bb3225e04af474931a4b54b92cdf05e3387eb86c226cc1',
    }),
    'plugins/NTE_PluginHUD.dll.sig': frozenset({
        'ad57533f857fa79d035fef5ed5603f1ad4c1fb1105d2f012a6a6bc8f346429d8', 'e7e77c3676d839e7a267533e21f3f050f893733ba22b6a6b8d4e5686f9d69a59',
        'f9698fe348e234434f33456014dc63866b1a8c834aff37888a34fa1fe2cdee7f',
    }),
    'plugins/NTE_PluginPerformance.dll': frozenset({
        '09f906df77e3a8a333becbde5dc3814546d1338694a8bfedd7538c1f43f86be4', 'ecde84ee27434280d4479efaa89b9f2f284b96fb75652048fe0fa217c97d1dfb',
    }),
    'plugins/NTE_PluginPerformance.dll.sig': frozenset({
        '3b509fa845239676fe32ad514be04fb4ab0533009230e93310a64778bb257bf6', 'f4b1ace3f494dd1128f0e9c29511a5f1f976c5c04dd5e76b916def0eb458cf0e',
    }),
    'plugins/NTE_PluginUser.dll': frozenset({
        '1ec71b125ab94255dfb3ea947af51a8ebb72087536e2c45c1354fbc8544cd939', 'c5d323b712ad0019c7b74916dae59c041934e46141cdc4678ea4547a2beb7cae',
        'e9fa510dc59b89bbf76f441d06525c8a6ce3303c2e2fbdfcdc5ec925077a6335',
    }),
    'plugins/NTE_PluginUser.dll.sig': frozenset({
        '384aeaecc7fb0e1159449710a8fff49efec6ef47d3e38b122bd32e73192d6152', 'd164c86159a899119c76aa7a45134645fe2e1f00b4c34fc444e0d3952ae8aa1d',
        'eb3b5faf1f36c6c2e934416679db175e6ab0adc5e29d719282c201714a005ccb',
    }),
}


class ForeignGameFileError(EquipmentPluginDeploymentError):
    """A game-folder file carries a component's name but was not deployed by this app."""
    foreign_game_file = True


def _ko_known_hashes(application_root=None, recorded=None) -> dict[str, set[str]]:
    known = {relative: set(hashes) for relative, hashes in _KO_OFFICIAL_COMPONENT_SHA256.items()}
    if application_root is not None:
        try:
            bundle = inspect_native_plugin_bundle(Path(application_root).expanduser().resolve())
            for relative, hashes in bundle.upgrade_from.items():
                known.setdefault(relative, set()).update(hashes)
            for role, relative in bundle.deployment_paths.items():
                source = bundle.roles.get(role)
                if source in bundle.files:
                    known.setdefault(relative, set()).add(bundle.files[source])
        except Exception:  # an unreadable bundle only means fewer files are recognised as ours
            pass
    for relative, digest in dict(recorded or {}).items():
        if isinstance(digest, str) and digest.strip():
            known.setdefault(relative, set()).add(digest.strip().casefold())
    return known


def _replace_file(source: Path, target: Path, digest: str, require_idle, *, suffix: str, expected_target: str | None) -> None:
    require_idle()
    descriptor, temporary_name = tempfile.mkstemp(prefix='.nte-deploy-', suffix=suffix, dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    failure = None
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
    except Exception as error:
        failure = error
        if isinstance(error, OSError) and getattr(error, 'winerror', None) in {225, 226}:
            raise EquipmentPluginDeploymentError(
                f'Windows 보안이 {target.name} 배포를 차단했습니다. 시스템 보호 기록을 확인하고 컴포넌트 출처를 점검하세요.'
            ) from error
        if isinstance(error, FileNotFoundError):
            raise EquipmentPluginDeploymentError(
                f'{target.name} 배포 중 파일이 사라져 쓰기 결과를 검증할 수 없습니다. 시스템 보호 기록과 파일 사용 상태를 확인하세요.'
            ) from error
        raise
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            if failure is None:
                raise  # Keep an earlier deployment failure when cleanup is also blocked.

def deploy_native_component_files(
    *, application_root: str | Path, directory_path: str | Path,
    operation_guard: Callable[[str], None] | None,
    game_running: Callable[[], bool | None] | None = None,
    component_roles: tuple[str, ...] | None = None,
    expected_existing_files: Mapping[str, str | None] | None = None,
    cleanup_legacy_proxy: bool = False,
    protect_foreign: bool = False,
    known_files: Mapping[str, str] | None = None,
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
    paths = bundle.deployment_paths
    component_roles = tuple(paths) if component_roles is None else component_roles
    if (not component_roles or len(set(component_roles)) != len(component_roles)
            or any(role not in paths for role in component_roles)):
        raise EquipmentPluginDeploymentError('배포 요청에 잘못된 수집 컴포넌트 역할이 포함되어 있습니다.')
    order = tuple(role for role in paths if role != 'host' and role in component_roles)
    if 'host' in component_roles:
        order += ('host',)
    sources, targets, expected = {}, {}, {}
    for role in order:
        relative = paths[role]
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
        if protect_foreign:   # KO patch: refuse to overwrite a game-folder file this app did not ship
            known = _ko_known_hashes(root, known_files)   # + what this app recorded when it deployed
            foreign = [relative for relative, previous in originals.items()
                       if previous is not None and previous not in known.get(relative, ())]
            if foreign:
                raise ForeignGameFileError('게임 폴더의 {0}은(는) 이 프로그램이 배포한 컴포넌트로 확인되지 않습니다(ReShade 등 다른 프로그램이나 다른 버전 계산기의 파일일 수 있음). 덮어쓰지 않도록 배포를 중단했습니다. 로드 방식을 "네이티브 Loader (예비)"로 바꿔 주세요.'.format(', '.join(foreign)))
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
    game_running: Callable[[], bool | None] | None = None,
    expected_existing_files: Mapping[str, str | None] | None = None,
    cleanup_legacy_proxy: bool = False,
    recorded_files: Mapping[str, str] | None = None,
) -> NativePluginDeployment:
    require_operation(operation_guard, 'native_load')
    executable = game_executable(game_executable_path)
    bundle = inspect_native_plugin_bundle(application_root)

    def wrap(record: NativeComponentFilesDeployment) -> NativePluginDeployment:
        return NativePluginDeployment(
            executable, record.directory / NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'],
            record.managed_files.get(NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'], ''),
            record.directory, record.backup_path, dict(record.managed_files), deployment_layout=bundle.layout,
        )

    try:
        return wrap(deploy_native_component_files(
            application_root=application_root, directory_path=executable.parent,
            operation_guard=operation_guard,
            game_running=game_running,
            expected_existing_files=expected_existing_files,
            cleanup_legacy_proxy=cleanup_legacy_proxy,
            protect_foreign=True,
            known_files=recorded_files,
        ))
    except NativeComponentFilesPendingCleanup as error:
        raise PluginDeploymentPendingCleanup(str(error), deployment=wrap(error.deployment)) from error


def cleanup_native_component_files(
    *, directory_path: str | Path, managed_files: dict[str, str],
    game_running: Callable[[], bool | None] | None = None,
    known_hashes: Mapping | None = None,
) -> NativePluginCleanupResult:
    probe = game_running or game_process_running
    if probe():
        return NativePluginCleanupResult('waiting_game_exit', '게임이 종료되지 않아 지금은 컴포넌트를 정리할 수 없습니다. 게임을 완전히 종료한 후 다시 검사하세요.')
    directory = Path(directory_path).expanduser()
    if not directory.is_absolute():
        raise EquipmentPluginDeploymentError('컴포넌트 정리 디렉터리는 기록된 절대 경로여야 합니다.')
    directory = directory.resolve()
    files = dict(managed_files)
    kept: list[str] = []
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
                if known_hashes is not None and _digest(target) not in known_hashes.get(relative, ()):
                    kept.append(relative)   # KO patch: not ours (e.g. ReShade as d3d12.dll) - never delete it
                    continue
                target.unlink()
    except EquipmentPluginDeploymentError as error:
        return NativePluginCleanupResult('conflict', str(error))
    except OSError as error:
        raise EquipmentPluginDeploymentError('기록된 컴포넌트를 정리할 수 없습니다. 게임을 종료한 상태로 다시 시도하세요.') from error
    if probe():
        return NativePluginCleanupResult('waiting_game_exit', '컴포넌트 파일은 정리되었지만, 이미 로드된 세션을 끝내려면 게임을 종료해야 합니다.')
    if kept:
        return NativePluginCleanupResult('cleaned', '이 프로그램의 컴포넌트를 정리했습니다. 게임 폴더의 {0}은(는) 이 프로그램의 파일이 아니어서(ReShade 등 다른 프로그램의 파일일 수 있음) 그대로 두었습니다.'.format(', '.join(kept)))
    return NativePluginCleanupResult('cleaned', '고정 파일명 기준으로 이 프로그램이 기록한 네이티브 컴포넌트를 정리했습니다.')


def cleanup_native_plugin(
    *, game_executable_path: str | Path, managed_files: dict[str, str],
    game_running: Callable[[], bool | None] | None = None,
    application_root: str | Path | None = None,
) -> NativePluginCleanupResult:
    executable = Path(str(game_executable_path).strip().strip('"')).expanduser()
    if not executable.is_absolute() or executable.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError('정리 기록에 있는 게임 실행 파일 경로가 유효하지 않습니다.')
    return cleanup_native_component_files(directory_path=executable.parent,
                                          managed_files=managed_files, game_running=game_running,
                                          known_hashes=_ko_known_hashes(application_root, managed_files))


def cleanup_manual_native_plugin(
    *, application_root: str | Path, game_executable_path: str | Path,
    managed_files: dict[str, str], game_running: Callable[[], bool | None] | None = None,
) -> NativePluginCleanupResult:
    """Explicit cleanup removes fixed component filenames in the selected directory."""
    probe = game_running or game_process_running
    if probe():
        return NativePluginCleanupResult('waiting_game_exit', '게임이 종료되지 않아 지금은 컴포넌트를 정리할 수 없습니다. 게임을 완전히 종료한 후 다시 검사하세요.')
    executable = Path(str(game_executable_path).strip().strip('"')).expanduser()
    if not executable.is_absolute() or executable.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError('정리 기록에 있는 게임 실행 파일 경로가 유효하지 않습니다.')
    directory = executable.parent.resolve()
    recorded = dict(managed_files)
    observed: dict[str, str] = {}
    kept: list[str] = []
    known = _ko_known_hashes(application_root, recorded)
    try:
        for relative in recorded:
            _manual_cleanup_target(directory, relative)
        for relative in {*NATIVE_PLUGIN_DEPLOYMENT_PATHS.values(), *HOT_PLUGIN_DEPLOYMENT_PATHS.values(), *PERFORMANCE_DEPLOYMENT_PATHS.values(), *HUD_DEPLOYMENT_PATHS.values()}:
            target = _manual_cleanup_target(directory, relative)
            if not target.exists():
                continue
            # The explicit cleanup action authorizes these exact names, including
            # unrecorded older versions. Hash only detects changes during this action.
            observed[relative] = _digest(target)
        ordered = sorted(observed, key=lambda relative: relative != NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'])
        for relative in ordered:
            if probe():
                return NativePluginCleanupResult('waiting_game_exit', '정리 도중 게임이 시작되어 남은 컴포넌트가 아직 정리되지 않았습니다. 게임을 완전히 종료한 후 다시 검사하세요.')
            target = _manual_cleanup_target(directory, relative)
            if not target.exists():
                continue
            if _digest(target) != observed[relative]:
                return NativePluginCleanupResult('conflict', f'{relative}이(가) 대조 후 변경되어 남은 구성 요소는 유지했습니다.')
            if observed[relative] not in known.get(relative, ()):
                kept.append(relative)   # KO patch: not ours (e.g. ReShade as d3d12.dll) - never delete it
                continue
            target.unlink()
    except EquipmentPluginDeploymentError as error:
        return NativePluginCleanupResult('conflict', str(error))
    except OSError as error:
        raise EquipmentPluginDeploymentError('게임 디렉터리의 네이티브 컴포넌트를 정리할 수 없습니다. 게임을 종료한 상태로 다시 시도하세요.') from error
    if probe():
        return NativePluginCleanupResult('waiting_game_exit', '컴포넌트 파일은 정리되었지만, 이미 로드된 세션을 끝내려면 게임을 종료해야 합니다.')
    if kept:
        return NativePluginCleanupResult('cleaned', '이 프로그램의 컴포넌트를 정리했습니다. 게임 폴더의 {0}은(는) 이 프로그램의 파일이 아니어서(ReShade 등 다른 프로그램의 파일일 수 있음) 그대로 두었습니다.'.format(', '.join(kept)))
    return NativePluginCleanupResult('cleaned', '고정 파일명 기준으로 게임 디렉터리의 네이티브 컴포넌트를 정리했습니다.')
