# 编排角色页与配装页共享的官方方案单件替换弹窗。
"""Shared Qt controller for official-role replacement previews."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtWidgets import QMessageBox, QWidget

from src.domain.allocation_rating import allocation_grade
from src.domain.loadout_plan_scores import assignment_score_key
from src.features.inventory.warehouse import warehouse_item_view
from src.features.official_role.controller import OfficialRoleController
from src.features.official_role.dependencies import OfficialRoleDependencies
from src.services.official_role_equipment_scoring_service import (
    score_official_role_equipment,
)
from src.services.official_role_page_service import (
    replacement_candidates_for_official_role,
)
from src.ui.equipment_replacement_dialog import (
    EquipmentReplacementCard,
    show_equipment_replacement_dialog,
)


OFFICIAL_ROLE_REPLACEMENT_SUMMARY = (
    "점수는 이 캐릭터의 직접 피해 가중치로 계산하며, 후보는 높은 순으로 정렬됩니다."
    "카드의 세 번째 항목 백분율은 이 장비가 가져오는 직접 피해 이득을 나타냅니다."
)


def show_official_role_replacement(
    window: QWidget,
    detail: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    context_key: str = "saved",
    on_saved: Callable[[], None] | None = None,
) -> bool:
    """Show, persist and report one official-role replacement operation."""

    candidates = replacement_candidates_for_official_role(detail, context_key, target)
    if not candidates:
        QMessageBox.information(
            window,
            "교체 최적화",
            "같은 세트·같은 형태이면서 현재 방안에서 사용하지 않는 교체 가능 장비가 없습니다.",
        )
        return False

    scoring_engine = getattr(window, "scoring_engine", None)
    shape_areas = getattr(window, "_shape_areas", {})

    current_item = dict(candidates[0]["current_item"])
    current_base_score = score_official_role_equipment(
        scoring_engine,
        detail=detail,
        item=current_item,
        shape_areas=shape_areas,
    )
    current_assignment_scores = {
        assignment_score_key(item): score_official_role_equipment(
            scoring_engine,
            detail=detail,
            item=item,
            shape_areas=shape_areas,
        )
        for item in candidates[0].get("current_items") or ()
    }

    def card_data(
        item: dict[str, Any],
        *,
        direct_damage_score: float | None,
        hidden_score: float,
        payload: Mapping[str, Any] | None,
    ) -> EquipmentReplacementCard:
        view = warehouse_item_view(item)
        icon_path = (detail.get("item_icon_paths") or {}).get(
            str(item.get("item_id") or "")
        )
        if icon_path:
            view["item_icon_path"] = icon_path
        score = float(hidden_score)
        area = 15 if str(item.get("kind") or "") == "core" else int(
            item.get("grid_count") or 0
        )
        return EquipmentReplacementCard(
            key=f"{item.get('uid_slot')}:{item.get('uid_serial')}",
            item_view=view,
            score=score,
            grade=allocation_grade(score, area),
            direct_damage_score=direct_damage_score,
            payload=payload,
            note=(
                f"{view.get('equipped_character_name')}의 저장 방안에서 대여하고,"
                "같은 트랜잭션에서 원래 슬롯에 금색 자리 표시 장비를 채웁니다."
                if view.get("equipped_character_name")
                else ""
            ),
        )

    current = card_data(
        current_item,
        direct_damage_score=candidates[0].get("current_direct_damage_score"),
        hidden_score=float(candidates[0].get("current_score") or 0.0),
        payload=None,
    )
    choices = [
        card_data(
            dict(row["item"]),
            direct_damage_score=row.get("direct_damage_score"),
            hidden_score=float(row.get("score") or 0.0),
            payload={
                **row,
                "base_score": score_official_role_equipment(
                    scoring_engine,
                    detail=detail,
                    item=dict(row["item"]),
                    shape_areas=shape_areas,
                ),
            },
        )
        for row in candidates[:30]
    ]

    role_controller = OfficialRoleController(
        OfficialRoleDependencies.from_app_context(window.app_context)
    )

    def save_choice(choice: EquipmentReplacementCard) -> None:
        row = choice.payload
        role_controller.save_replacement(
            detail,
            target,
            row["item"],
            context_key=context_key,
            replacement_score=float(row["base_score"]),
            current_score=current_base_score,
            current_assignment_scores=current_assignment_scores,
        )

    accepted = show_equipment_replacement_dialog(
        window,
        title="교체 최적화",
        role_name=str((detail.get("character") or {}).get("name_zh") or ""),
        summary=OFFICIAL_ROLE_REPLACEMENT_SUMMARY,
        current=current,
        candidates=choices,
        on_confirm=save_choice,
    )
    if not accepted:
        return False
    if on_saved is not None:
        on_saved()
    QMessageBox.information(window, "교체 최적화", "새 장비 세팅 방안으로 저장했습니다.")
    return True
