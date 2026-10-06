# 格式化倒带角色卡的评分、槽位名称与完整提示。
from __future__ import annotations

from dataclasses import dataclass

from src.domain.allocation_rating import loadout_total_grade
from src.domain.rewind_loadout import RewindSlotReference, finite_score
from src.domain.role_name_order import role_name_sort_key
from src.services.rewind_shape_recommendation_service import RewindTargetRole


@dataclass(frozen=True, slots=True)
class RewindRoleCardPresentation:
    score: float | None
    score_text: str
    grade: str
    slot_text: str
    detail: str
    slot_detail: str
    can_switch: bool


def rewind_role_sort_key(role: RewindTargetRole):
    scores = [score for slot in role.slots if (score := finite_score(slot.score)) is not None]
    highest = max(scores) if scores else None
    return (highest is None, -highest if highest is not None else 0,
            role_name_sort_key(role.name), role.character_id)


def rewind_role_card_presentation(
    role: RewindTargetRole, selected_reference: RewindSlotReference | None,
) -> RewindRoleCardPresentation:
    slot = next((row for row in role.slots if row.reference == selected_reference), None)
    score = slot.score if slot else None
    grade = loadout_total_grade(score) if score is not None else ""
    if score is not None:
        score_text = f"{score:.2f}".rstrip("0").rstrip(".")
        if len(score_text) > 10:
            score_text = f"{score:.3g}"
    elif not role.slots or (slot and slot.state == "empty"):
        score_text = "방안 없음"
    else:
        score_text = "점수 데이터 부족" if slot else "슬롯 선택 대기"
    if not role.slots:
        caption = "장비 세팅 없음"
    elif slot is None:
        caption = "슬롯 선택"
    else:
        caption = slot.slot_name
    if slot is not None:
        exact_score = f"{score:g}점 ({grade})" if score is not None else score_text
        detail = f"{role.name}\n{slot.slot_name}：{exact_score}\n{slot.reason}"
    else:
        detail = f"{role.name}\n{score_text}\n{caption}"
    slot_detail = f"{role.name}의 장비 세팅 슬롯 전환\n{detail}" if role.slots else detail
    return RewindRoleCardPresentation(score, score_text, grade, caption, detail, slot_detail, bool(role.slots))
