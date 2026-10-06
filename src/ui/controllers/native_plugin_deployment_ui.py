# 在现有设置部署入口展示原生插件配套状态并提交游戏退出后的整套部署。
from PySide6.QtCore import QEventLoop, QSize, Qt, QThread, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout

from src.app.theme import theme_color
from src.app.window_geometry import fit_dialog_to_available_screen
from src.integrations.game_component_bundle import inspect_game_component_bundle
from src.services.deployed_plugin_inspection import inspect_deployed_native_plugin
from src.services.equipment_plugin_deployment import EquipmentPluginDeploymentError
from src.services.native_plugin_deployment import PluginDeploymentPendingCleanup
from src.services.native_plugin_deployment import deploy_native_plugin
from src.services.mod_plugin_loading_service import ModPluginLoadingError, ModPluginLoadingWaiting
from src.utils.logger import logger
from src.integrations.legacy_game_proxy import ko_kept_note as _ko_kept_note


def _deployment_error_hint(error: Exception) -> str:
    """Keep implementation errors out of the short user-facing dialog."""
    if getattr(error, "foreign_game_file", False) or getattr(error.__cause__, "foreign_game_file", False):
        return str(error)   # KO patch: name the game-folder file that was kept, not a generic hint
    logger.warning(f"컴포넌트 배포 미완료 kind={type(error).__name__}")
    if isinstance(error, PermissionError):
        return "컴포넌트 처리가 완료되지 않았습니다; 작업 모드와 게임 디렉터리 권한을 점검하고, 게임이 종료되었는지 확인하세요."
    if isinstance(error, TimeoutError):
        return "컴포넌트 또는 동기화 작업 종료를 기다리다 시간 초과되었습니다; 게임을 종료한 후 다시 검사하고 재시도하세요."
    return "컴포넌트 처리가 완료되지 않았습니다; 게임과 런처가 종료되었는지 확인한 후, 환경 설정에서 검사 상세 정보를 확인하세요."


class _DeploymentWorker(QThread):
    def __init__(self, target, parent):
        super().__init__(parent)
        self._target = target
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self._target()
        except Exception as error:
            self.error = error


