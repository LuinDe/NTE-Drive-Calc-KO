# 收集模式所需真实状态并执行已授权的组件生命周期，不接触账号业务数据。
from __future__ import annotations

from dataclasses import dataclass, replace, asdict
from importlib.util import find_spec
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Callable

from src.domain.work_mode import CheckState, NativeFeatureProbe, WorkModeProbe
from src.integrations.analysis_core_release import create_bundled_analysis_client
from src.integrations.game_component_bundle import inspect_game_component_bundle
from src.integrations.component_deployment_failure import ComponentDeploymentFailure
from src.integrations.native_plugin_bundle import NATIVE_PLUGIN_LAYOUTS
from src.integrations.legacy_game_proxy import legacy_game_proxy_present, ko_note_recorded_proxy
from src.integrations.game_path_discovery import running_game_executables
from src.integrations.native_capture_process import native_capture_game_pid
from src.integrations.launcher_process import LauncherProcessProbeError, selected_launcher_running
from src.integrations.mod_loader import ModLoaderRuntimeError, game_launcher_candidates
from src.integrations.nte_core import resolve_nte_core_executable
from src.integrations.nte_core_protocol import NteCoreError
from src.services.deployed_plugin_inspection import inspect_deployed_native_plugin
from src.services.native_plugin_deployment import PluginDeploymentPendingCleanup
from src.services.native_plugin_deployment import (
    deploy_native_plugin, cleanup_native_plugin, cleanup_manual_native_plugin, NativePluginCleanupResult,
)
from src.services.equipment_plugin_deployment import (
    find_game_executables, game_process_running, game_executable,
    mod_workspace_registry_snapshot, npcap_installation_present,
    EquipmentPluginDeploymentError,
)
from src.services.managed_plugin_cleanup import cleanup_managed_plugin
from src.services.mod_plugin_loading_service import ModPluginLoadingError, ModPluginLoadingWaiting
from src.services.work_mode_diagnostics import detection_failure_detail, detection_failure_state


@dataclass(frozen=True)
class _CleanupObservation:
    context: tuple[str, str]
    state: CheckState
    detail: str
    notify_on_exit: bool


def _cleanup_error_detail(error: Exception) -> str:
    cause = error.__cause__ if isinstance(error.__cause__, OSError) else error
    code = getattr(cause, "winerror", None)
    if code in {32, 33}:
        reason = "컴포넌트 파일이 다른 프로세스에서 사용 중입니다. 게임과 해당 파일을 사용 중인 프로그램을 완전히 종료한 후 다시 검사하세요."
    elif code == 5 or isinstance(cause, PermissionError):
        reason = "권한이 부족합니다. 게임 디렉터리 권한을 확인하거나 Calc를 관리자 권한으로 실행한 후 다시 검사하세요."
    elif code in {2, 3} or isinstance(cause, FileNotFoundError):
        reason = "정리 대상 경로가 더 이상 존재하지 않습니다. 게임 경로를 다시 검사해 주세요."
    else:
        reason = str(error) or type(error).__name__
    filename = getattr(cause, "filename", None)
    evidence = ([f"파일: {Path(filename).name}"] if filename else []) + ([f"시스템 오류 {code}"] if code is not None else [])
    return "컴포넌트 정리 실패:" + reason + ("（" + "；".join(evidence) + "）" if evidence else "")


def _has_cleanup_record(record: dict) -> bool:
    return any(record.get(key) for key in (
        "deployed_sha256", "managed_files", "workspace_path", "native_workspace_root",
    ))


