# 处理设置页中的 Mods Plugin 加载方式与 Loader 会话交互。
"""UI intent handlers for the optional NTE Mod Loader."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QMessageBox

from src.observability.context import OperationContext
from src.observability.operation import log_event
from src.services.equipment_plugin_deployment import EquipmentPluginDeploymentError
from src.services.mod_plugin_loading_service import ModPluginLoadingError


def selected_plugin_loading_method(window: Any) -> str:
    combo = getattr(window, "_equipment_plugin_loading_method_combo", None)
    selected = combo.currentData() if combo is not None else None
    return "loader" if selected == "loader" else "proxy"


def _save_plugin_preference(window: Any, key: str, value: Any) -> None:
    preferences = getattr(window, "_ui_preferences", None)
    if not isinstance(preferences, dict):
        return
    preferences[key] = value
    window._save_ui_preferences()


def _new_loader_operation(window: Any) -> OperationContext:
    app_context = getattr(window, "app_context", None)
    return OperationContext.create(
        "mod_loader",
        account_id=(
            app_context.account.active_account_id
            if app_context is not None
            else None
        ),
        context_generation=(
            app_context.generation if app_context is not None else None
        ),
    )


def equipment_plugin_loading_method_changed(window: Any, _index: int) -> None:
    method = selected_plugin_loading_method(window)
    try:
        snapshot = window._mod_plugin_loading_service.snapshot()
    except (EquipmentPluginDeploymentError, ModPluginLoadingError):
        snapshot = None
    if snapshot is not None and snapshot.phase == "running" and method != "loader":
        combo = window._equipment_plugin_loading_method_combo
        combo.blockSignals(True)
        combo.setCurrentIndex(max(0, combo.findData("loader")))
        combo.blockSignals(False)
        QMessageBox.warning(
            window,
            "로드 방식 전환",
            "Mod Loader가 실행 중입니다. 먼저 “Mod Loader 중지”를 클릭한 뒤 프록시 DLL로 전환하세요.",
        )
        return
    if method != "loader":
        try:
            window._mod_plugin_loading_service.stop_loader()
        except ModPluginLoadingError as exc:
            combo = window._equipment_plugin_loading_method_combo
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData("loader")))
            combo.blockSignals(False)
            QMessageBox.warning(
                window,
                "로드 방식 전환",
                "Loader 세션 정리를 완료할 수 없어 Loader 방식을 유지했습니다:" + str(exc),
            )
            return
    _save_plugin_preference(window, "equipment_plugin_loading_method", method)
    window._refresh_equipment_plugin_status()


def equipment_plugin_risk_acknowledgement_changed(
    window: Any,
    checked: bool,
) -> None:
    _save_plugin_preference(
        window,
        "equipment_plugin_risk_acknowledged",
        bool(checked),
    )


def activate_equipment_plugin_loading_method(window: Any) -> None:
    if selected_plugin_loading_method(window) == "loader":
        window._start_equipment_mod_loader()
    else:
        window._deploy_equipment_plugin()


def deactivate_equipment_plugin_loading_method(window: Any) -> None:
    if selected_plugin_loading_method(window) == "loader":
        window._stop_equipment_mod_loader()
    else:
        window._restore_equipment_plugin()


def start_equipment_mod_loader(window: Any) -> None:
    consent = getattr(window, "_equipment_plugin_consent", None)
    if consent is None or not consent.isChecked():
        QMessageBox.warning(
            window,
            "Mod Loader 시작",
            "먼저 위험 안내를 읽고, 장비 플러그인을 자발적으로 사용하며 그에 따른 위험을 감수한다는 확인란을 체크하세요.",
        )
        return
    executable = window._equipment_plugin_game_executable_edit.text().strip()
    if QMessageBox.question(
        window,
        "예비 Mod Loader 시작 확인",
        "예비 Loader는 관리자 권한을 요청하고 공식 런처를 모니터링하며, HTGame.exe가 생성될 때"
        "패키지된 dwmapi.dll을 로드합니다. 게임 디렉터리에 DLL을 쓰지는 않습니다.\n\n"
        "Loader 파일은 사용자가 직접 교체할 수 있으며 프로그램은 고정 SHA-256을 검증하지 않습니다. 교체한 EXE도 여전히"
        "관리자 권한으로 실행되므로 신뢰할 수 있는 출처만 사용하세요.\n\n"
        "시작 전에 게임 디렉터리를 자동으로 검사합니다: 이 프로그램이 알고 있는 프록시 DLL은 바로 제거하고, 이전 버전이거나"
        "출처를 알 수 없는 dwmapi.dll은 먼저 현재 계정 저장 디렉터리에 백업해 검증한 뒤 게임 디렉터리에서"
        "제거합니다. 어느 단계라도 실패하면 Loader를 시작하지 않습니다.\n\n"
        "먼저 게임과 공식 런처를 종료하세요. Loader를 중지할 때 이번 세션에서 주입된 공식"
        "런처도 함께 종료될 수 있습니다.\n\n계속할까요?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    ) != QMessageBox.Yes:
        return
    operation = _new_loader_operation(window)
    log_event(
        "INFO",
        "environment.mod_loader_start_started",
        "예비 Mod Loader 시작 중",
        operation,
        game_executable_configured=bool(executable),
    )
    try:
        preferences = window._ui_preferences
        result = window._mod_plugin_loading_service.start_loader(
            game_executable_path=executable,
            writable_workspace_path=(
                window.app_context.paths.config_dir / "mods-plugin"
            ),
            proxy_backup_directory=(
                window.app_context.account.account_data_root
                / "equipment_plugin_backups"
            ),
            recorded_proxy_sha256=str(
                preferences.get("equipment_plugin_deployed_sha256") or ""
            ),
            recorded_proxy_workspace_path=(
                preferences.get("equipment_plugin_workspace") or None
            ),
            recorded_proxy_registry_value_before=(
                preferences.get(
                    "equipment_plugin_workspace_registry_value_before"
                )
                or None
            ),
            recorded_proxy_registry_value_existed=bool(
                preferences.get(
                    "equipment_plugin_workspace_registry_value_existed"
                )
            ),
        )
        window._ui_preferences.update({
            "equipment_plugin_game_executable": executable,
            "equipment_plugin_loading_method": "loader",
            "equipment_plugin_risk_acknowledged": True,
            "equipment_plugin_workspace": str(result.workspace_path),
            "equipment_plugin_backup_path": "",
            "equipment_plugin_deployed_sha256": "",
            "equipment_plugin_workspace_registry_value_before": "",
            "equipment_plugin_workspace_registry_value_existed": False,
        })
        window._save_ui_preferences()
        log_event(
            "INFO",
            "environment.mod_loader_start_succeeded",
            "예비 Mod Loader 모니터링 프로세스가 시작되었습니다",
            operation,
            loader_process_started=bool(result.runtime.process_id),
            local_proxy_removed=bool(result.removed_proxy),
            local_proxy_known=(
                result.removed_proxy.known
                if result.removed_proxy is not None
                else None
            ),
            local_proxy_backup_created=bool(
                result.removed_proxy is not None
                and result.removed_proxy.backup_path is not None
            ),
        )
        window._refresh_equipment_plugin_status()
        proxy_message = ""
        if result.removed_proxy is not None:
            if result.removed_proxy.backup_path is None:
                proxy_message = "\n\n게임 디렉터리에서 이 프로그램이 알고 있는 프록시 DLL을 제거했습니다."
            else:
                proxy_message = (
                    "\n\n이전 버전 또는 알 수 없는 DLL을 감지해 백업한 뒤 게임 디렉터리에서 옮겼습니다:\n"
                    + str(result.removed_proxy.backup_path)
                )
        QMessageBox.information(
            window,
            "Mod Loader 모니터링이 시작되었습니다",
            "현재 게임 설치의 공식 런처를 Loader에 명시적으로 제공했습니다. Loader 프로세스가 실행 중이라고 해서"
            "게임 플러그인이 로드된 것은 아닙니다. 게임을 평소처럼 시작한 뒤 “dwmapi 진단”으로"
            "nte-mods-plugin-v7 파이프가 나타나는지 확인하세요."
            + proxy_message,
        )
    except (EquipmentPluginDeploymentError, ModPluginLoadingError) as exc:
        log_event(
            "ERROR",
            "environment.mod_loader_start_failed",
            "예비 Mod Loader 시작 실패",
            operation,
            error=exc,
        )
        QMessageBox.warning(window, "Mod Loader 시작", str(exc))


def stop_equipment_mod_loader(window: Any) -> None:
    if QMessageBox.question(
        window,
        "Mod Loader 중지",
        "Loader를 중지하면 이번 Loader 세션이 종료됩니다. 업스트림 Loader가 이번 세션에서"
        "주입된 공식 런처도 함께 종료할 수 있습니다. 먼저 게임을 종료하세요.\n\n계속할까요?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    ) != QMessageBox.Yes:
        return
    operation = _new_loader_operation(window)
    log_event(
        "INFO",
        "environment.mod_loader_stop_started",
        "예비 Mod Loader 중지 중",
        operation,
    )
    try:
        stopped = window._mod_plugin_loading_service.stop_loader()
        log_event(
            "INFO",
            "environment.mod_loader_stop_succeeded",
            "예비 Mod Loader가 중지되었습니다",
            operation,
            loader_was_running=bool(stopped),
        )
        window._refresh_equipment_plugin_status()
        QMessageBox.information(
            window,
            "Mod Loader 중지",
            "Mod Loader가 중지되었습니다." if stopped else "이번 앱 세션에 실행 중인 Mod Loader가 없습니다.",
        )
    except ModPluginLoadingError as exc:
        log_event(
            "ERROR",
            "environment.mod_loader_stop_failed",
            "예비 Mod Loader 중지 실패",
            operation,
            error=exc,
        )
        QMessageBox.warning(window, "Mod Loader 중지", str(exc))
