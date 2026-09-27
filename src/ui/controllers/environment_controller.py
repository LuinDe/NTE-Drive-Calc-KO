# 从 MainWindow 抽离的控制器方法。
"""Compatibility-installed MainWindow controller."""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from src.app.workers import WorkerThread
from src.ui.controllers.native_plugin_deployment_ui import refresh_native_plugin_status, deploy_native_plugin_from_settings
from src.observability.context import OperationContext
from src.observability.operation import log_event
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    find_game_executables,
    game_process_running,
    npcap_installation_present,
)
from src.services.nte_core_diagnostics import (
    capture_device_names,
    collect_nte_core_diagnostics,
    format_nte_core_diagnostics,
)
from src.ui.controllers.mod_loader_controller import (
    activate_equipment_plugin_loading_method,
    deactivate_equipment_plugin_loading_method,
    equipment_plugin_loading_method_changed,
    start_equipment_mod_loader,
    stop_equipment_mod_loader,
)


def _new_environment_operation(
    self: Any,
    feature: str,
) -> OperationContext:
    app_context = getattr(self, "app_context", None)
    return OperationContext.create(
        feature,
        account_id=(
            app_context.account.active_account_id
            if app_context is not None
            else None
        ),
        context_generation=(
            app_context.generation if app_context is not None else None
        ),
    )


def _environment_result_acceptor(self, operation: OperationContext, capability: str):
    revision = self.work_mode_service.operation_revision
    delivered = False

    def accept() -> bool:
        nonlocal delivered
        if delivered:
            return False
        delivered = True
        context = self.app_context
        return (
            context.account.active_account_id == operation.account_id
            and context.generation == operation.context_generation
            and self.work_mode_service.operation_revision == revision
            and self.work_mode_service.allowed(capability)
        )

    return accept


def _refresh_equipment_plugin_status(self):
    for name, worker_name in (
        ("_equipment_plugin_primary_button", None),
        ("_nte_core_diagnostic_button", "_nte_core_diagnostic_worker"),
    ):
        button = getattr(self, name, None)
        worker = getattr(self, worker_name, None) if worker_name else None
        if button is not None:
            button.setEnabled(worker is None or not worker.isRunning())
    stop_button = getattr(self, "_equipment_plugin_stop_button", None)
    if stop_button is not None:
        stop_button.setEnabled(True)
        stop_button.setText("게임 디렉터리 정리")
    label = getattr(self, "_npcap_status_label", None)
    if label is not None:
        label.setText(
            "Npcap: 감지됨" if npcap_installation_present()
            else "Npcap: 감지되지 않음 (공식 설치 프로그램으로 설치하세요)"
        )
    refresh_native_plugin_status(self)


def _select_equipment_plugin_game_executable(self):
    selected, _ = QFileDialog.getOpenFileName(
        self, "게임 실행 파일 선택", "", "HTGame.exe (HTGame.exe)"
    )
    if selected:
        self._equipment_plugin_game_executable_edit.setText(selected)
        self.work_mode_service.set_game_executable(selected)
        self._refresh_equipment_plugin_status()


