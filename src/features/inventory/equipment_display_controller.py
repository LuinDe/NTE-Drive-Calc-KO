# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.optimizer.contracts import (
    DIFF_CHANGED,
    DIFF_ADDED,
    DIFF_REMOVED,
    EQUIP_DISPLAY_NAME,
    EQUIP_SET_NAME,
    EQUIP_UID,
    ROLE_EQUIPPED_DRIVES,
)
from src.features.inventory.equipment_display_context import equipment_presentation
from src.features.inventory.equipment_loadout_scoring import (
    score_equipment_display_state,
)
from src.app.theme import themed_style
from src.features.inventory.equipment_master_detail_view import (
    update_equipment_role_status,
)
from src.services.game_loadout_projection_service import (
    GameLoadoutImportRequest,
    GameLoadoutProjectionService,
)
from src.utils.logger import logger


__all__ = [
    "_equipment_compare_signature",
    "_same_equipment_by_ocr",
    "_page_equipment",
    "_set_equipment_mode",
    "_refresh_equip",
    "_saved_plan_diff_text",
    "_show_saved_plan_diff_dialog",
    "_clear_all_equipment",
    "_delete_role_equipment",
    "_optimize_saved_equipment",
    "_toggle_role_allocation_lock",
    "_manage_loadout_slot",
    "_import_game_loadout",
    "_import_all_game_loadouts",
    "reset_equipment_account_state",
]

EQUIPMENT_ROLE_PLACEHOLDER_HEIGHT = 520
EQUIPMENT_VIEWPORT_PREFETCH_COUNT = 1
# Legacy test hosts and non-Qt callers retain the old batch-only path.
EQUIPMENT_INITIAL_RENDER_COUNT = 8
EQUIPMENT_RENDER_BATCH_SIZE = 3

_OFFICIAL_STAT_LABELS = {
    "AtkAdd": "攻击力",
    "AtkUp": "攻击力%",
    "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%",
    "DamageUpChaosBase": "暗属性异能伤害增强%",
    "DamageUpCosmosBase": "光属性异能伤害增强%",
    "DamageUpGeneralBase": "伤害增加%",
    "DamageUpIncantationBase": "咒属性异能伤害增强%",
    "DamageUpLakshanaBase": "相属性异能伤害增强%",
    "DamageUpNatureBase": "灵属性异能伤害增强%",
    "DamageUpPsycheBase": "魂属性异能伤害增强%",
    "DamageUpPsychicallyBase": "心灵伤害增强%",
    "DefAdd": "防御力",
    "DefUp": "防御力%",
    "HealUp": "治疗加成",
    "HPMaxAdd": "生命值",
    "HPMaxUp": "生命值%",
    "MagBase": "环合强度",
    "UnbalIntensityBase": "倾陷强度",
}
_OFFICIAL_SHAPE_LABELS = {
    "hen2": "H_2",
    "hen3": "H_3",
    "hen4": "H_4",
    "shu2": "V_2",
    "shu3": "V_3",
    "shu4": "V_4",
    "z3": "Trap_4_H",
    "z4": "Trap_4_V",
    "zhijiao1": "L_3_BL",
    "zhijiao2": "L_3_TL",
    "zhijiao3": "L_3_TR",
    "zhijiao4": "L_3_BR",
}


from src.features.inventory.equipment_display_view import (
    _equipment_paths,
    _equipment_compare_signature,
    _same_equipment_by_ocr,
    _page_equipment,
    _request_equipment_graduation_rate,
    _set_equipment_mode,
    _refresh_equip,
)


from src.features.inventory.equipment_plan_optimizer import (
    _optimize_saved_equipment,
)




