# 处理设置页中的原生组件加载意图，记录只属于本机工作模式。
"""UI intent handlers for managed game component loading."""
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QMessageBox

from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
)
from src.services.mod_plugin_loading_service import ModPluginLoadingError


def selected_plugin_loading_method(window: Any) -> str:
    combo = getattr(window, "_equipment_plugin_loading_method_combo", None)
    selected = combo.currentData() if combo is not None else None
    if selected not in {"loader", "native-capture"}:
        selected = window.work_mode_service.deployment_record.get("loading_method")
    return "loader" if selected == "loader" else "native-capture"


def equipment_plugin_loading_method_changed(window: Any, _index: int) -> None:
    method = selected_plugin_loading_method(window)
    try:
        snapshot = window._mod_plugin_loading_service.snapshot()
        if snapshot.phase == "running" and method != "loader":
            combo = window._equipment_plugin_loading_method_combo
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData("loader")))
            combo.blockSignals(False)
            QMessageBox.warning(window, "로드 방식 전환", "Loader가 로드를 기다리는 중입니다. 먼저 현재 로드 진입점을 정리한 뒤 전환하세요.")
            return
        record = window.work_mode_service.deployment_record
        record["loading_method"] = method
        window.work_mode_service.update_deployment(record)
        window.work_mode_runtime.invalidate()
        window._refresh_equipment_plugin_status()
    except (EquipmentPluginDeploymentError, ModPluginLoadingError) as error:
        QMessageBox.warning(window, "로드 방식 전환", str(error))


def activate_equipment_plugin_loading_method(window: Any) -> None:
    if selected_plugin_loading_method(window) == "loader":
        window._start_equipment_mod_loader()
    else:
        window._deploy_equipment_plugin()


def deactivate_equipment_plugin_loading_method(window: Any) -> None:
    window.work_mode_controller.cleanup()


def start_equipment_mod_loader(window: Any) -> None:
    if not window.operation_entry("native_load", "Mod Loader 시작"):
        return
    try:
        window.work_mode_service.require("native_load")
    except PermissionError as error:
        QMessageBox.warning(window, "Mod Loader 시작", str(error))
        return
    executable = window.work_mode_service.settings.game_executable
    if not executable.strip():
        window.operation_unavailable("Mod Loader 시작", "게임 실행 파일 HTGame.exe가 아직 선택되지 않았습니다.", target="deployment")
        return
    from src.ui.controllers.native_plugin_deployment_ui import start_native_loader_from_settings
    start_native_loader_from_settings(window)


def stop_equipment_mod_loader(window: Any) -> None:
    """All stop/cleanup requests share the mode owner's pending lifecycle."""
    window.work_mode_controller.cleanup()