def _detect_equipment_plugin_game_executable(self):
    current_worker = getattr(self, "_equipment_plugin_detection_worker", None)
    if current_worker is not None and current_worker.isRunning():
        return
    button = getattr(self, "_equipment_plugin_detect_button", None)
    if button is not None:
        button.setEnabled(False)
        button.setText("감지하는 중…")
    worker = WorkerThread(target=find_game_executables, parent=self)
    self._equipment_plugin_detection_worker = worker
    operation = _new_environment_operation(self, "game_detection")
    frozen_account_id = self.app_context.account.active_account_id
    frozen_generation = self.app_context.generation

    def context_is_current() -> bool:
        return (
            self.app_context.account.active_account_id == frozen_account_id
            and self.app_context.generation == frozen_generation
        )

    log_event(
        "INFO",
        "environment.game_detection_started",
        "게임 위치 자동 감지 시작",
        operation,
    )

    def finish(candidates):
        if button is not None:
            button.setEnabled(True)
            button.setText("자동 감지")
        if not context_is_current():
            log_event(
                "INFO",
                "environment.game_detection_discarded",
                "계정 컨텍스트가 바뀌어 자동 감지 결과를 버립니다",
                operation,
            )
            return
        choices = [str(path) for path in candidates]
        log_event(
            "INFO",
            "environment.game_detection_succeeded",
            "게임 위치 자동 감지 완료",
            operation,
            candidate_count=len(choices),
        )
        if not choices:
            QMessageBox.information(
                self,
                "게임 위치 감지",
                "이환 설치 레지스트리와 일반적인 게임 라이브러리 디렉터리를 확인했지만 HTGame.exe를 찾지 못했습니다."
                "직접 입력하거나 파일을 선택할 수 있으며, 찾는 방법은 다음과 같습니다:\n\n"
                "1. 바탕 화면의 게임 아이콘을 마우스 오른쪽 버튼으로 클릭해 “파일 위치 열기”를 선택하세요.\n"
                "2. Client\\WindowsNoEditor\\HT\\Binaries\\Win64로 들어가 HTGame.exe를 찾으세요.\n"
                "3. HTGame.exe를 마우스 오른쪽 버튼으로 클릭해 “경로로 복사”를 선택한 뒤 게임 실행 파일 칸에 붙여넣으세요.",
            )
            return
        selected = choices[0]
        if len(choices) > 1:
            selected, accepted = QInputDialog.getItem(
                self, "게임 위치 선택", "HTGame.exe가 여러 개 감지되었습니다. 사용 중인 게임을 선택하세요:",
                choices, 0, False,
            )
            if not accepted:
                return
        self._equipment_plugin_game_executable_edit.setText(selected)
        self.work_mode_service.set_game_executable(selected)
        self._refresh_equipment_plugin_status()

    def failed(error):
        if button is not None:
            button.setEnabled(True)
            button.setText("자동 감지")
        if not context_is_current():
            log_event(
                "INFO",
                "environment.game_detection_discarded",
                "계정 컨텍스트가 바뀌어 자동 감지 오류를 버립니다",
                operation,
            )
            return
        log_event(
            "ERROR",
            "environment.game_detection_failed",
            "게임 위치 자동 감지 실패",
            operation,
            error=error,
        )
        QMessageBox.warning(
            self,
            "게임 위치 감지",
            "상태: 게임 위치 자동 인식에 실패했습니다.\n"
            "원인: 유일하게 사용 가능한 게임 메인 실행 파일을 확인하지 못했습니다.\n"
            "다음 단계: 현재 환경 설정에서 HTGame.exe를 직접 선택한 뒤 다시 검사하세요.",
        )

    worker.result_ready.connect(finish)
    worker.error.connect(failed)
    worker.start()

def _open_npcap_download(self):
    if not _require_environment_diagnostics(self, packet=True, label="Npcap 다운로드"):
        return
    self._open_url("https://npcap.com/dist/npcap-1.88.exe")

def _show_npcap_status(self):
    if not _require_environment_diagnostics(self, packet=True, label="Npcap 검사"):
        return
    if npcap_installation_present():
        QMessageBox.information(
            self, "Npcap 상태", "Npcap이 감지되어 가방 동기화 환경의 이 의존성은 충족되었습니다."
        )
        return
    self.operation_unavailable(
        "Npcap 검사", "Npcap이 감지되지 않아 패킷 캡처 동기화에 이 의존성이 없습니다; DLL 동기화는 별도로 검사합니다."
        "설정에서 공식 Npcap 설치 프로그램을 다운로드하고, 설치한 후 다시 검사하세요.", target="detection",
    )


def _require_environment_diagnostics(self, *, packet: bool, label: str) -> bool:
    capability = "diagnostics" if not packet or self.work_mode_service.allowed("diagnostics") else "packet_capture"
    if not self.operation_entry(capability, label):
        return False
    try:
        self.work_mode_service.require(capability)
    except PermissionError as error:
        QMessageBox.warning(self, "환경 검사", str(error))
        return False
    return True