def _saved_plan_diff_text(self, role_name, diff):
    removed = diff.get(DIFF_REMOVED, []) or []
    added = diff.get(DIFF_ADDED, []) or []
    if not removed and not added:
        return "이번 저장은 이전 방안과 장비 변동이 없습니다."
    lines = [f"{role_name} 장비 변동:"]
    if removed:
        lines.append("\n해제:")
        lines.extend(f"- {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in removed)
    if added:
        lines.append("\n장착:")
        lines.extend(f"+ {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in added)
    return "\n".join(lines)


def _show_saved_plan_diff_dialog(self, role_name, diff):
    presentation = equipment_presentation(self)
    build_dialog = getattr(presentation, "plan_diff_dialog", None)
    if callable(build_dialog):
        build_dialog(role_name, diff).exec()
        return
    QMessageBox.information(self, "장비 변동", self._saved_plan_diff_text(role_name, diff))


def _clear_all_equipment(self):
    database_path = _equipment_paths(self)[0]
    with UserDataDao(database_path) as dao:
        slot_rows: list[dict[str, Any]] = getattr(
            dao,
            "list_current_loadout_slot_plans",
            lambda: [],
        )()
        plans = [
            (
                str((row.get("plan", {}).get("payload") or {}).get("source_role_name") or "알 수 없는 캐릭터"),
                str((row.get("slot") or {}).get("slot_name") or "이름 없는 슬롯"),
                row.get("plan") or {},
            )
            for row in slot_rows
        ]
        if not plans:
            plans = [
                (role_name, role_name, plan)
                for role_name, plan in dao.list_active_loadout_plans_by_role().items()
            ]
    if not plans:
        QMessageBox.information(self, "장비 세팅 비우기", "현재 저장된 장비 세팅이 없습니다.")
        return
    ret = QMessageBox.question(
        self,
        "장비 세팅 비우기",
        "현재 장비 세팅 페이지에서 저장된 방안을 모두 제거할까요?\n방안 기록과 작업 기록은 남지만 이 방안들은 더 이상 장착에 사용되지 않습니다.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret != QMessageBox.Yes:
        return
    skipped_locked = []
    with UserDataDao(database_path) as dao:
        for role_name, slot_name, plan in plans:
            if plan.get("allocation_locked"):
                skipped_locked.append(f"{role_name} · {slot_name}")
                continue
            dao.deactivate_loadout_plan(plan["plan_id"])
    self._saved_equipment_cache_valid = False
    self._refresh_equip()
    if skipped_locked:
        QMessageBox.information(
            self,
            "장비 세팅 비우기",
            "잠기지 않은 방안을 비웠습니다. 다음 방안은 계산 잠금 때문에 유지됩니다:" + "、".join(skipped_locked),
        )
    logger.success("잠기지 않은 캐릭터 장비 세팅을 모두 비웠습니다")


def invalidate_saved_equipment_cache(self: Any) -> None:
    """Public cross-feature hook after a persisted loadout mutation."""

    self._saved_equipment_cache_valid = False


def reset_equipment_account_state(self: Any) -> None:
    """Discard account-owned projections before the shell refreshes a page."""

    invalidate_saved_equipment_cache(self)
    self._equip_load_token = object()
    self._equipment_graduation_tokens = {}
    self._saved_equipment_states = {}
    self._game_loadout_states = {}
    self._equip_selected_role_by_mode = {}


def refresh_saved_equipment_after_mutation(
    self: Any,
    *,
    restore_role_name: str | None = None,
) -> None:
    """Refresh mutated loadouts while retaining the selected role."""

    if restore_role_name is None:
        mode = getattr(self, "_equipment_mode", "saved")
        selected_by_mode = getattr(self, "_equip_selected_role_by_mode", {})
        if isinstance(selected_by_mode, dict):
            selected = selected_by_mode.get(mode)
            restore_role_name = str(selected) if selected else None
    invalidate_saved_equipment_cache(self)
    self._refresh_equip(restore_role_name=restore_role_name)


def _delete_role_equipment(
    self: Any,
    role_name: str,
    *,
    plan_id: int | None = None,
) -> None:
    database_path = _equipment_paths(self)[0]
    with UserDataDao(database_path) as dao:
        plan = dao.get_loadout_plan(int(plan_id)) if plan_id is not None else dao.get_active_loadout_plan_for_role(role_name)
    if plan is None:
        self._refresh_equip()
        return
    ret = QMessageBox.question(
        self,
        "캐릭터 장비 세팅 삭제",
        f"현재 장비 세팅 페이지에서 [{role_name}]의 저장된 방안을 제거할까요?\n방안 기록은 남지만 이 방안은 더 이상 장착에 사용되지 않습니다.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret != QMessageBox.Yes:
        return
    try:
        with UserDataDao(database_path) as dao:
            dao.deactivate_loadout_plan(plan["plan_id"])
    except Exception as exc:
        QMessageBox.warning(self, "캐릭터 장비 세팅 삭제", str(exc))
        return
    self._saved_equipment_cache_valid = False
    self._refresh_equip()
    logger.success(f"캐릭터 장비 세팅 삭제됨: {role_name}")


def _manage_loadout_slot(
    self: Any,
    slot_id: int,
    *,
    role_name: str | None = None,
) -> None:
    """Open one character-scoped manager for its visible loadout slots."""

    database_path = _equipment_paths(self)[0]
    try:
        with UserDataDao(database_path) as dao:
            initial_slot = dao.get_loadout_slot(int(slot_id))
        if initial_slot is None or initial_slot.get("is_archived"):
            raise RuntimeError("현재 장비 세팅 슬롯이 더 이상 없습니다. 페이지를 새로 고치세요.")
    except Exception as exc:
        QMessageBox.warning(self, "장비 세팅 슬롯 관리", str(exc))
        return

    character_id = int(initial_slot["character_id"])
    character_label = role_name or str(initial_slot["slot_name"])
    if initial_slot.get("slot_key") == "primary" and initial_slot.get("slot_name") == "主力" and role_name:
        try:
            with UserDataDao(database_path) as dao:
                dao.rename_loadout_slot(int(initial_slot["slot_id"]), role_name)
            initial_slot["slot_name"] = role_name
        except Exception as exc:
            QMessageBox.warning(self, "장비 세팅 슬롯 관리", str(exc))
            return
    dialog = QDialog(self)
    dialog.setWindowTitle(f"{character_label} · 장비 세팅 슬롯 관리")
    dialog.setFixedWidth(360)
    layout = QVBoxLayout(dialog)
    layout.setSpacing(12)

    selector_row = QHBoxLayout()
    selector_row.addWidget(QLabel("슬롯 관리:"))
    selector = QComboBox(dialog)
    selector.setMinimumWidth(260)
    selector_row.addWidget(selector, 1)
    layout.addLayout(selector_row)

    plan_status = QLabel(dialog)
    plan_status.setWordWrap(True)
    plan_status.setStyleSheet(themed_style(
        "QLabel{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:9px;}"
    ))
    layout.addWidget(plan_status)

    actions = QHBoxLayout()
    create_button = QPushButton("슬롯 추가", dialog)
    rename_button = QPushButton("이름 바꾸기", dialog)
    archive_button = QPushButton("슬롯 삭제", dialog)
    archive_button.setStyleSheet(themed_style("QPushButton{color:#f85149}"))
    actions.addWidget(create_button)
    actions.addWidget(rename_button)
    actions.addWidget(archive_button)
    layout.addLayout(actions)

    changed = False
    slots: list[dict[str, Any]] = []

    def slot_label(slot: dict[str, Any]) -> str:
        if slot.get("slot_key") == "primary" and str(slot["slot_name"]) == "主力":
            return character_label
        return str(slot["slot_name"])

    def slot_suit_text(slot: dict[str, Any]) -> str:
        slot_state = next(
            (
                state
                for state in (getattr(self, "_saved_equipment_states", {}) or {}).values()
                if isinstance(state, dict)
                and state.get("_loadout_slot_id") == slot["slot_id"]
            ),
            {},
        )
        suit_names = {
            str(drive.get(EQUIP_SET_NAME)).strip()
            for drive in slot_state.get(ROLE_EQUIPPED_DRIVES, ()) or ()
            if isinstance(drive, dict) and str(drive.get(EQUIP_SET_NAME) or "").strip()
        }
        return f"세트: {' / '.join(sorted(suit_names))}" if suit_names else "세트: 미장착"

    def selected_slot() -> dict[str, Any] | None:
        selected_id = selector.currentData()
        return next(
            (candidate for candidate in slots if candidate["slot_id"] == selected_id),
            None,
        )

    def update_selected_slot() -> None:
        slot = selected_slot()
        if slot is None:
            plan_status.setText("관리할 장비 세팅 슬롯이 없습니다.")
            rename_button.setEnabled(False)
            archive_button.setEnabled(False)
            return
        plan = slot.get("current_plan") or {}
        if plan:
            if plan.get("allocation_locked"):
                status = "상태: 잠금"
            elif bool(((plan.get("payload") or {}).get("last_diff") or {}).get(DIFF_CHANGED)):
                status = "상태: 변동"
            else:
                status = "상태: 세팅됨"
        else:
            status = "상태: 미세팅"
        plan_status.setText(f"{status}\n{slot_suit_text(slot)}")
        rename_button.setEnabled(True)
        archive_button.setEnabled(len(slots) > 1)
        archive_button.setToolTip("장비 세팅 슬롯은 최소 하나 남겨야 합니다" if len(slots) <= 1 else "")

    def reload_slots(selected_id: int | None = None) -> None:
        nonlocal slots
        with UserDataDao(database_path) as dao:
            slots = dao.list_loadout_slots(character_id)
        selector.blockSignals(True)
        selector.clear()
        for slot in slots:
            suffix = "(잠김)" if (slot.get("current_plan") or {}).get("allocation_locked") else ""
            selector.addItem(f"{slot_label(slot)}{suffix}", int(slot["slot_id"]))
        target_id = selected_id if selected_id is not None else int(slot_id)
        target_index = selector.findData(target_id)
        selector.setCurrentIndex(target_index if target_index >= 0 else 0)
        selector.blockSignals(False)
        update_selected_slot()

    def create_slot() -> None:
        nonlocal changed
        name, accepted = QInputDialog.getText(dialog, "장비 세팅 슬롯 추가", "슬롯 이름:")
        if not accepted:
            return
        try:
            with UserDataDao(database_path) as dao:
                new_slot_id = dao.create_loadout_slot(character_id, name)
        except Exception as exc:
            QMessageBox.warning(dialog, "장비 세팅 슬롯 추가", str(exc))
            return
        changed = True
        reload_slots(new_slot_id)

    def rename_slot() -> None:
        nonlocal changed
        slot = selected_slot()
        if slot is None:
            return
        name, accepted = QInputDialog.getText(
            dialog,
            "장비 세팅 슬롯 이름 바꾸기",
            "슬롯 이름:",
            text=slot_label(slot),
        )
        if not accepted:
            return
        try:
            with UserDataDao(database_path) as dao:
                dao.rename_loadout_slot(int(slot["slot_id"]), name)
        except Exception as exc:
            QMessageBox.warning(dialog, "장비 세팅 슬롯 이름 바꾸기", str(exc))
            return
        changed = True
        reload_slots(int(slot["slot_id"]))

    def archive_slot() -> None:
        nonlocal changed
        slot = selected_slot()
        if slot is None or len(slots) <= 1:
            return
        answer = QMessageBox.question(
            dialog,
            "장비 세팅 슬롯 삭제",
            f"[{slot_label(slot)}]을(를) 삭제하면 이 슬롯의 현재 방안은 더 이상 표시·장착에 사용되지 않습니다. 기록은 유지됩니다. 계속할까요?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            with UserDataDao(database_path) as dao:
                dao.archive_loadout_slot(int(slot["slot_id"]))
        except Exception as exc:
            QMessageBox.warning(dialog, "장비 세팅 슬롯 삭제", str(exc))
            return
        changed = True
        reload_slots()

    selector.currentIndexChanged.connect(lambda _index: update_selected_slot())
    create_button.clicked.connect(create_slot)
    rename_button.clicked.connect(rename_slot)
    archive_button.clicked.connect(archive_slot)
    reload_slots(int(slot_id))
    dialog.exec()
    if changed:
        invalidate_saved_equipment_cache(self)
        self._refresh_equip(restore_role_name=character_label)


def _toggle_role_allocation_lock(
    self: Any,
    role_name: str,
    *,
    plan_id: int | None = None,
    state_key: str | None = None,
) -> bool | None:
    """Persist one current-slot lock change and refresh its navigator badge."""

    database_path = _equipment_paths(self)[0]
    try:
        with UserDataDao(database_path) as dao:
            plan = dao.get_loadout_plan(int(plan_id)) if plan_id is not None else dao.get_active_loadout_plan_for_role(role_name)
            if plan is None:
                raise RuntimeError("해당 캐릭터의 활성 장비 세팅 방안을 찾지 못했습니다")
            locked = not bool(plan.get("allocation_locked"))
            dao.set_allocation_lock(int(plan["plan_id"]), locked)
    except Exception as exc:
        logger.warning(f"세팅 잠금 전환 실패 role={role_name}: {exc}")
        QMessageBox.warning(self, "세팅 잠금", str(exc))
        return None
    logger.info(
        f"세팅 잠금 {'开启' if locked else '解除'}: role={role_name}, plan_id={plan['plan_id']}"
    )
    invalidate_saved_equipment_cache(self)
    update_equipment_role_status(
        self,
        state_key or role_name,
        _allocation_locked=locked,
    )
    game_state = (getattr(self, "_game_loadout_states", {}) or {}).get(role_name)
    if isinstance(game_state, dict):
        game_state["_game_existing_plan_locked"] = locked
    return locked


def _game_loadout_scores(
    self: Any,
    role_name: str,
    state: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    presentation = equipment_presentation(self)
    return score_equipment_display_state(
        presentation,
        role_name,
        state,
        getattr(self, "roles_db", {}) or {},
    )


def _import_game_loadout(self: Any, role_name: str) -> None:
    state = (getattr(self, "_game_loadout_states", {}) or {}).get(role_name)
    if not isinstance(state, dict):
        QMessageBox.warning(self, "게임 내 방안 가져오기", "현재 표시가 만료되었습니다. 새로 고친 뒤 다시 시도하세요.")
        return
    projection = state.get("_game_projection")
    if projection is None or not bool(state.get("_game_importable")):
        QMessageBox.warning(
            self,
            "게임 내 방안 가져오기",
            str(state.get("_game_reason") or "현재 게임 내 장비로는 완전한 방안을 구성할 수 없습니다."),
        )
        return
    database_path, static_database_path, _ = _equipment_paths(self)
    try:
        with UserDataDao(database_path) as user_dao:
            slots = user_dao.list_loadout_slots(int(projection.character_id))
            if not slots:
                user_dao.create_loadout_slot(
                    int(projection.character_id),
                    "主力",
                    slot_key="primary",
                )
                slots = user_dao.list_loadout_slots(int(projection.character_id))
            labels = [
                f"{role_name if slot['slot_name'] == '主力' else slot['slot_name']} (슬롯 #{slot['slot_id']})" + ("(잠김)" if (slot.get("current_plan") or {}).get("allocation_locked") else "")
                for slot in slots
            ]
        if len(slots) == 1:
            target_slot = slots[0]
        else:
            label, accepted = QInputDialog.getItem(
                self,
                "가져오기 대상 슬롯",
                f"[{role_name}]의 게임 내 장비 세팅을 가져올 위치:",
                labels,
                0,
                False,
            )
            if not accepted:
                return
            target_slot = slots[labels.index(label)]
        if (target_slot.get("current_plan") or {}).get("allocation_locked"):
            QMessageBox.warning(self, "게임 내 방안 가져오기", "대상 장비 세팅 슬롯이 잠겨 있습니다. 먼저 잠금을 해제하세요.")
            return
    except Exception as exc:
        QMessageBox.warning(self, "게임 내 방안 가져오기", str(exc))
        return
    if state.get("_game_existing_plan_id") is not None and not bool(state.get("_game_imported")):
        answer = QMessageBox.question(
            self,
            "게임 내 방안 가져오기",
            f"가져오면 [{role_name}]의 현재 계산기 장비 세팅 방안을 대체합니다. 계속할까요?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

    try:
        total_score, assignment_scores = _game_loadout_scores(self, role_name, state)
        with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
            plan_id = GameLoadoutProjectionService(user_dao, static_dao).import_role(
                projection,
                score=total_score,
                assignment_scores=assignment_scores,
                slot_id=int(target_slot["slot_id"]),
            )
    except Exception as exc:
        logger.warning(f"게임 내 장비 세팅 가져오기 실패 role={role_name}: {exc}")
        QMessageBox.warning(self, "게임 내 방안 가져오기", str(exc))
        return
    logger.info(f"게임 내 장비 세팅 가져옴 role={role_name}, plan_id={plan_id}")
    QMessageBox.information(self, "게임 내 방안 가져오기", f"[{role_name}]을(를) 계산기 장비 세팅 방안으로 가져왔습니다.")
    self._saved_equipment_cache_valid = False
    self._refresh_equip(restore_role_name=role_name)


def _import_all_game_loadouts(self: Any) -> None:
    states = getattr(self, "_game_loadout_states", {}) or {}
    eligible = [
        (role_name, state)
        for role_name, state in states.items()
        if isinstance(state, dict)
        and bool(state.get("_game_importable"))
        and not bool(state.get("_game_imported"))
        and not bool(state.get("_game_existing_plan_locked"))
    ]
    locked_count = sum(
        isinstance(state, dict)
        and bool(state.get("_game_importable"))
        and bool(state.get("_game_existing_plan_locked"))
        for state in states.values()
    )
    if not eligible:
        QMessageBox.information(self, "원클릭 가져오기", "현재 가져올 완전한 게임 내 방안이 없습니다.")
        return
    prompt = (
        f"캐릭터 {len(eligible)}명의 게임 내 방안을 가져옵니다."
        "해당 캐릭터의 기존 잠기지 않은 방안은 대체됩니다. 계속할까요?"
    )
    if locked_count:
        prompt += f"\n\n그 외 캐릭터 {locked_count}명은 기존 방안이 잠겨 있어 건너뜁니다."
    answer = QMessageBox.question(
        self,
        "원클릭 가져오기",
        prompt,
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if answer != QMessageBox.Yes:
        return

    requests = []
    try:
        for role_name, state in eligible:
            total_score, assignment_scores = _game_loadout_scores(
                self,
                role_name,
                state,
            )
            requests.append(GameLoadoutImportRequest(
                projection=state["_game_projection"],
                score=total_score,
                assignment_scores=assignment_scores,
            ))
        database_path, static_database_path, _ = _equipment_paths(self)
        with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
            plan_ids = GameLoadoutProjectionService(user_dao, static_dao).import_roles(
                requests,
            )
    except Exception as exc:
        logger.warning(f"게임 내 장비 세팅 원클릭 가져오기 실패: {exc}")
        QMessageBox.warning(self, "원클릭 가져오기", str(exc))
        return
    logger.info(f"게임 내 장비 세팅을 원클릭으로 가져옴 count={len(plan_ids)}")
    message = f"캐릭터 {len(plan_ids)}명의 게임 내 방안을 가져왔습니다."
    if locked_count:
        message += f"\n캐릭터 {locked_count}명은 방안이 잠겨 있어 건너뛰었습니다."
    QMessageBox.information(self, "원클릭 가져오기", message)
    self._saved_equipment_cache_valid = False
    self._refresh_equip()


class EquipmentDisplayControllerMixin:
    """Explicit MainWindow surface for saved equipment-plan display."""

    _equipment_compare_signature = _equipment_compare_signature
    _same_equipment_by_ocr = _same_equipment_by_ocr
    _page_equipment = _page_equipment
    _set_equipment_mode = _set_equipment_mode
    _refresh_equip = _refresh_equip
    _request_equipment_graduation_rate = _request_equipment_graduation_rate
    invalidate_saved_equipment_cache = invalidate_saved_equipment_cache
    reset_equipment_account_state = reset_equipment_account_state
    refresh_saved_equipment_after_mutation = (
        refresh_saved_equipment_after_mutation
    )
    _saved_plan_diff_text = _saved_plan_diff_text
    _show_saved_plan_diff_dialog = _show_saved_plan_diff_dialog
    _clear_all_equipment = _clear_all_equipment
    _delete_role_equipment = _delete_role_equipment
    _toggle_role_allocation_lock = _toggle_role_allocation_lock
    _manage_loadout_slot = _manage_loadout_slot
    _import_game_loadout = _import_game_loadout
    _import_all_game_loadouts = _import_all_game_loadouts
    _optimize_saved_equipment = _optimize_saved_equipment
