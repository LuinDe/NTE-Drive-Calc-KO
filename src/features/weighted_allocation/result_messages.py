# 生成加权配装结果中未分配角色和缺失卡带的说明文本。
"""Human-readable result explanations independent from Qt layout code."""

from __future__ import annotations

from src.services.allocation_context import AllocationContext


def unassigned_reason(
    owner,
    context: AllocationContext | None,
    character_ids: tuple[int, ...],
) -> str:
    if context is None:
        return "일부 캐릭터에게 사용 가능한 완전한 방안이 없습니다."
    names = getattr(owner, "_weighted_role_names", {})
    suits = getattr(owner, "_weighted_suit_names", {})
    attributes = getattr(owner, "_weighted_property_names", {})
    reasons = []
    for role in context.roles:
        if role.character_id not in character_ids:
            continue
        cores = [item for item in context.candidates if item.kind == "core"]
        if role.target_suit_id:
            cores = [
                item for item in cores if item.suit_id == role.target_suit_id
            ]
        if role.core_main_property_id:
            cores = [
                item
                for item in cores
                if any(
                    stat.property_id == role.core_main_property_id
                    for stat in item.main_stats
                )
            ]
        if not cores:
            suit = suits.get(
                role.target_suit_id, role.target_suit_id or "모든 세트"
            )
            attribute = attributes.get(
                role.core_main_property_id,
                role.core_main_property_id or "모든 메인 스탯",
            )
            reasons.append(
                f"{names.get(role.character_id, role.character_id)}:"
                f"{suit}＋{attribute} 메인 스탯 카트리지 없음"
            )
        else:
            reasons.append(
                f"{names.get(role.character_id, role.character_id)}:"
                "완전한 청사진을 구성할 드라이브 없음"
            )
    return "；".join(reasons)


def missing_core_text(owner, role, reason: str | None = None) -> str:
    if reason:
        return f"카트리지 없음: {reason} (드라이브 청사진은 매칭됨, 방안은 불완전 상태로 저장)"
    if role is None:
        return "카트리지 미분배"
    suits = getattr(owner, "_weighted_suit_names", {})
    attributes = getattr(owner, "_weighted_property_names", {})
    suit = suits.get(role.target_suit_id, role.target_suit_id or "모든 세트")
    attribute = attributes.get(
        role.core_main_property_id,
        role.core_main_property_id or "모든 메인 스탯",
    )
    return (
        f"카트리지 없음: {suit}＋{attribute} 메인 스탯 카트리지 없음"
        "(드라이브 청사진 매칭됨)"
    )
