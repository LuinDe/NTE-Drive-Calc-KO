# 编排游戏界面自动装配的确认、账号投影、后台执行和结果恢复。
"""Controller helpers for step-by-step game UI equipment assembly."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from src.app.theme import current_style_sheet
from src.app.workers import WorkerThread
from src.features.drive_assembly.ui_bridge import (
    execute_all_roles_from_current_game_page,
    execute_selected_role_from_current_game_page,
)
from src.features.inventory.equipment_assembly_dialogs import (
    assembly_report_dialog as _assembly_report_dialog,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.loadout_slot_selection_service import LoadoutSlotSelectionService
from src.utils.logger import logger

from .equipment_plan_optimizer import _sqlite_plan_display_state
from .equipment_slot_selection_dialog import select_assembly_slot_ids


def _return_to_equipment_after_assembly(window: Any) -> None:
    """Restore the calculator window and return to the equipment page."""

    show_normal = getattr(window, "showNormal", None)
    if callable(show_normal):
        show_normal()
    go_to_page = getattr(window, "_go", None)
    if callable(go_to_page):
        go_to_page("equipment")
    raise_window = getattr(window, "raise_", None)
    if callable(raise_window):
        raise_window()
    activate_window = getattr(window, "activateWindow", None)
    if callable(activate_window):
        activate_window()


def _prompt_protagonist_alias_if_needed(
    window: Any,
    role_names: list[str],
) -> dict[str, str]:
    roles = {str(role).strip() for role in (role_names or []) if str(role).strip()}
    protagonist_roles = roles.intersection({"主角", "零", "「零」"})
    if not protagonist_roles:
        return {}
    preferences = getattr(window, "_ui_preferences", {}) or {}
    default_name = str(
        preferences.get("protagonist_game_name")
        or getattr(window, "_drive_assembly_protagonist_name", "")
        or ""
    ).strip()
    if default_name:
        window._drive_assembly_protagonist_name = default_name
        return {role_name: default_name for role_name in protagonist_roles}

    dialog = QDialog(window)
    dialog.setWindowTitle("주인공 이름")
    dialog.setStyleSheet(current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("「제로」는 게임 안에서 플레이어 이름으로 표시됩니다. 그 이름을 입력한 뒤 자동 장착을 계속하세요."))
    name_edit = QLineEdit()
    name_edit.setPlaceholderText("게임 내 주인공 이름")
    layout.addWidget(name_edit)
    dont_remind = QCheckBox("이 이름을 기억하고 다시 묻지 않기")
    layout.addWidget(dont_remind)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.Accepted:
        return {}
    player_name = name_edit.text().strip()
    if not player_name:
        QMessageBox.warning(window, "주인공 이름", "주인공이 게임에서 표시되는 이름을 입력해야 합니다.")
        return {}
    window._drive_assembly_protagonist_name = player_name
    if isinstance(preferences, dict):
        preferences["protagonist_game_name"] = player_name
        preferences["skip_protagonist_name_prompt"] = bool(dont_remind.isChecked())
        saver = getattr(window, "_save_ui_preferences", None)
        if callable(saver):
            saver()
    return {role_name: player_name for role_name in protagonist_roles}


def _account_database_path(window: Any) -> Path:
    app_context = getattr(window, "app_context", None)
    if app_context is None:
        explicit = getattr(window, "user_database_path", None)
        if explicit is None:
            raise RuntimeError("장착 컨트롤러에 현재 계정 데이터베이스 의존성이 없습니다")
        return Path(explicit)
    return Path(app_context.account.user_database_path)


def _assembly_runtime_paths(window: Any) -> tuple[Path, Path]:
    """Return role-template and run-record roots for the active account."""

    app_context = getattr(window, "app_context", None)
    if app_context is None:
        template_dir = getattr(window, "role_template_dir", None)
        screenshot_dir = getattr(window, "screenshot_dir", None)
        if template_dir is None or screenshot_dir is None:
            raise RuntimeError("자동 장착에 캐릭터 템플릿 또는 현재 계정 스크린샷 디렉터리 의존성이 없습니다")
        return Path(template_dir), Path(screenshot_dir) / "record"
    return (
        Path(app_context.paths.template_dir) / "roles",
        Path(app_context.account.screenshot_dir) / "record",
    )


def _sqlite_automatic_assembly_state(
    database_path: str | Path,
    role_names: list[str],
    *,
    slot_ids: list[int] | None = None,
) -> dict[str, dict[str, Any]]:
    """从 SQLite 已保存方案构建自动装配动作所需的只读投影。"""

    with UserDataDao(database_path) as user_dao, StaticGameDataDao() as static_dao:
        states: dict[str, dict[str, Any]] = {}
        if slot_ids:
            plans_by_role = [
                (selection.role_name, dict(selection.plan))
                for selection in LoadoutSlotSelectionService(user_dao).resolve(slot_ids)
            ]
        else:
            plans_by_role = [
                (selection.role_name, dict(selection.plan))
                for selection in LoadoutSlotSelectionService(user_dao).resolve_default_roles(role_names)
            ]
        for role_name, plan in plans_by_role:
            states[role_name] = _sqlite_plan_display_state(
                plan,
                user_dao,
                static_dao,
            )
    return states


def _start_automatic_equipment_assembly(
    window: Any,
    role_names: list[str],
    *,
    slot_ids: list[int] | None = None,
) -> None:
    """在工作线程中执行逐步游戏界面自动装配。"""

    current_worker = getattr(window, "_automatic_equipment_apply_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(
            window,
            "자동 장착",
            "이미 자동 장착 작업이 실행 중입니다. 끝날 때까지 기다리세요.",
        )
        return
    try:
        state = _sqlite_automatic_assembly_state(
            _account_database_path(window),
            role_names,
            slot_ids=slot_ids,
        )
    except Exception as exc:
        QMessageBox.warning(window, "자동 장착", f"공식 SQLite 방안을 읽을 수 없습니다: {exc}")
        return

    execution_role_names = list(state)
    aliases = _prompt_protagonist_alias_if_needed(window, execution_role_names)
    protagonist_names = {"主角", "零", "「零」"}
    if {str(role).strip() for role in execution_role_names}.intersection(
        protagonist_names
    ) and not aliases:
        return
    hotkey_manager = getattr(window, "global_hotkey_manager", None)
    configuration = getattr(hotkey_manager, "configuration", None)
    stop_hotkey = str(getattr(configuration, "stop", "전역 중지 키"))
    confirmation = QMessageBox.question(
        window,
        "자동 장착 준비",
        "게임 내 조작을 모사해 단계별로 장착합니다. 3초 안에 게임의 캐릭터 상세 페이지로 전환하고"
        f"게임 창을 계속 보이게 유지하세요. 실행 중에는 설정의 전역 중지 키({stop_hotkey})로 중지할 수 있습니다.\n\n"
        "게임에서 C 키 캐릭터 페이지가 열려 있고 게임 해상도가 1080p 또는 2K인지 확인하세요.",
        QMessageBox.Ok | QMessageBox.Cancel,
        QMessageBox.Cancel,
    )
    if confirmation != QMessageBox.Ok:
        return
    hotkey_owner = "automatic_equipment_apply"
    active_hotkey_owner = getattr(hotkey_manager, "active_owner", None)
    if active_hotkey_owner not in (None, hotkey_owner):
        QMessageBox.information(
            window,
            "자동 장착",
            "현재 전역 중지 키를 다른 작업이 사용 중입니다. 먼저 그 작업을 중지한 뒤 자동 장착을 시작하세요.",
        )
        return
    stop_requested = threading.Event()
    if hotkey_manager is not None:
        hotkey_manager.start(owner=hotkey_owner, on_stop=stop_requested.set)
    show_minimized = getattr(window, "showMinimized", None)
    if callable(show_minimized):
        show_minimized()

    def run() -> object:
        template_dir, record_root = _assembly_runtime_paths(window)
        if len(execution_role_names) == 1:
            return execute_selected_role_from_current_game_page(
                state,
                execution_role_names[0],
                template_dir=str(template_dir),
                record_root=record_root,
                role_name_aliases=aliases,
                should_stop=stop_requested.is_set,
            )
        return execute_all_roles_from_current_game_page(
            state,
            template_dir=str(template_dir),
            record_root=record_root,
            role_name_aliases=aliases,
            should_stop=stop_requested.is_set,
        )

    worker = WorkerThread(target=run, parent=window)
    window._automatic_equipment_apply_worker = worker

    def on_result(report: object) -> None:
        if hotkey_manager is not None:
            hotkey_manager.stop(owner=hotkey_owner)
        _return_to_equipment_after_assembly(window)
        title, message, completed = _assembly_report_dialog(
            "자동 장착",
            report,
            len(execution_role_names),
        )
        (QMessageBox.information if completed else QMessageBox.warning)(
            window,
            title,
            message,
        )
        refresh = getattr(window, "_refresh_equip", None)
        if callable(refresh):
            refresh()

    def on_error(message: str) -> None:
        if hotkey_manager is not None:
            hotkey_manager.stop(owner=hotkey_owner)
        _return_to_equipment_after_assembly(window)
        QMessageBox.critical(
            window,
            "자동 장착 실패",
            f"자동 장착을 완료하지 못했습니다:\n{message}",
        )

    worker.result_ready.connect(on_result)
    worker.error.connect(on_error)
    worker.start()


def _confirm_automatic_assembly_duplicate_warning(window: Any) -> bool:
    """Warn once that UI automation cannot resolve repeated drive placement."""

    preferences = getattr(window, "_ui_preferences", None)
    if isinstance(preferences, dict) and preferences.get(
        "skip_automatic_assembly_duplicate_warning"
    ):
        return True

    dialog = QMessageBox(window)
    dialog.setWindowTitle("자동 장착 안내")
    dialog.setIcon(QMessageBox.Warning)
    dialog.setText("자동 장착은 중복 드라이브 상황을 완벽하게 처리할 수 없습니다.")
    dialog.setInformativeText("실행이 끝난 뒤 중복 드라이브로 생긴 빈자리를 직접 채워 주세요.")
    dialog.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
    dialog.setDefaultButton(QMessageBox.Cancel)
    dont_remind = QCheckBox("다시 묻지 않기")
    dialog.setCheckBox(dont_remind)
    confirm_button = dialog.button(QMessageBox.Ok)
    dialog.exec()
    if dialog.clickedButton() is not confirm_button:
        return False
    if dont_remind.isChecked():
        if not isinstance(preferences, dict):
            preferences = {}
            window._ui_preferences = preferences
        preferences["skip_automatic_assembly_duplicate_warning"] = True
        saver = getattr(window, "_save_ui_preferences", None)
        if callable(saver):
            try:
                saver()
            except Exception as exc:
                logger.warning(f"자동 장착 안내 선호도 저장 실패: {exc}")
    return True


def _preview_automatic_assemble_role(
    window: Any,
    role_name: str,
    *,
    slot_id: int | None = None,
    confirmed: bool = False,
) -> None:
    """确认后通过游戏界面自动化装配一个角色。"""

    if not confirmed:
        result = QMessageBox.question(
            window,
            "자동 장착",
            f"게임 내 조작을 모사해 [{role_name}]을(를) 단계별로 장착합니다.\n\n"
            "장비 플러그인은 필요 없지만 게임 캐릭터 상세 페이지로 전환해야 하며 시간이 더 걸립니다."
            "실행 중에는 설정의 전역 중지 키로 중지할 수 있습니다. 계속할까요?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return
    if not _confirm_automatic_assembly_duplicate_warning(window):
        return
    _start_automatic_equipment_assembly(
        window,
        [role_name] if slot_id is None else [],
        slot_ids=[int(slot_id)] if slot_id is not None else None,
    )


def _preview_automatic_assemble_all_roles(
    window: Any,
    role_names: list[str] | None = None,
) -> None:
    """确认后通过游戏界面自动化装配全部已保存角色。"""

    requested_roles = tuple(
        dict.fromkeys(str(name) for name in (role_names or ()))
    )
    try:
        with UserDataDao(_account_database_path(window)) as user_dao:
            selection_service = LoadoutSlotSelectionService(user_dao)
            current_slots = selection_service.list_current()
            if requested_roles:
                available_roles = {selection.role_name for selection in current_slots}
                missing = [name for name in requested_roles if name not in available_roles]
                if missing:
                    QMessageBox.information(
                        window,
                        "자동 장착",
                        f"다음 캐릭터는 아직 현재 방안을 저장하지 않았습니다: {'、'.join(missing)}",
                    )
                    return
                current_slots = tuple(
                    selection
                    for selection in current_slots
                    if selection.role_name in requested_roles
                )
            selected_slot_ids = select_assembly_slot_ids(window, current_slots)
            if selected_slot_ids is None:
                return
            selections = selection_service.resolve(selected_slot_ids)
    except Exception as exc:
        QMessageBox.warning(
            window,
            "자동 장착",
            f"공식 SQLite 방안을 읽을 수 없습니다: {exc}",
        )
        return
    if not selections:
        QMessageBox.information(
            window,
            "자동 장착",
            "현재 공식 가방 스냅샷에서 온 저장된 방안이 없습니다. 먼저 다시 계산하고 저장하세요.",
        )
        return
    result = QMessageBox.question(
        window,
        "자동 장착",
        f"게임 내 조작을 모사해 캐릭터 {len(selected_slot_ids)}명을 순서대로 장착합니다.\n\n"
        "장비 플러그인은 필요 없지만 게임 캐릭터 상세 페이지로 전환해야 하며 시간이 더 걸립니다."
        "실행 중에는 설정의 전역 중지 키로 중지할 수 있습니다. 계속할까요?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if (
        result == QMessageBox.Yes
        and _confirm_automatic_assembly_duplicate_warning(window)
    ):
        _start_automatic_equipment_assembly(window, [], slot_ids=selected_slot_ids)
