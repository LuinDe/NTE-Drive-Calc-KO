# 从 MainWindow 抽离的控制器方法。
"""Compatibility-installed MainWindow controller."""

from __future__ import annotations

from dataclasses import asdict
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
from src.observability.context import OperationContext
from src.observability.operation import log_event
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    deploy_plugin,
    find_game_executables,
    game_process_running,
    npcap_installation_present,
    packaged_mod_workspace,
    packaged_plugin_dll,
    restore_plugin,
)
from src.services.dwmapi_diagnostics import (
    collect_dwmapi_diagnostics,
    format_dwmapi_diagnostics,
)
from src.services.nte_core_diagnostics import (
    capture_device_names,
    collect_nte_core_diagnostics,
    format_nte_core_diagnostics,
)
from src.services.mod_plugin_loading_service import ModPluginLoadingError
from src.ui.controllers.mod_loader_controller import (
    activate_equipment_plugin_loading_method,
    deactivate_equipment_plugin_loading_method,
    equipment_plugin_loading_method_changed,
    equipment_plugin_risk_acknowledgement_changed,
    selected_plugin_loading_method,
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


def _refresh_equipment_plugin_status(self):
    label = getattr(self, "_npcap_status_label", None)
    if label is not None:
        label.setText(
            "Npcap: 감지됨" if npcap_installation_present()
            else "Npcap: 감지되지 않음 (공식 설치 프로그램으로 설치하세요)"
        )
    plugin_label = getattr(self, "_equipment_plugin_status_label", None)
    if plugin_label is None:
        return
    executable = getattr(self, "_equipment_plugin_game_executable_edit", None)
    bundle_label = getattr(self, "_equipment_plugin_bundle_label", None)
    if executable is None:
        return
    bundled_plugin = None
    loader_snapshot = None
    try:
        bundled_plugin = packaged_plugin_dll(self.app_context.paths.root)
        packaged_mod_workspace(self.app_context.paths.root)
    except EquipmentPluginDeploymentError:
        pass
    try:
        loader_snapshot = self._mod_plugin_loading_service.snapshot()
    except ModPluginLoadingError:
        pass
    if bundle_label is not None:
        if bundled_plugin is None:
            bundle_label.setText("패키지 플러그인 없음: 전체 앱 패키지를 다시 설치하세요")
        else:
            loader_text = "Loader 상태 알 수 없음"
            if loader_snapshot is not None:
                loader_text = (
                    "Loader 패키지됨"
                    if loader_snapshot.phase
                    not in {"missing_loader", "unsupported"}
                    else "Loader 사용 불가"
                )
            bundle_label.setText(
                f"패키지 플러그인과 Mod 스크립트: {bundled_plugin}; {loader_text}"
            )
    method = selected_plugin_loading_method(self)
    if loader_snapshot is not None and loader_snapshot.phase == "running":
        method = "loader"
        method_combo = getattr(
            self, "_equipment_plugin_loading_method_combo", None
        )
        if method_combo is not None and method_combo.currentData() != "loader":
            method_combo.blockSignals(True)
            method_combo.setCurrentIndex(
                max(0, method_combo.findData("loader"))
            )
            method_combo.blockSignals(False)
    primary = getattr(self, "_equipment_plugin_primary_button", None)
    stop = getattr(self, "_equipment_plugin_stop_button", None)
    if primary is not None:
        primary.setText(
            "Mod Loader 시작" if method == "loader" else "프록시 DLL 배포"
        )
    if stop is not None:
        stop.setText(
            "Mod Loader 중지" if method == "loader" else "게임 디렉터리 복원"
        )
    if loader_snapshot is not None and loader_snapshot.phase == "running":
        plugin_label.setText(
            "Mod Loader 모니터링 프로세스가 실행 중입니다. 진단에서 장비 IPC 파이프가 있다고 확인되어야만"
            "게임 플러그인이 로드된 것으로 볼 수 있습니다."
        )
    elif not executable.text().strip():
        plugin_label.setText("아직 HTGame.exe를 선택하지 않음")
    elif bundled_plugin is None:
        plugin_label.setText("앱 루트 디렉터리에 패키지된 dwmapi.dll이 없어 배포할 수 없습니다")
    elif method == "loader" and (
        loader_snapshot is None
        or loader_snapshot.phase in {"missing_loader", "unsupported"}
    ):
        loader_detail = (
            loader_snapshot.detail
            if loader_snapshot is not None
            else "Loader 상태를 읽을 수 없습니다"
        )
        plugin_label.setText(
            "Mod Loader를 현재 사용할 수 없습니다:" + loader_detail
        )
    else:
        plugin_label.setText(
            "게임 디렉터리를 선택했습니다;"
            + (
                "Loader를 시작하기 전에 게임 디렉터리에 프록시 dwmapi.dll이 없는지 먼저 확인하세요"
                if method == "loader"
                else "프록시 DLL을 배포하기 전에 확인이 필요합니다"
            )
        )


def _select_equipment_plugin_game_executable(self):
    selected, _ = QFileDialog.getOpenFileName(
        self, "게임 실행 파일 선택", "", "HTGame.exe (HTGame.exe)"
    )
    if selected:
        self._equipment_plugin_game_executable_edit.setText(selected)
        self._ui_preferences["equipment_plugin_game_executable"] = selected
        self._save_ui_preferences()
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
        self._ui_preferences["equipment_plugin_game_executable"] = selected
        self._save_ui_preferences()
        self._refresh_equipment_plugin_status()
        self._equipment_plugin_status_label.setText(
            f"게임 실행 파일을 자동으로 찾아 저장했습니다: {selected}"
        )

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
            f"자동 감지 실패: {error}\n\n"
            "직접 입력하거나 파일을 선택할 수 있습니다:\n"
            "1. 바탕 화면의 게임 아이콘을 마우스 오른쪽 버튼으로 클릭해 “파일 위치 열기”를 선택하세요.\n"
            "2. Client\\WindowsNoEditor\\HT\\Binaries\\Win64로 들어가 HTGame.exe를 찾으세요.\n"
            "3. HTGame.exe를 마우스 오른쪽 버튼으로 클릭해 “경로로 복사”를 선택한 뒤 게임 실행 파일 칸에 붙여넣으세요.",
        )

    worker.result_ready.connect(finish)
    worker.error.connect(failed)
    worker.start()