class _DeploymentProgress(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.running = True

    def reject(self):
        if not self.running:
            super().reject()

    def closeEvent(self, event):
        if self.running:
            event.ignore()
        else:
            super().closeEvent(event)


def _run_deployment_worker(window, target):
    """Keep disk hashing and replacement off the GUI thread without losing typed errors."""

    dialog = _DeploymentProgress(window)
    dialog.setWindowTitle("네이티브 컴포넌트를 배포하는 중")
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    layout = QVBoxLayout(dialog)
    label = QLabel("게임 컴포넌트를 확인하고 기록하는 중입니다. 게임을 종료한 상태로 유지해 주세요…", dialog)
    label.setWordWrap(True)
    layout.addWidget(label)
    progress = QProgressBar(dialog)
    progress.setRange(0, 0)
    layout.addWidget(progress)
    fit_dialog_to_available_screen(dialog, QSize(420, 120))

    loop = QEventLoop(dialog)
    worker = _DeploymentWorker(target, dialog)
    worker.finished.connect(loop.quit)
    try:
        dialog.show()
        QTimer.singleShot(0, worker.start)
        loop.exec()
        worker.wait()
        if worker.error is not None:
            raise worker.error
        return worker.result
    finally:
        dialog.running = False
        dialog.close()
        dialog.deleteLater()


def _refresh_work_mode_detection(window) -> None:
    controller = getattr(window, "work_mode_controller", None)
    if controller is not None:
        controller.component_state_changed()


def _set_component_status(label, *, issues=(), ready=False, pending=False):
    if pending:
        label.setText("Loader 작업 공간 대조가 완료되지 않았습니다; 검사 상세를 확인하세요.")
        label.setToolTip("")
    elif ready:
        label.setText("컴포넌트가 준비되었습니다. 게임을 시작하면 연결과 사용 가능한 기능을 자동으로 확인합니다.")
        label.setToolTip("")
    else:
        label.setText(f"컴포넌트가 준비되지 않았습니다({len(issues)}개 항목); 검사 상세 정보를 확인하세요.")
        label.setToolTip("\n".join(str(issue) for issue in issues))


def refresh_native_plugin_status(window) -> None:
    bundle = inspect_game_component_bundle(window.app_context.paths.root)
    combo = getattr(window, "_equipment_plugin_loading_method_combo", None)
    if combo is not None:
        combo.blockSignals(True)
        index = combo.findData("native-capture")
        if index < 0:
            index = combo.findData("proxy")
            if index >= 0:
                combo.setItemText(index, "D3D 수집 프록시")
                combo.setItemData(index, "native-capture")
            else:
                combo.addItem("D3D 수집 프록시", "native-capture")
                index = combo.findData("native-capture")
        if combo.currentData() not in {"loader", "native-capture"}:
            combo.setCurrentIndex(index)
        combo.setEnabled(True)
        combo.blockSignals(False)
    primary = getattr(window, "_equipment_plugin_primary_button", None)
    if primary is not None:
        primary.setText("네이티브 Loader 시작" if combo is not None and combo.currentData() == "loader" else "네이티브 컴포넌트 배포")
    label = getattr(window, "_equipment_plugin_status_label", None)
    if label is not None:
        if not bundle.ready:
            _set_component_status(label, issues=bundle.issues)
        elif combo is not None and combo.currentData() == "loader":
            try:
                service = window._mod_plugin_loading_service
                workspace = service.inspect_native_workspace()
                _set_component_status(label, ready=workspace.files_compatible, issues=workspace.issues)
            except (EquipmentPluginDeploymentError, ModPluginLoadingError):
                _set_component_status(label, pending=True)
        else:
            result = inspect_deployed_native_plugin(
                application_root=window.app_context.paths.root,
                game_executable_path=window.work_mode_service.settings.game_executable,
                recorded_files=window.work_mode_service.deployment_record.get("managed_files", {}),
                bundle_inspection=bundle,
            )
            _set_component_status(label, ready=result.files_compatible, issues=result.issues)


def _confirm_d3d_deployment(window) -> bool:
    dialog = QDialog(window)
    dialog.setObjectName("d3dDeploymentConfirmation")
    dialog.setWindowTitle("D3D 네이티브 컴포넌트 배포")
    dialog.setWindowModality(Qt.WindowModal)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 18)
    layout.setSpacing(14)

    warning = QLabel("배포 전에 게임을 완전히 종료하세요", dialog)
    warning.setObjectName("d3dDeploymentExitWarning")
    warning.setStyleSheet(f"color:{theme_color('#f85149')};font-size:17px;font-weight:700")
    layout.addWidget(warning)
    guidance = QLabel("게임 창과 게임 프로세스가 모두 종료되었는지 확인한 후 배포를 계속하세요.", dialog)
    guidance.setWordWrap(True)
    layout.addWidget(guidance)
    detail = QLabel(
        "이번에 d3d12.dll, NTE_Capture.dll을 교체하고 이전 dwmapi.dll을 정리합니다.\n"
        "기존 컴포넌트는 직접 교체되며 백업을 남기지 않습니다.",
        dialog,
    )
    detail.setObjectName("d3dDeploymentChanges")
    detail.setWordWrap(True)
    detail.setStyleSheet(f"color:{theme_color('#8b949e')}")
    layout.addWidget(detail)

    actions = QHBoxLayout()
    actions.addStretch()
    cancel = QPushButton("취소", dialog)
    cancel.setDefault(True)
    cancel.setFocus()
    cancel.clicked.connect(dialog.reject)
    actions.addWidget(cancel)
    proceed = QPushButton("게임을 종료했습니다. 배포를 계속합니다", dialog)
    proceed.setObjectName("d3dDeploymentProceed")
    proceed.setAutoDefault(False)
    proceed.clicked.connect(dialog.accept)
    actions.addWidget(proceed)
    layout.addLayout(actions)
    fit_dialog_to_available_screen(dialog, QSize(560, 250))
    return dialog.exec() == QDialog.Accepted