def _diagnose_nte_core(self):
    if not _require_environment_diagnostics(self, packet=True, label="nte-core 진단"):
        return
    current_worker = getattr(self, "_nte_core_diagnostic_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "nte-core 진단", "진단이 진행 중입니다. 잠시 기다리세요.")
        return
    button = getattr(self, "_nte_core_diagnostic_button", None)
    if button is not None:
        button.setEnabled(False)
        button.setText("진단 중…")
    capability = "diagnostics" if self.work_mode_service.allowed("diagnostics") else "packet_capture"

    def collect():
        self.work_mode_service.require(capability)
        return collect_nte_core_diagnostics(cwd=self.app_context.paths.app_dir)

    worker = WorkerThread(target=collect, parent=self)
    self._nte_core_diagnostic_worker = worker
    operation = _new_environment_operation(self, "nte_core_diagnostics")
    accept_result = _environment_result_acceptor(self, operation, capability)
    log_event(
        "INFO",
        "environment.nte_core_diagnostics_started",
        "nte-core 진단 시작",
        operation,
    )

    def finish(result):
        if self._nte_core_diagnostic_worker is not worker:
            return
        if button is not None:
            button.setEnabled(True)
            button.setText("nte-core 진단")
        if not accept_result():
            return
        if result.get("error"):
            self.operation_unavailable("nte-core 진단", str(result["error"]), target="detection")
            return
        detected = result.get("capture_detect")
        devices = capture_device_names(detected) if isinstance(detected, dict) else []
        log_event(
            "INFO",
            "environment.nte_core_diagnostics_succeeded",
            "nte-core 진단 완료",
            operation,
            capture_device_count=len(devices),
            diagnostic_section_count=len(result),
        )
        if not (self.work_mode_service.allowed("packet_capture") or self.work_mode_service.allowed("diagnostics")):
            return
        self._show_nte_core_diagnostic_report(
            format_nte_core_diagnostics(result),
            devices,
            allow_manual_device_selection=bool(
                devices
                and isinstance(detected, dict)
                and detected.get("recommended_device") is None
            ),
        )

    def failed(error):
        if self._nte_core_diagnostic_worker is not worker:
            return
        if button is not None:
            button.setEnabled(True)
            button.setText("nte-core 진단")
        if not accept_result():
            return
        log_event(
            "ERROR",
            "environment.nte_core_diagnostics_failed",
            "nte-core 진단 실패",
            operation,
            error=error,
        )
        self.operation_unavailable(
            "nte-core 진단", "진단이 완료되지 않았습니다; 다시 검사하세요. 계속 실패하면 계정 로그를 확인하세요.",
            target="detection",
        )

    worker.result_ready.connect(finish)
    worker.error.connect(failed)
    worker.start()


def _show_nte_core_diagnostic_report(
    self: Any,
    report: str,
    devices: list[str] | None = None,
    *,
    allow_manual_device_selection: bool = False,
) -> None:
    dialog = QDialog(self)
    dialog.setWindowTitle("nte-core 진단 결과")
    dialog.resize(720, 510)
    layout = QVBoxLayout(dialog)
    hint = QLabel(
        "보고서에는 패킷 캡처에 필요한 코어, Npcap 드라이버, 네트워크 어댑터, DLL 단서만 남깁니다."
        "패킷 캡처를 시작하거나 원본 데이터를 저장하거나 IP/MAC 주소를 표시하지 않습니다."
    )
    hint.setWordWrap(True)
    layout.addWidget(hint)
    content = QPlainTextEdit(dialog)
    content.setReadOnly(True)
    content.setPlainText(report)
    layout.addWidget(content, 1)
    actions = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
    if devices and allow_manual_device_selection:
        select_device_button = cast(
            QAbstractButton,
            actions.addButton("고급 문제 해결…", QDialogButtonBox.ActionRole),
        )

        def select_capture_device() -> None:
            if not _require_environment_diagnostics(self, packet=True, label="캡처 어댑터 직접 지정"):
                return
            proceed = QMessageBox.question(
                dialog,
                "고급 문제 해결",
                "네트워크 어댑터를 직접 지정하면 자동 선택을 덮어쓰며 동기화가 실패할 수 있습니다."
                "자동 선택이 반복해서 실패할 때만 계속하세요.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if proceed != QMessageBox.Yes:
                return
            selected, accepted = QInputDialog.getItem(
                dialog,
                "캡처 어댑터 직접 지정",
                "진단에서 확인된 어댑터를 선택하세요:",
                devices,
                0,
                False,
            )
            if not accepted:
                return
            capture_device_edit = getattr(self, "_sync_capture_device_edit", None)
            if capture_device_edit is None:
                QMessageBox.warning(
                    self,
                    "고급 문제 해결",
                    "“캡처 네트워크 어댑터” 설정을 찾지 못했습니다. 설정 페이지를 다시 연 뒤 다시 시도하세요.",
                )
                return
            capture_device_edit.setText(selected)
            save_diagnostics = getattr(self, "_save_capture_diagnostics", None)
            if callable(save_diagnostics):
                save_diagnostics()
            QMessageBox.information(
                self,
                "고급 문제 해결",
                "캡처용 네트워크 어댑터를 저장했습니다; 동기화를 재시작하면 적용됩니다.",
            )

        select_device_button.clicked.connect(select_capture_device)
    copy_button = cast(
        QAbstractButton,
        actions.addButton("진단 복사", QDialogButtonBox.ActionRole),
    )
    copy_button.clicked.connect(lambda: QApplication.clipboard().setText(report))
    actions.rejected.connect(dialog.reject)
    layout.addWidget(actions)
    dialog.exec()


def _deploy_equipment_plugin(self):
    if not self.operation_entry("native_load", "게임 내 컴포넌트 배포"):
        return
    try:
        self.work_mode_service.require("native_load")
    except PermissionError as error:
        QMessageBox.warning(self, "게임 내 컴포넌트 배포", str(error))
        return
    executable = self.work_mode_service.settings.game_executable
    if not executable.strip():
        self.operation_unavailable("게임 내 컴포넌트 배포", "게임 실행 파일 HTGame.exe가 아직 선택되지 않았습니다.", target="deployment")
        return
    try:
        running = game_process_running()
    except EquipmentPluginDeploymentError as error:
        QMessageBox.warning(self, "장비 플러그인 배포", str(error))
        return
    if running:
        QMessageBox.warning(
            self,
            "장비 플러그인 배포",
            "게임이 실행 중입니다.\n게임을 완전히 종료한 뒤 플러그인을 배포하세요.",
        )
        return
    deploy_native_plugin_from_settings(self)


def _cleanup_equipment_plugin(self):
    self.work_mode_controller.cleanup()


def _focus_environment_configuration(self):
    self._go("settings")
    scroll = getattr(self, "_settings_scroll", None)
    card = getattr(self, "_environment_configuration_card", None)
    if scroll is not None and card is not None:
        QTimer.singleShot(0, lambda: scroll.verticalScrollBar().setValue(card.y()))


class EnvironmentControllerMixin:
    _refresh_equipment_plugin_status = _refresh_equipment_plugin_status
    _equipment_plugin_loading_method_changed = (
        equipment_plugin_loading_method_changed
    )
    _activate_equipment_plugin_loading_method = (
        activate_equipment_plugin_loading_method
    )
    _deactivate_equipment_plugin_loading_method = (
        deactivate_equipment_plugin_loading_method
    )
    _start_equipment_mod_loader = start_equipment_mod_loader
    _stop_equipment_mod_loader = stop_equipment_mod_loader
    _select_equipment_plugin_game_executable = _select_equipment_plugin_game_executable
    _detect_equipment_plugin_game_executable = _detect_equipment_plugin_game_executable
    _open_npcap_download = _open_npcap_download
    _show_npcap_status = _show_npcap_status
    _diagnose_nte_core = _diagnose_nte_core
    _show_nte_core_diagnostic_report = _show_nte_core_diagnostic_report
    _deploy_equipment_plugin = _deploy_equipment_plugin
    _cleanup_equipment_plugin = _cleanup_equipment_plugin
    _focus_environment_configuration = _focus_environment_configuration
