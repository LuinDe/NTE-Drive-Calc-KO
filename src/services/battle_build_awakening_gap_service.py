# 将会改变固定轴事件或不可恢复状态的觉醒差异标记为未量化缺口。
"""Awakening boundaries that fixed-axis build replay cannot synthesize."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

from src.domain.battle_counterfactual import BattleBuildRoleCounterfactual
from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
    BattleQuantificationGap,
)
from src.domain.battle_report import BattleCharacterBaseline


_LINKO_UNGENERATED_BOUNDARIES = {
    "Effect1": (
        "linko_effect1_attack_interval_unquantified",
        "linko_awaken_effect1_attack_interval",
        "링코 1각성은 공격 간격을 바꾸지만, 고정 축은 그로 인해 늘거나 줄어드는 동작과 히트를 생성하지 않았습니다.",
    ),
    "Effect3": (
        "linko_effect3_e_cooldown_reset_unquantified",
        "linko_awaken_effect3_e_cooldown_reset",
        "링코 3각성의 공격력과 스킬 레벨은 기존 공식으로 정량화되지만, E 재사용 대기시간 초기화가 동작과 히트 집합을 바꿀 수 있습니다.",
    ),
    "Effect4": (
        "linko_effect4_added_hit_unquantified",
        "linko_awaken_effect4_added_hit",
        "링코 4각성은 히트를 추가하지만, 고정 축에 신뢰할 수 있는 런타임 이벤트 근거가 없어 변화를 0 이득으로 간주할 수 없습니다.",
    ),
    "Effect5": (
        "linko_effect5_added_reaction_unquantified",
        "linko_awaken_effect5_added_reaction",
        "링코 5각성은 반응 정산을 추가하지만, 고정 축에 신뢰할 수 있는 런타임 이벤트 근거가 없어 변화를 0 이득으로 간주할 수 없습니다.",
    ),
    "Effect6": (
        "linko_effect6_resource_restore_unquantified",
        "linko_awaken_effect6_resource_restore",
        "링코 6각성의 치명타와 공명 피해 증가는 기존 공식으로 정량화되지만, 자원 회복이 이후 동작과 히트 집합을 바꿀 수 있습니다.",
    ),
}
_SHINKU_UNRESOLVED_BOUNDARIES = {
    "Effect3": (
        "shinku_effect3_watch_growth_unquantified",
        "shinku_awaken_effect3_watch_growth",
        "신쿠 3번째 각성은 퇴장 시 응시 유지와 백그라운드 증가를 바꿉니다; 고정 축에는 히트별 응시 중첩 수가 없어 그로 인해 바뀌는 발동 시점과 피해를 정량화할 수 없습니다.",
    ),
    "Effect4": (
        "shinku_effect4_watch_cap_unquantified",
        "shinku_awaken_effect4_watch_cap",
        "신쿠 4번째 각성은 응시 중첩 상한과 추가 피해 발동을 바꿉니다; 고정 축은 해당 히트를 생성하거나 삭제할 수 없고, 원본 히트의 중첩 수가 그대로라고 가정할 수도 없습니다.",
    ),
}
_CHARACTER_BOUNDARIES = {
    1072: _LINKO_UNGENERATED_BOUNDARIES,
    1076: _SHINKU_UNRESOLVED_BOUNDARIES,
}


def awakening_change_gaps(
    original: Mapping[int, BattleCharacterBaseline],
    candidate: Mapping[int, BattleCharacterBaseline],
) -> tuple[BattleQuantificationGap, ...]:
    """Retain changed mechanics whose event or state axis cannot be rebuilt."""

    return tuple(
        BattleQuantificationGap(
            code=code,
            dimension_id=dimension_id,
            dependency_scope="mechanic_specific",
            property_ids=(),
            explanation=explanation,
        )
        for character_id, boundaries in _CHARACTER_BOUNDARIES.items()
        if character_id in original and character_id in candidate
        for effect_id in sorted(
            set(original[character_id].selected_awaken_effect_ids)
            ^ set(candidate[character_id].selected_awaken_effect_ids)
        )
        if effect_id in boundaries
        for code, dimension_id, explanation in (
            boundaries[effect_id],
        )
    )


def awakening_gaps_for_character(
    gaps: Sequence[BattleQuantificationGap],
    character_id: int,
) -> tuple[BattleQuantificationGap, ...]:
    """Scope team-level awakening gaps to their actual character owner."""

    dimensions = {
        boundary[1]
        for boundary in _CHARACTER_BOUNDARIES.get(character_id, {}).values()
    }
    return tuple(gap for gap in gaps if gap.dimension_id in dimensions)


def with_awakening_gaps(
    quantification: BattleDamageQuantification,
    gaps: Sequence[BattleQuantificationGap],
) -> BattleDamageQuantification:
    """Downgrade a comparison while retaining every already quantified delta."""

    unique_gaps = tuple(dict.fromkeys((*quantification.gaps, *gaps)))
    if not gaps:
        return quantification
    if quantification.status == "unavailable":
        return replace(quantification, gaps=unique_gaps)
    if quantification.status == "not_applicable":
        if quantification.basis_damage <= 0.0:
            return replace(
                quantification,
                status="unavailable",
                quantified_increment=None,
                gaps=unique_gaps,
            )
        return replace(
            quantification,
            status="partial",
            fully_quantified_damage=quantification.basis_damage,
            proven_unchanged_damage=0.0,
            gaps=unique_gaps,
        )
    return replace(quantification, status="partial", gaps=unique_gaps)


def mark_awakening_roles_partial(
    rows: Sequence[BattleBuildRoleCounterfactual],
    gaps: Sequence[BattleQuantificationGap],
) -> tuple[BattleBuildRoleCounterfactual, ...]:
    """Keep unrelated roles outside each missing-mechanic boundary."""

    if not gaps:
        return tuple(rows)
    return tuple(
        replace(
            row,
            quantification=with_awakening_gaps(row.quantification, role_gaps),
            candidate_damage=None,
            gain_percent=None,
            team_gain_percent=None,
        )
        if role_gaps else row
        for row in rows
        for role_gaps in (awakening_gaps_for_character(gaps, row.character_id),)
    )


__all__ = [
    "awakening_change_gaps",
    "awakening_gaps_for_character",
    "mark_awakening_roles_partial",
    "with_awakening_gaps",
]