class WorkModeRuntime:
    def __init__(self, *, policy, native_session, loader, application_root: Path,
                 config_dir: Path, game_running: Callable[[], bool | None] | None = None) -> None:
        self.policy, self.native_session, self.loader = policy, native_session, loader
        self.root, self.config_dir = application_root, config_dir
        self._lock = RLock()
        self._last_files = float("-inf")
        self._file_key = None
        self._last_auto_attempt = float("-inf")
        self._game_running = game_running or game_process_running
        ko_note_recorded_proxy(self.policy.deployment_record)
        self._loader_files = False
        self._native_deployed = None
        self._bundle = None
        self._last_discovery = float("-inf")
        self._discovery_key = None
        self.path_candidates: tuple[str, ...] = ()
        self.path_detail = ""
        self.cleanup_detail = ""
        self._cleanup_observation: _CleanupObservation | None = None
        self._closed = False
        self._analysis_available = False
        self._last_analysis = float("-inf")
        self._deployment_failure = ComponentDeploymentFailure(config_dir)
        self._auto_error = self._deployment_failure.load()

    def invalidate(self, *, retry_deployment: bool = False) -> None:
        with self._lock:
            if retry_deployment:
                self._deployment_failure.clear()
                self._auto_error = ""
            self._last_files = float("-inf")
            self._last_auto_attempt = float("-inf")
            self._last_analysis = float("-inf")
            self._last_discovery = float("-inf")

    def _block_automatic_deployment(self, detail: str) -> None:
        self._auto_error = detail + "; 자동 재시도를 중지했습니다. 원인을 확인한 후 “검사·처리”를 클릭해 다시 시도하세요."
        try:
            self._deployment_failure.save(self._auto_error)
        except OSError:
            self._auto_error += " 장애 상태를 저장하지 못했습니다. 원인을 해결하기 전에는 자동 배포를 다시 시작하지 마세요."
        self.cleanup_detail = self._auto_error

    @staticmethod
    def _validated_game_path(raw: str | Path) -> str:
        try:
            if not Path(str(raw).strip().strip('"')).expanduser().is_absolute():
                return ""
            return str(game_executable(raw))
        except (EquipmentPluginDeploymentError, OSError, ValueError):
            return ""

    @staticmethod
    def _recorded_cleanup_path(raw: str | Path) -> str:
        """Validate identity without requiring the old executable to still exist."""
        try:
            candidate = Path(str(raw).strip().strip('"')).expanduser()
            if (not candidate.is_absolute()
                    or candidate.name.casefold() != "htgame.exe"):
                return ""
            return str(candidate.resolve())
        except (OSError, ValueError):
            return ""

    def discover(self, *, force: bool = True, persist: bool = True) -> tuple[str, ...]:
        with self._lock:
            if self._closed:
                return ()
            configured = self.policy.settings.game_executable
            valid = self._validated_game_path(configured)
            if valid:
                self.path_candidates = (valid,)
                self.path_detail = ""
                self._discovery_key = None
                return self.path_candidates
            record_path = str(self.policy.deployment_record.get("game_executable") or "")
            key = configured, record_path
            if not force and key == self._discovery_key and monotonic() - self._last_discovery < 15:
                return self.path_candidates
            self._last_discovery, self._discovery_key = monotonic(), key
            discovered = (*running_game_executables(), *find_game_executables(), record_path)
            candidates = {}
            for candidate in discovered:
                path = self._validated_game_path(candidate)
                if path:
                    candidates.setdefault(path.casefold(), path)
            paths = tuple(candidates.values())
            if self._closed or self.policy.settings.game_executable != configured:
                return ()
            self.path_candidates = paths
            if len(paths) == 1:
                if persist:
                    self.policy.set_game_executable(paths[0])
                self.path_detail = "" if persist else "유일한 게임 디렉터리를 발견했습니다. 환경 설정에서 확인해 주세요."
            else:
                self.path_detail = (
                    "게임 디렉터리가 여러 개 발견되었지만 어느 것을 사용하는지 아직 확인할 수 없습니다; HTGame.exe를 확인해 선택하세요." if paths else
                    "게임 경로(HTGame.exe)를 찾지 못했습니다. 자동 탐지를 클릭하거나 게임 주 실행 파일을 직접 선택하세요; 백그라운드에서 자동으로 재시도합니다."
                )
            return paths

    def save_deployment(self, deployed) -> None:
        record = {key: str(value) if isinstance(value, Path) else value
                  for key, value in asdict(deployed).items()}
        previous = self.policy.deployment_record
        if (record.get("deployment_layout") in NATIVE_PLUGIN_LAYOUTS
                and previous.get("game_executable") == record.get("game_executable")):
            record["managed_files"] = {**previous.get("managed_files", {}), **record.get("managed_files", {})}
        for key in ("native_workspace_root", "native_workspace_files", "native_workspace_backup_path"):
            if key in previous:
                record[key] = previous[key]
        selected = "loader" if previous.get("loading_method") == "loader" else "native-capture"
        self.policy.update_deployment({**record, "loading_method": selected})
        self.policy.set_game_executable(str(deployed.game_executable))
        if not self.policy.allowed("native_load"):
            self.policy.set_cleanup_pending(True)
        self.invalidate(retry_deployment=True)

    def save_pending_deployment(self, error: PluginDeploymentPendingCleanup) -> None:
        self.save_deployment(error.deployment)
        self.policy.set_cleanup_pending(True)
        self._block_automatic_deployment(str(error))
        self._record_cleanup(CheckState.CLEANUP_PENDING, str(error), notify=True)

    def prepare_manual_native_deployment(self, *, expected_operation_revision: int) -> int:
        """Finish old cleanup before replacing its ownership with a new deployment."""
        with self._lock:
            frozen = self.policy.settings
            self.policy.require("native_load")
            if (self._closed or self.policy.operation_revision != expected_operation_revision
                    or self.native_session.battle_active):
                raise PermissionError("네이티브 컴포넌트 배포 컨텍스트가 변경되어 작업을 중지했습니다.")
            if frozen.pending_cleanup:
                self.cleanup(allow_unrecorded_legacy_workspace=True)
                if self.policy.settings.pending_cleanup:
                    raise EquipmentPluginDeploymentError(self.cleanup_detail)
                if self.policy.operation_revision != expected_operation_revision:
                    raise PermissionError("정리 중 네이티브 컴포넌트 배포 컨텍스트가 변경되어 작업을 중지했습니다.")
            self.policy.require("native_load")
            return self.policy.operation_revision

    def _current_cleanup_observation(self) -> _CleanupObservation | None:
        settings = self.policy.settings
        result = self._cleanup_observation
        if (not settings.pending_cleanup or result is None
                or result.context != (settings.game_executable, settings.deployment_json)):
            return None
        return result

    @property
    def cleanup_state(self) -> CheckState | None:
        result = self._current_cleanup_observation()
        return result.state if result is not None else None

    @property
    def cleanup_exit_detail(self) -> str:
        result = self._current_cleanup_observation()
        return result.detail if result is not None and result.notify_on_exit else ""

    def _record_cleanup(self, state: CheckState, detail: str, *, notify: bool = False) -> None:
        settings = self.policy.settings
        self.cleanup_detail = detail
        self._cleanup_observation = _CleanupObservation(
            (settings.game_executable, settings.deployment_json), state, detail, notify,
        )

    def cleanup(
        self, *, running: bool | None = None,
        allow_unrecorded_legacy_workspace: bool = False,
    ) -> None:
        self._cleanup_observation = None
        try:
            self._cleanup_components(
                running=running,
                allow_unrecorded_legacy_workspace=allow_unrecorded_legacy_workspace,
            )
        except (EquipmentPluginDeploymentError, ModPluginLoadingError, OSError) as error:
            self._record_cleanup(CheckState.FAULT, _cleanup_error_detail(error), notify=True)
            raise

    def _cleanup_components(
        self, *, running: bool | None = None,
        allow_unrecorded_legacy_workspace: bool = False,
    ) -> None:
        record = self.policy.deployment_record
        ko_note_recorded_proxy(record)
        has_deployment = _has_cleanup_record(record)
        if self.native_session.battle_active:
            self._record_cleanup(CheckState.WAITING, "이번 전투의 네이티브 전투 리포트를 마무리한 뒤 정리합니다.", notify=has_deployment)
            return
        self.discover(force=False)
        record = self.policy.deployment_record
        recorded_path = str(record.get("game_executable") or "")
        path = (
            self._recorded_cleanup_path(recorded_path)
            if recorded_path else self._validated_game_path(self.policy.settings.game_executable)
        )
        workspace_only = (record.get("deployment_layout") in NATIVE_PLUGIN_LAYOUTS
                          and not record.get("managed_files") and bool(record.get("native_workspace_root")))
        if not path and not workspace_only:
            detail = (
                "기존 컴포넌트 배포 기록의 게임 경로가 유효하지 않아 원래 정리 대상 디렉터리를 확인할 수 없습니다; 기록을 보존하며, 다른 게임 디렉터리를 대신 사용하지 않습니다."
                if recorded_path else self.path_detail
            )
            self._record_cleanup(CheckState.WAITING, detail, notify=has_deployment)
            return
        if record.get("deployment_layout") in NATIVE_PLUGIN_LAYOUTS:
            self._restore_native_workspace(record)
            self.loader.stop_loader()
            managed_files = record.get("managed_files", {})
            if allow_unrecorded_legacy_workspace and path:
                result = cleanup_manual_native_plugin(
                    application_root=self.root, game_executable_path=path,
                    managed_files=managed_files, game_running=self._game_running,
                )
            else:
                result = cleanup_native_plugin(
                    application_root=self.root, game_executable_path=path, managed_files=managed_files,
                    game_running=self._game_running,
                ) if managed_files else NativePluginCleanupResult("cleaned", "정리할 게임 디렉터리 컴포넌트가 없습니다.")
            self.cleanup_detail = result.detail
            if (result.status == "cleaned" and allow_unrecorded_legacy_workspace and path
                    and (legacy_game_proxy_present(Path(path).parent) or record.get("workspace_path"))):
                legacy_workspace = record.get("workspace_path")
                registered, current = mod_workspace_registry_snapshot()
                if registered and current and legacy_workspace:
                    if not Path(current).expanduser().is_absolute():
                        raise EquipmentPluginDeploymentError("현재 등록된 Mod 작업 공간 경로가 유효하지 않습니다. 배포 기록은 유지했습니다.")
                    if Path(legacy_workspace).expanduser().resolve() != Path(current).expanduser().resolve():
                        record = {**record, "workspace_path": current}
                        self.policy.update_deployment(record)
                        legacy_workspace = current
                result = cleanup_managed_plugin(
                    game_executable_path=path, mod_workspace_path=legacy_workspace,
                    game_running=self._game_running,
                    allow_unrecorded_workspace_adoption=not bool(legacy_workspace),
                    cleanup_legacy_proxy=True,
                )
                self.cleanup_detail = result.detail
            if result.status == "cleaned" and record.get("native_workspace_root"):
                result = self.loader.cleanup_native_workspace()
                self.cleanup_detail = result.detail
            self._record_cleanup(
                CheckState.FAULT if result.status == "conflict" else CheckState.CLEANUP_PENDING,
                result.detail, notify=result.status != "cleaned",
            )
            if result.status == "cleaned":
                self.policy.update_deployment({"loading_method": self.policy.deployment_record.get("loading_method", "native-capture")})
                self.policy.set_cleanup_pending(False)
                self.invalidate()
            return
        workspace = record.get("workspace_path")
        self.loader.stop_loader()
        if running is None:
            running = self._game_running()
        if running:
            self._record_cleanup(
                CheckState.CLEANUP_PENDING if has_deployment or workspace else CheckState.WAITING,
                "게임이 종료되지 않아 지금은 컴포넌트를 정리할 수 없습니다. 게임을 완전히 종료한 후 다시 검사하세요."
                if has_deployment or workspace else "게임이 종료되지 않아 게임 폴더에 컴포넌트가 있는지 아직 확인하지 못했습니다. 게임을 종료한 후 다시 검사하세요.",
                notify=bool(has_deployment or workspace),
            )
            return
        if allow_unrecorded_legacy_workspace:
            native_result = cleanup_manual_native_plugin(
                application_root=self.root, game_executable_path=path,
                managed_files=record.get("managed_files", {}), game_running=self._game_running,
            )
            if native_result.status != "cleaned":
                self._record_cleanup(
                    CheckState.FAULT if native_result.status == "conflict" else CheckState.CLEANUP_PENDING,
                    native_result.detail, notify=True,
                )
                return
        if allow_unrecorded_legacy_workspace and workspace:
            registered, current = mod_workspace_registry_snapshot()
            if registered and current:
                registered_path = Path(current).expanduser()
                if not registered_path.is_absolute():
                    raise EquipmentPluginDeploymentError("현재 등록된 Mod 작업 공간 경로가 유효하지 않습니다. 배포 기록은 유지했습니다.")
                if Path(workspace).expanduser().resolve() != registered_path.resolve():
                    if self._game_running():
                        self._record_cleanup(
                            CheckState.CLEANUP_PENDING,
                            "정리 전에 게임이 시작되어 로드 설정이 아직 조정되지 않았습니다. 게임을 종료한 후 다시 검사하세요.",
                            notify=True,
                        )
                        return
                    # Explicit cleanup adopts only the dedicated legacy registry
                    # value. Persist it before dispatch so a later retry uses the
                    # same observed workspace; deletion rechecks the registry.
                    record = {**record, "workspace_path": current}
                    self.policy.update_deployment(record)
                    workspace = current
        result = cleanup_managed_plugin(
            game_executable_path=path,
            mod_workspace_path=workspace,
            game_running=self._game_running,
            allow_unrecorded_workspace_adoption=(
                allow_unrecorded_legacy_workspace and not workspace
            ),
            cleanup_legacy_proxy=allow_unrecorded_legacy_workspace,
        )
        self._record_cleanup(
            CheckState.FAULT if result.status == "conflict" else CheckState.CLEANUP_PENDING,
            result.detail, notify=result.status != "cleaned",
        )
        if result.status != "cleaned":
            return
        method = self.policy.deployment_record.get("loading_method", "native-capture")
        self.policy.update_deployment({"loading_method": method})
        self.policy.set_cleanup_pending(False)
        self.cleanup_detail = (result.detail if 'dwmapi.dll' in result.detail else "이 프로그램의 컴포넌트와 이전 로드 진입점을 정리했습니다; 과거 DLL이나 로드 구성은 복원하지 않았습니다.")
        self.invalidate()

    def _automatic_deploy(self, running: bool | None) -> None:
        if self._auto_error:
            self.cleanup_detail = self._auto_error
            return
        if self._closed or self.native_session.battle_active or self.policy.settings.pending_cleanup:
            return
        if not self.policy.allowed("native_load", automatic=True):
            return
        if self._bundle is None or not self._bundle.ready:
            self.cleanup_detail = "동봉된 전체 컴포넌트가 점검을 통과하지 못했습니다. 자동 배포는 수정된 설치 패키지를 기다립니다."
            return
        self._automatic_native_deploy(running)

    def _automatic_native_deploy(self, running: bool | None) -> None:
        if self.policy.deployment_record.get("loading_method") == "loader":
            self._automatic_native_loader(running)
            return
        if self.loader.snapshot().phase == "running":
            self.cleanup_detail = "Loader가 실행 중입니다; 현재 Loader를 중지해야 D3D 진입점을 배포할 수 있습니다."
            return
        if self._native_deployed is not None and self._native_deployed.files_compatible:
            return
        if running is not False:
            self.cleanup_detail = (
                "게임이 실행 중입니다. 네이티브 컴포넌트 업데이트는 게임이 종료되기를 기다립니다." if running
                else "게임이 종료되었는지 확인하지 못해 자동 배포가 확인을 기다리고 있습니다; 다시 검사하세요."
            )
            return
        executable = self.policy.settings.game_executable
        if not executable or monotonic() - self._last_auto_attempt < 15:
            return
        self._last_auto_attempt = monotonic()
        frozen = replace(self.policy.settings, revision=0, auto_sync_enabled=False)
        operation_revision = self.policy.operation_revision

        def guard(capability):
            current = replace(self.policy.settings, revision=0, auto_sync_enabled=False)
            if (self._closed or self.policy.operation_revision != operation_revision or current != frozen
                    or self.policy.settings.game_executable != executable
                    or self.policy.settings.pending_cleanup or self.native_session.battle_active):
                raise PermissionError("네이티브 컴포넌트 배포 컨텍스트가 변경되어 이번 작업을 중지했습니다.")
            self.policy.require(capability, automatic=True)

        try:
            guard("native_load")
            self.native_session.close()
            deployed = deploy_native_plugin(
                application_root=self.root, game_executable_path=executable,
                operation_guard=guard, game_running=self._game_running,
                expected_existing_files={name: item.sha256 if item.present else None
                                         for name, item in self._native_deployed.files.items()}
                if self._native_deployed is not None else None,
                recorded_files=self.policy.deployment_record.get("managed_files") or {},
            )
            self.save_deployment(deployed)
            self.cleanup_detail = "네이티브 컴포넌트가 배포되었습니다; 게임을 실행한 뒤 연결과 각 기능을 다시 대조하세요."
        except PluginDeploymentPendingCleanup as error:
            self.save_pending_deployment(error)
        except (EquipmentPluginDeploymentError, PermissionError, OSError) as error:
            self._block_automatic_deployment("자동 네이티브 컴포넌트 배포 실패: " + str(error))

    def _restore_native_workspace(self, record) -> None:
        path = record.get("native_workspace_root")
        if path:
            self.loader.restore_native_workspace_record(
                workspace_path=path, managed_files=record.get("native_workspace_files", {}),
                backup_path=record.get("native_workspace_backup_path"),
            )

    def _save_native_loader_record(self, *, executable: str, pending: bool) -> None:
        workspace = self.loader.native_workspace_record
        if workspace is None:
            return
        record = self.policy.deployment_record
        previous_files = (record.get("native_workspace_files", {})
                          if record.get("native_workspace_root") == str(workspace.directory) else {})
        record.update({"game_executable": executable, "deployment_layout": inspect_game_component_bundle(self.root).layout,
                       "native_workspace_root": str(workspace.directory),
                       "native_workspace_files": {**previous_files, **workspace.managed_files},
                       "native_workspace_backup_path": str(workspace.backup_path) if workspace.backup_path else None,
                       "loader_payload_sha256": self.loader.active_payload_sha256})
        self.policy.update_deployment(record)
        if pending:
            self.policy.set_cleanup_pending(True)
        self.invalidate()

    def start_native_loader(self, *, automatic: bool = False):
        """Use the same Loader path for manual and automatic starts, preserving selection."""
        with self._lock:
            self.policy.require("native_load", automatic=automatic)
            if self._closed or self.native_session.battle_active:
                raise ModPluginLoadingError("먼저 진행 중인 수집 작업을 종료한 후 Loader를 시작하세요.")
            if self.policy.deployment_record.get("loading_method") != "loader":
                raise ModPluginLoadingError("현재 Loader 로드 방식이 선택되지 않았습니다.")
            if self._game_running():
                raise ModPluginLoadingWaiting("먼저 런처를 닫고 게임을 완전히 종료한 후 Loader를 시작하세요.")
            frozen = self.policy.settings
            executable = frozen.game_executable
            operation_revision = self.policy.operation_revision
            launcher_running = getattr(self.loader, "launcher_running", None)
            if callable(launcher_running):
                try:
                    if launcher_running(executable):
                        raise ModPluginLoadingWaiting("공식 런처가 아직 실행 중입니다; 런처와 게임을 종료한 후 Loader를 시작하세요.")
                except (LauncherProcessProbeError, ModLoaderRuntimeError, OSError) as error:
                    raise ModPluginLoadingWaiting(str(error)) from error

            def guard(capability):
                self.policy.require(capability, automatic=automatic)
                current = replace(self.policy.settings, revision=0, auto_sync_enabled=False)
                expected = replace(frozen, revision=0, auto_sync_enabled=False)
                if (self._closed or self.policy.operation_revision != operation_revision or current != expected
                        or self.policy.settings.game_executable != executable
                        or self.policy.deployment_record.get("loading_method") != "loader"
                        or self.native_session.battle_active):
                    raise PermissionError("Loader 시작 컨텍스트가 변경되어 작업을 중지했습니다.")

            self.loader.require_native_loader_supported()
            guard("native_load")
            record = self.policy.deployment_record
            self._restore_native_workspace(record)
            if (self.loader.snapshot().phase == "running" and not frozen.pending_cleanup
                    and (automatic or not legacy_game_proxy_present(Path(executable).parent))):
                return None
            self.native_session.close()
            guard("native_load")
            if frozen.pending_cleanup or (record.get("deployment_layout") in NATIVE_PLUGIN_LAYOUTS and record.get("managed_files")):
                self.policy.set_cleanup_pending(True)
                self.cleanup(running=False)
                if self.policy.settings.pending_cleanup:
                    raise ModPluginLoadingError(self.cleanup_detail)
                current = self.policy.settings
                # Only cleanup-owned settings may change while handing off the entry.
                if (replace(current, revision=0, deployment_json="{}", pending_cleanup=False, auto_sync_enabled=False)
                        != replace(frozen, revision=0, deployment_json="{}", pending_cleanup=False, auto_sync_enabled=False)):
                    raise PermissionError("정리 중 Loader 시작 컨텍스트가 변경되어 작업을 중지했습니다.")
                frozen = current
                guard("native_load")

            try:
                guard("native_load")
                result = self.loader.start_loader(
                    game_executable_path=executable,
                    writable_workspace_path=self.config_dir / "native-loader",
                    scoped_guard=guard,
                    cleanup_legacy_proxy=not automatic,
                )
                guard("native_load")
            except (EquipmentPluginDeploymentError, ModPluginLoadingError, PermissionError) as error:
                stop_error = None
                try:
                    self.loader.stop_loader()
                except (ModPluginLoadingError, OSError) as failure:
                    stop_error = failure
                finally:
                    self._save_native_loader_record(executable=executable, pending=self.loader.pending_native_workspace_path is not None)
                if stop_error is not None:
                    raise ModPluginLoadingError("Loader 시작이 무효화되었고 중지에도 실패했습니다; 정리 대기 상태를 유지합니다:" + str(stop_error)) from error
                raise
            self._save_native_loader_record(executable=executable, pending=False)
            self.cleanup_detail = "Loader가 게임 대기를 시작했습니다; 호스트, 플러그인, 수집 연결은 항목별 검사가 더 필요합니다."
            return result

    def _automatic_native_loader(self, running: bool | None) -> None:
        if running is not False:
            return
        if monotonic() - self._last_auto_attempt < 15:
            return
        self._last_auto_attempt = monotonic()
        try:
            self.start_native_loader(automatic=True)
        except ModPluginLoadingWaiting as error:
            self.cleanup_detail = str(error)
        except (EquipmentPluginDeploymentError, ModPluginLoadingError, PermissionError) as error:
            self._block_automatic_deployment("자동 Loader 시작 실패: " + str(error))

    def _inspect_component_files(self, *, path_valid: bool, running: bool | None) -> None:
        settings = self.policy.settings
        record = self.policy.deployment_record
        loader_hash = self.loader.active_payload_sha256
        key = (settings.game_executable, settings.deployment_json, loader_hash, running)
        if key == self._file_key and monotonic() - self._last_files <= 15:
            return
        self._bundle = inspect_game_component_bundle(self.root)
        self._native_deployed = inspect_deployed_native_plugin(
            application_root=self.root, game_executable_path=settings.game_executable,
            recorded_files=record.get("managed_files", {}), bundle_inspection=self._bundle,
        ) if path_valid else None
        self._loader_files = False
        if record.get("loading_method") == "loader" and record.get("native_workspace_root"):
            self._restore_native_workspace(record)
            workspace = self.loader.inspect_native_workspace()
            state = self.loader.snapshot()
            self._loader_files = workspace.files_compatible and (state.phase == "running" or running)
        self._last_files, self._file_key = monotonic(), key

    def tick(
        self, *, allow_connect: bool = False,
        allow_unrecorded_legacy_cleanup: bool = False,
        preview: bool = False,
    ) -> WorkModeProbe:
        with self._lock:
            if self._closed:
                return WorkModeProbe()
            self.discover(force=False, persist=not preview)
            if self._closed:
                return WorkModeProbe()
            path_valid = bool(self._validated_game_path(self.policy.settings.game_executable))
            if monotonic() - self._last_analysis > 15:
                try:
                    analysis = create_bundled_analysis_client(static_database_path=self.root / "data" / "game_static.sqlite3")
                    self._analysis_available = bool(analysis and analysis.supports_battle_page)
                except Exception:
                    self._analysis_available = False
                self._last_analysis = monotonic()
            try:
                core_available = resolve_nte_core_executable().is_file()
            except Exception:
                core_available = False
            local_probe = WorkModeProbe(
                analysis_available=self._analysis_available, core_available=core_available,
                npcap_available=npcap_installation_present(), input_available=find_spec("pyautogui") is not None,
            )
            if not path_valid:
                self._file_key = None
                record = self.policy.deployment_record
                recorded_cleanup_path = self._recorded_cleanup_path(record.get("game_executable") or "")
                workspace_only = (
                    record.get("deployment_layout") in NATIVE_PLUGIN_LAYOUTS
                    and not record.get("managed_files")
                    and bool(record.get("native_workspace_root"))
                )
                if not preview and self.policy.settings.pending_cleanup and (recorded_cleanup_path or workspace_only):
                    running = self._game_running()
                    if not self.native_session.battle_active:
                        self.native_session.close()
                    try:
                        self.cleanup(
                            running=running,
                            allow_unrecorded_legacy_workspace=allow_unrecorded_legacy_cleanup,
                        )
                    except (EquipmentPluginDeploymentError, ModPluginLoadingError, OSError):
                        return replace(
                            local_probe, game_path_valid=False, game_running=running,
                            cleanup_state=self.cleanup_state, cleanup_detail=self.cleanup_detail,
                            component_update_state=CheckState.FAULT,
                            component_update_detail=self.cleanup_detail,
                        )
                if self.policy.settings.pending_cleanup and self.cleanup_state is None:
                    self._record_cleanup(CheckState.WAITING, self.path_detail,
                                         notify=_has_cleanup_record(self.policy.deployment_record))
                return replace(local_probe,
                    game_path_valid=False,
                    component_update_state=CheckState.WAITING, component_update_detail=self.path_detail,
                    cleanup_detail=self.cleanup_detail if self.cleanup_state is not None else self.path_detail,
                    cleanup_state=self.cleanup_state,
                )
            running = self._game_running()
            settings = self.policy.settings
            if settings.pending_cleanup and not preview:
                if self.native_session.battle_active:
                    self.cleanup(
                        running=running,
                        allow_unrecorded_legacy_workspace=allow_unrecorded_legacy_cleanup,
                    )
                else:
                    self.native_session.close()
                    try:
                        self.cleanup(
                            running=running,
                            allow_unrecorded_legacy_workspace=allow_unrecorded_legacy_cleanup,
                        )
                    except (EquipmentPluginDeploymentError, ModPluginLoadingError, OSError):
                        return replace(local_probe,
                            game_path_valid=True, game_running=running,
                            cleanup_state=self.cleanup_state, cleanup_detail=self.cleanup_detail,
                            component_update_state=CheckState.FAULT, component_update_detail=self.cleanup_detail,
                        )
            settings = self.policy.settings
            self._inspect_component_files(path_valid=path_valid, running=running)
            if path_valid and not settings.pending_cleanup and not preview:
                self._automatic_deploy(running)
            files = (self._loader_files if self.policy.deployment_record.get("loading_method") == "loader"
                     else bool(self._native_deployed and self._native_deployed.files_compatible))
            launcher_running = None
            launcher_error = ""
            if self.policy.deployment_record.get("loading_method") == "loader" and (settings.pending_cleanup or not files):
                try:
                    launcher_running = any(
                        selected_launcher_running(candidate)
                        for candidate in game_launcher_candidates(settings.game_executable)
                    )
                except (LauncherProcessProbeError, ModLoaderRuntimeError, OSError) as error:
                    launcher_error = str(error)
            current_package = loading = files
            capabilities = self._bundle.native_capabilities
            core_available = self._bundle.ready
            native = NativeFeatureProbe(files=files)
            domain_files = {
                "native_" + name: NativeFeatureProbe(
                    files=loading and f"{name}.snapshot.v1" in capabilities,
                    reason=("packaged_capability_missing" if loading and
                            f"{name}.snapshot.v1" not in capabilities else ""),
                ) for name in ("character", "inventory", "team", "environment")
            }
            battle_supported = {"combat.hit_buff.v1", "combat.context.v1"}.issubset(capabilities)
            battle_files = NativeFeatureProbe(files=files, supported=battle_supported,
                reason="" if battle_supported else "packaged_capability_missing")
            equipment_supported = "equipment.execute.v1" in capabilities
            equipment_files = NativeFeatureProbe(files=files, supported=equipment_supported,
                reason="" if equipment_supported else "packaged_capability_missing")
            if self._auto_error:
                update_state, update_detail = CheckState.FAULT, self._auto_error
            elif not self._bundle or not self._bundle.ready:
                update_state = CheckState.MISSING
                update_detail = ("；".join(self._bundle.issues) if self._bundle else "") or "매칭 컴포넌트 패키지가 아직 점검을 통과하지 못했습니다."
            elif settings.pending_cleanup:
                update_state = CheckState.CLEANUP_PENDING
                update_detail = self.cleanup_detail or "이전 컴포넌트 정리가 아직 완료되지 않았습니다. 먼저 정리를 완료하세요."
            elif current_package:
                update_state, update_detail = CheckState.AVAILABLE, "현재 함께 제공되는 컴포넌트가 배포되었습니다."
            else:
                update_state = CheckState.MISSING
                update_detail = "현재 게임 내 컴포넌트가 아직 배포되지 않았거나 컴포넌트 패키지와 호환되지 않습니다; 컴포넌트 배포로 이동해 배포한 뒤 다시 검사하세요."
            probe = replace(local_probe,
                component_update_state=update_state, component_update_detail=update_detail,
                game_path_valid=path_valid, game_running=running,
                launcher_running=launcher_running, launcher_probe_error=launcher_error,
                core_available=core_available,
                native_load=native, **domain_files, native_battle=battle_files,
                native_equipment=equipment_files, cleanup_detail=self.cleanup_detail, cleanup_state=self.cleanup_state,
            )
            if running is False:
                if not preview:
                    self.native_session.close()
                return probe
            if (not self.policy.allowed("native_sync") or settings.pending_cleanup or settings.paused
                    or not (allow_connect or self.policy.allowed("native_sync", automatic=True)
                            or self.native_session.battle_active)):
                return probe
            try:
                pipe = native_capture_game_pid() is not None
            except Exception as error:
                return replace(
                    probe,
                    native_diagnostic=detection_failure_detail(error, record=allow_connect),
                    native_diagnostic_state=detection_failure_state(error),
                )
            native = replace(native, pipe=pipe)
            values = {key: replace(value, pipe=pipe) for key, value in domain_files.items()}
            values["native_battle"] = replace(battle_files, pipe=pipe)
            values["native_equipment"] = replace(equipment_files, pipe=pipe)
            if pipe:
                try:
                    # Background checks only observe readiness. An explicit check
                    # may still collect evidence for manual feature readiness.
                    response = self.native_session.inspect(refresh=allow_connect, check_equipment=allow_connect)
                    caps = response["hello"].get("capabilities", [])
                    equipment = response.get("equipment") or {}
                    inventory_ready = response.get("inventory_snapshot_ready") is True
                    values["native_equipment"] = replace(values["native_equipment"], handshake=True,
                        supported=equipment_files.supported is True and {"equipment", "native_equipment_v1"}.issubset(caps),
                        ready=equipment.get("ready"), snapshot=inventory_ready,
                        projection_complete=inventory_ready,
                        reason=equipment_files.reason or str(equipment.get("reason") or (
                            "equipment_check_required" if response.get("equipment") is None else "")))
                    battle = response["status"].get("native_status", {})
                    values["native_battle"] = replace(
                        values["native_battle"], handshake=True, supported=battle_files.reason != "packaged_capability_missing" and {"native_hit_buff_v1", "battle_axis_v1", "native_context_observation_v1"}.issubset(caps),
                        ready=battle.get("ready"), reason=str(battle.get("readyReason") or ""),
                    )
                    domains = response["domains"].get("domains", [])
                    domain_map = {item.get("domain"): item for item in domains if isinstance(item, dict)}
                    domain_errors = response.get("domain_errors", {})
                    for domain in ("character", "inventory", "team", "environment"):
                        item = domain_map.get(domain, {})
                        error = domain_errors.get(domain)
                        values["native_" + domain] = replace(
                            values["native_" + domain], handshake=True,
                            supported=f"{domain}.snapshot.v1" in caps and (
                                domain not in {"inventory", "character"} or
                                ("native_inventory_dto_v1" if domain == "inventory" else "native_character_profile_v1") in caps
                            ),
                            profile_projection_supported=domain == "character" and "native_character_profile_v1" in caps,
                            ready=False if error else item.get("ready"),
                            reason=(values["native_" + domain].reason or
                                    (str(error.get("reason") or error["message"]) if error else
                                     str(item.get("readyReason") or ""))),
                            snapshot=bool(item.get("snapshotId")), complete=item.get("complete"),
                            source_coverage=str(item.get("sourceCoverage") or "unknown"),
                        )
                    probe = replace(probe, logged_in=battle.get("ready") is True)
                except Exception as error:
                    # Preserve only this request's confirmed transport fact, never partial business data.
                    context = error.request_context if isinstance(error, NteCoreError) else None
                    handshake = True if context is not None and context.handshake_confirmed else None
                    values = {key: replace(getattr(probe, key), pipe=pipe, handshake=handshake) for key in values}
                    probe = replace(
                        probe,
                        native_diagnostic=detection_failure_detail(error, record=allow_connect),
                        native_diagnostic_state=detection_failure_state(error),
                    )
            return replace(probe, **values)

    def request_close(self) -> None:
        self._closed = True
        self.native_session.request_close(permanent=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self.native_session.close()