def _open_npcap_download(self):
    self._open_url("https://npcap.com/dist/npcap-1.88.exe")

def _show_npcap_status(self):
    if npcap_installation_present():
        QMessageBox.information(
            self, "Npcap 상태", "Npcap이 감지되어 가방 동기화 환경의 이 의존성은 충족되었습니다."
        )
        return
    QMessageBox.warning(
        self,
        "Npcap 상태",
        "Npcap이 감지되지 않았습니다. 가방 동기화가 로컬 코어 구성 요소로 게임 데이터를 읽을 수 없습니다."
        "“Npcap 1.88 다운로드”를 클릭해 설치를 마친 뒤 다시 확인하세요.",
    )


def _diagnose_nte_core(self):
    current_worker = getattr(self, "_nte_core_diagnostic_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "nte-core 진단", "진단이 진행 중입니다. 잠시 기다리세요.")
        return
    button = getattr(self, "_nte_core_diagnostic_button", None)
    if button is not None:
        button.setEnabled(False)
        button.setText("진단 중…")
    worker = WorkerThread(
        target=lambda: collect_nte_core_diagnostics(
            cwd=self.app_context.paths.app_dir
        ),
        parent=self,
    )
    self._nte_core_diagnostic_worker = worker
    operation = _new_environment_operation(self, "nte_core_diagnostics")
    log_event(
        "INFO",
        "environment.nte_core_diagnostics_started",
        "nte-core 진단 시작",
        operation,
    )

    def finish(result):
        if button is not None:
            button.setEnabled(True)
            button.setText("nte-core 진단")
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
        if button is not None:
            button.setEnabled(True)
            button.setText("nte-core 진단")
        log_event(
            "ERROR",
            "environment.nte_core_diagnostics_failed",
            "nte-core 진단 실패",
            operation,
            error=error,
        )
        QMessageBox.warning(self, "nte-core 진단", f"진단 프로그램 실행 실패: {error}")

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
            QMessageBox.information(
                self,
                "고급 문제 해결",
                "캡처 어댑터를 입력했습니다. “동기화 설정 저장”을 클릭한 뒤 동기화를 다시 시작하세요.",
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


def _diagnose_dwmapi(self):
    current_worker = getattr(self, "_dwmapi_diagnostic_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "dwmapi 진단", "진단이 진행 중입니다. 잠시 기다리세요.")
        return
    executable_edit = getattr(self, "_equipment_plugin_game_executable_edit", None)
    executable = executable_edit.text().strip() if executable_edit is not None else ""
    button = getattr(self, "_dwmapi_diagnostic_button", None)
    if button is not None:
        button.setEnabled(False)
        button.setText("진단 중…")
    preferences = getattr(self, "_ui_preferences", {}) or {}
    try:
        runtime_snapshot = asdict(self._mod_plugin_loading_service.snapshot())
        runtime_snapshot["loader_path"] = str(runtime_snapshot["loader_path"])
        runtime_snapshot["payload_path"] = str(runtime_snapshot["payload_path"])
    except (EquipmentPluginDeploymentError, ModPluginLoadingError) as exc:
        runtime_snapshot = {
            "phase": "probe_error",
            "detail": str(exc),
        }
    worker = WorkerThread(
        target=lambda: collect_dwmapi_diagnostics(
            game_executable_path=executable,
            application_root=self.app_context.paths.root,
            recorded_deployed_sha256=str(
                preferences.get("equipment_plugin_deployed_sha256") or ""
            ),
            recorded_workspace_path=str(
                preferences.get("equipment_plugin_workspace") or ""
            ),
            loading_method=selected_plugin_loading_method(self),
            loader_snapshot=runtime_snapshot,
        ),
        parent=self,
    )
    self._dwmapi_diagnostic_worker = worker
    operation = _new_environment_operation(self, "dwmapi_diagnostics")
    log_event(
        "INFO",
        "environment.dwmapi_diagnostics_started",
        "장비 플러그인 진단 시작",
        operation,
        game_executable_configured=bool(executable),
    )

    def finish(result):
        if button is not None:
            button.setEnabled(True)
            button.setText("dwmapi 진단")
        log_event(
            "INFO",
            "environment.dwmapi_diagnostics_succeeded",
            "장비 플러그인 진단 완료",
            operation,
            diagnostic_section_count=len(result),
        )
        self._show_dwmapi_diagnostic_report(format_dwmapi_diagnostics(result))

    def failed(error):
        if button is not None:
            button.setEnabled(True)
            button.setText("dwmapi 진단")
        log_event(
            "ERROR",
            "environment.dwmapi_diagnostics_failed",
            "장비 플러그인 진단 실패",
            operation,
            error=error,
        )
        QMessageBox.warning(self, "dwmapi 진단", f"진단 프로그램 실행 실패: {error}")

    worker.result_ready.connect(finish)
    worker.error.connect(failed)
    worker.start()


def _show_dwmapi_diagnostic_report(self: Any, report: str) -> None:
    dialog = QDialog(self)
    dialog.setWindowTitle("Mods 플러그인 로드 진단 결과")
    dialog.resize(760, 540)
    layout = QVBoxLayout(dialog)
    hint = QLabel(
        "아래 정보는 그대로 복사해 문제 해결용으로 보낼 수 있습니다. 이 작업은 장비 조작, Loader 시작, DLL 복사·수정을 실행하지 않습니다."
    )
    hint.setWordWrap(True)
    layout.addWidget(hint)
    content = QPlainTextEdit(dialog)
    content.setReadOnly(True)
    content.setPlainText(report)
    layout.addWidget(content, 1)
    actions = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
    copy_button = cast(
        QAbstractButton,
        actions.addButton("진단 복사", QDialogButtonBox.ActionRole),
    )
    copy_button.clicked.connect(lambda: QApplication.clipboard().setText(report))
    actions.rejected.connect(dialog.reject)
    layout.addWidget(actions)
    dialog.exec()

def _deploy_equipment_plugin(self):
    consent = getattr(self, "_equipment_plugin_consent", None)
    if consent is None or not consent.isChecked():
        QMessageBox.warning(
            self,
            "장비 플러그인 배포",
            "먼저 위험 안내를 읽고, 장비 플러그인을 자발적으로 사용하며 그에 따른 위험을 감수한다는 확인란을 체크하세요.",
        )
        return
    executable = self._equipment_plugin_game_executable_edit.text().strip()
    if game_process_running():
        QMessageBox.warning(
            self,
            "장비 플러그인 배포",
            "게임이 실행 중입니다.\n게임을 완전히 종료한 뒤 플러그인을 배포하세요.",
        )
        return
    try:
        self._mod_plugin_loading_service.ensure_proxy_deployment_allowed()
        source = packaged_plugin_dll(self.app_context.paths.root)
    except (EquipmentPluginDeploymentError, ModPluginLoadingError) as exc:
        QMessageBox.warning(self, "장비 플러그인 배포", str(exc))
        return
    if QMessageBox.question(
        self,
        "장비 플러그인 배포 확인",
        "장비 플러그인을 선택한 게임 디렉터리에 배포합니다.\n"
        "같은 이름의 파일이 있으면 자동으로 백업합니다.\n\n"
        "계속할까요?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    ) != QMessageBox.Yes:
        return
    operation = _new_environment_operation(self, "equipment_plugin")
    log_event(
        "INFO",
        "environment.plugin_deploy_started",
        "장비 플러그인 배포 시작",
        operation,
        game_executable_configured=bool(executable),
    )
    try:
        deployed = deploy_plugin(
            game_executable_path=executable,
            plugin_dll_path=source,
            application_root=self.app_context.paths.root,
            writable_workspace_path=(
                self.app_context.paths.config_dir / "mods-plugin"
            ),
            backup_directory=(
                self.app_context.account.account_data_root
                / "equipment_plugin_backups"
            ),
        )
        prior_workspace = str(
            self._ui_preferences.get("equipment_plugin_workspace") or ""
        )
        prior_hash = str(
            self._ui_preferences.get("equipment_plugin_deployed_sha256") or ""
        )
        registry_value_before = deployed.workspace_registry_value_before
        registry_value_existed = deployed.workspace_registry_value_existed
        if prior_hash and prior_workspace == str(deployed.workspace_path):
            registry_value_before = (
                self._ui_preferences.get(
                    "equipment_plugin_workspace_registry_value_before"
                )
                or None
            )
            registry_value_existed = bool(
                self._ui_preferences.get(
                    "equipment_plugin_workspace_registry_value_existed"
                )
            )
        self._ui_preferences.update({
            "equipment_plugin_game_executable": str(deployed.game_executable),
            "equipment_plugin_dll_source": str(source),
            "equipment_plugin_backup_path": str(deployed.backup_path or ""),
            "equipment_plugin_deployed_sha256": deployed.deployed_sha256,
            "equipment_plugin_workspace": str(deployed.workspace_path),
            "equipment_plugin_workspace_registry_value_before": registry_value_before or "",
            "equipment_plugin_workspace_registry_value_existed": registry_value_existed,
        })
        self._save_ui_preferences()
        log_event(
            "INFO",
            "environment.plugin_deploy_succeeded",
            "장비 플러그인 배포 완료",
            operation,
            backup_created=bool(deployed.backup_path),
            registry_value_existed=bool(registry_value_existed),
        )
        self._equipment_plugin_status_label.setText("최신 Mod 플러그인과 장비 스크립트를 배포했습니다. 게임 종료 전에 여기서 복원할 수 있습니다.")
        QMessageBox.information(
            self,
            "장비 플러그인 배포",
            f"dwmapi.dll을 배포하고 Mod 작업 공간을 등록했습니다:\n{deployed.workspace_path}",
        )
    except EquipmentPluginDeploymentError as exc:
        log_event(
            "ERROR",
            "environment.plugin_deploy_failed",
            "장비 플러그인 배포 실패",
            operation,
            error=exc,
        )
        QMessageBox.warning(self, "장비 플러그인 배포", str(exc))


def _restore_equipment_plugin(self):
    preferences = self._ui_preferences or {}
    executable = self._equipment_plugin_game_executable_edit.text().strip()
    deployed_sha256 = str(preferences.get("equipment_plugin_deployed_sha256") or "")
    if not executable or not deployed_sha256:
        QMessageBox.information(self, "장비 플러그인 복원", "현재 계정에 복원할 배포 기록이 없습니다.")
        return
    if QMessageBox.question(
        self, "장비 플러그인 복원",
        "배포 전에 백업한 dwmapi.dll을 복원합니다. 백업이 없으면 이 프로그램이 배포한 파일만 삭제합니다.\n"
        "Mod 작업 공간을 이 프로그램이 아직 보유 중이면 배포 전 레지스트리 값도 복원합니다.",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
    ) != QMessageBox.Yes:
        return
    operation = _new_environment_operation(self, "equipment_plugin")
    log_event(
        "INFO",
        "environment.plugin_restore_started",
        "장비 플러그인 복원 시작",
        operation,
        backup_configured=bool(preferences.get("equipment_plugin_backup_path")),
    )
    try:
        workspace_restored = restore_plugin(
            game_executable_path=executable,
            deployed_sha256=deployed_sha256,
            backup_path=preferences.get("equipment_plugin_backup_path"),
            mod_workspace_path=preferences.get("equipment_plugin_workspace") or None,
            workspace_registry_value_before=preferences.get(
                "equipment_plugin_workspace_registry_value_before"
            ) or None,
            workspace_registry_value_existed=bool(
                preferences.get("equipment_plugin_workspace_registry_value_existed")
            ),
        )
        self._ui_preferences.update({
            "equipment_plugin_backup_path": "",
            "equipment_plugin_deployed_sha256": "",
            "equipment_plugin_workspace": "",
            "equipment_plugin_workspace_registry_value_before": "",
            "equipment_plugin_workspace_registry_value_existed": False,
        })
        self._save_ui_preferences()
        log_event(
            "INFO",
            "environment.plugin_restore_succeeded",
            "장비 플러그인 복원 완료",
            operation,
            workspace_restored=bool(workspace_restored),
        )
        self._equipment_plugin_status_label.setText("게임 디렉터리의 dwmapi.dll을 복원했습니다.")
        QMessageBox.information(
            self,
            "장비 플러그인 복원",
            "복원을 완료했습니다."
            + (
                "이전 Mod 작업 공간도 복원했습니다."
                if workspace_restored
                else "Mod 작업 공간을 다른 프로그램이 넘겨받았거나 존재하지 않아 레지스트리 값을 수정하지 않았습니다."
            ),
        )
    except EquipmentPluginDeploymentError as exc:
        log_event(
            "ERROR",
            "environment.plugin_restore_failed",
            "장비 플러그인 복원 실패",
            operation,
            error=exc,
        )
        QMessageBox.warning(self, "장비 플러그인 복원", str(exc))


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
    _equipment_plugin_risk_acknowledgement_changed = (
        equipment_plugin_risk_acknowledgement_changed
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
    _diagnose_dwmapi = _diagnose_dwmapi
    _show_dwmapi_diagnostic_report = _show_dwmapi_diagnostic_report
    _deploy_equipment_plugin = _deploy_equipment_plugin
    _restore_equipment_plugin = _restore_equipment_plugin
    _focus_environment_configuration = _focus_environment_configuration