def deploy_native_plugin_from_settings(window) -> None:
    bundle = inspect_game_component_bundle(window.app_context.paths.root)
    if not bundle.ready:
        window.operation_unavailable("네이티브 컴포넌트 배포", "；".join(bundle.issues), target="deployment")
        return
    if window.native_game_session.battle_active:
        QMessageBox.information(window, "네이티브 컴포넌트 배포", "먼저 진행 중인 전투 리포트 수집을 종료한 후 컴포넌트를 배포하세요.")
        return
    try:
        if window._mod_plugin_loading_service.snapshot().phase == "running":
            QMessageBox.information(window, "네이티브 컴포넌트 배포", "먼저 Loader를 중지한 후 D3D 네이티브 컴포넌트를 배포하세요.")
            return
    except (EquipmentPluginDeploymentError, ModPluginLoadingError) as error:
        window.operation_unavailable("네이티브 컴포넌트 배포", _deployment_error_hint(error), target="deployment")
        return
    executable = window.work_mode_service.settings.game_executable
    generation = window.operation_generation()
    if not _confirm_d3d_deployment(window):
        return

    policy = window.work_mode_service
    context = window.app_context
    session = window.native_game_session
    runtime = window.work_mode_runtime
    root = context.paths.root

    def guard(capability):
        policy.require(capability)
        if ((policy.operation_revision, context.generation) != generation
                or policy.settings.game_executable != executable
                or session.battle_active):
            raise PermissionError("네이티브 컴포넌트 배포 컨텍스트가 변경되어 작업을 중지했습니다.")

    try:
        guard("native_load")
        invalidate = getattr(window, "invalidate_inventory_sync_notifications", None)
        if invalidate is not None:
            invalidate()
        sync_service = getattr(window, "_inventory_sync_service", None)
        if sync_service is not None:
            sync_service.request_stop()
        window.character_profile_sync_controller.request_stop()

        def deploy_after_stop():
            nonlocal generation
            if sync_service is not None and sync_service.is_running:
                sync_service.stop()
            session.close()
            revision = runtime.prepare_manual_native_deployment(expected_operation_revision=generation[0])
            generation = (revision, generation[1])
            guard("native_load")
            return deploy_native_plugin(
                application_root=root,
                game_executable_path=executable,
                operation_guard=guard,
                cleanup_legacy_proxy=True,
                recorded_files=policy.deployment_record.get("managed_files") or {},
            )

        try:
            deployed = _run_deployment_worker(window, deploy_after_stop)
        finally:
            if sync_service is None or not sync_service.is_running:
                window._stop_inventory_sync()
        runtime.save_deployment(deployed)
        window._refresh_equipment_plugin_status()
        _refresh_work_mode_detection(window)
        QMessageBox.information(window, "네이티브 컴포넌트 배포 완료", ("게임을 시작한 후 연결과 각 능력을 다시 검사하세요.") + _ko_kept_note())
    except PluginDeploymentPendingCleanup as error:
        runtime.save_pending_deployment(error)
        _refresh_work_mode_detection(window)
        QMessageBox.warning(
            window, "컴포넌트 배포 정리 대기",
            "상태: 배포가 아직 완료되지 않았습니다.\n"
            "원인: 이전 컴포넌트 정리가 확인을 통과하지 못했습니다.\n"
            "다음 단계: 환경 설정에서 정리 결과를 확인하고, 처리 후 다시 배포하세요.",
        )
    except (EquipmentPluginDeploymentError, PermissionError, TimeoutError) as error:
        if window.work_mode_service.allowed("native_load"):
            window.operation_unavailable("네이티브 컴포넌트 배포", _deployment_error_hint(error), target="deployment")


def start_native_loader_from_settings(window) -> None:
    try:
        window._stop_inventory_sync()
        window.character_profile_sync_controller.request_stop()
        window.work_mode_runtime.start_native_loader()
        window._refresh_equipment_plugin_status()
        _refresh_work_mode_detection(window)
        QMessageBox.information(window, "네이티브 Loader 시작됨",
                                ("게임을 정상적으로 시작한 후 연결과 각 능력을 다시 검사하세요.") + _ko_kept_note())
    except ModPluginLoadingWaiting as error:
        window._refresh_equipment_plugin_status()
        QMessageBox.warning(
            window, "Loader가 프로그램 종료를 기다리는 중",
            "상태: Loader가 아직 시작되지 않음\n원인: " + str(error) +
            "\n다음 단계: 런처와 게임을 완전히 종료한 뒤 “네이티브 Loader 시작”을 클릭하세요.",
        )
    except (EquipmentPluginDeploymentError, ModPluginLoadingError, PermissionError) as error:
        window._refresh_equipment_plugin_status()
        window.operation_unavailable("네이티브 Loader 시작", _deployment_error_hint(error), target="deployment")
