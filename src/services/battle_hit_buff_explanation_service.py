# 把单次逐击的推算 Buff 投影整理为可审计的中文详情。
"""Qt-free explanation for inferred Buffs active on one battle hit."""

from __future__ import annotations

from collections.abc import Sequence

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffProjectionDecision,
    BattleInferredBuffInterval,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
    normalize_battle_buff_property_id,
)
from src.services.skill_name_rendering_service import preferred_battle_damage_name


_SCOPE_LABELS = {
    "self": "자신",
    "team": "팀 전체",
    "target": "적 대상",
    "unknown": "적용 대상 확인 필요",
}
_STATUS_LABELS = {
    "applied": "투영됨",
    "not_applied": "미채택",
    "unresolved": "확인 대기/구조화",
}
_PROPERTY_LABELS = {
    "AtkUp": "공격력 증가",
    "AtkAdd": "추가 공격력",
    "HPMaxUp": "HP 상한 증가",
    "HPMaxAdd": "추가 HP 상한",
    "DefUp": "방어력 증가",
    "DefAdd": "추가 방어력",
    "CritBase": "暴击率",
    "CritDamageBase": "暴击伤害",
    "DamageUpGeneralBase": "통용 피해 증가",
    "DamageUpChaosBase": "암속성 이능력 피해 증가",
    "DamageUpCosmosBase": "빛속성 이능력 피해 증가",
    "DamageUpIncantationBase": "주속성 이능력 피해 증가",
    "DamageUpLakshanaBase": "상속성 이능력 피해 증가",
    "DamageUpNatureBase": "령속성 이능력 피해 증가",
    "DamageUpPsycheBase": "혼속성 이능력 피해 증가",
    "DamageUpPsychicallyBase": "정신 피해 증가",
    "DefIgnore": "방어 관통",
    "DamagePenetrateChaos": "암속성 관통",
    "DamagePenetrateCosmos": "빛속성 관통",
    "DamagePenetrateIncantation": "주속성 관통",
    "DamagePenetrateLakshana": "상속성 관통",
    "DamagePenetrateNature": "령속성 관통",
    "DamagePenetratePsyche": "혼속성 관통",
    "DamagePenetratePsychically": "정신속성 관통",
    "DamageResistChaosBase": "적 암속성 저항 변화",
    "DamageResistChaosAdd": "적 추가 암속성 저항 변화",
    "DamageResistCosmosBase": "적 빛속성 저항 변화",
    "DamageResistCosmosAdd": "적 추가 빛속성 저항 변화",
    "DamageResistIncantationBase": "적 주속성 저항 변화",
    "DamageResistIncantationAdd": "적 추가 주속성 저항 변화",
    "DamageResistLakshanaBase": "적 상속성 저항 변화",
    "DamageResistLakshanaAdd": "적 추가 상속성 저항 변화",
    "DamageResistNatureBase": "적 령속성 저항 변화",
    "DamageResistNatureAdd": "적 추가 령속성 저항 변화",
    "DamageResistPsycheBase": "적 혼속성 저항 변화",
    "DamageResistPsycheAdd": "적 추가 혼속성 저항 변화",
    "DamageResistPsychicallyBase": "적 정신 저항 변화",
    "DamageResistPsychicallyAdd": "적 추가 정신 저항 변화",
    "MagBase": "环合强度",
    "UnbalIntensityBase": "倾陷强度",
    "UnbalIntensityUp": "브레이크 강도 증가",
    "UnbalIntensityAdd": "추가 브레이크 강도",
    "UnbalDamageUp": "브레이크 피해 증가",
    "ChargeGetEfficiencyBase": "充能效率",
    "ImmuneDeadByTeammates": "팀원 사망 방지",
    "ShareOutTeammatesDamageMul": "팀원 피해 분담",
    "MoveSpeedMaxMult": "이동 속도 상한",
}
_PERCENT_PROPERTIES = frozenset({
    "AtkUp",
    "HPMaxUp",
    "DefUp",
    "CritBase",
    "CritDamageBase",
    "DamageUpGeneralBase",
    "DefIgnore",
    "UnbalIntensityUp",
    "UnbalDamageUp",
    "ChargeGetEfficiencyBase",
    *(
        property_id
        for property_id in _PROPERTY_LABELS
        if property_id.startswith("DamageUp")
        or property_id.startswith("DamagePenetrate")
        or property_id.startswith("DamageResist")
    ),
})


def _time(value_us: int) -> str:
    seconds = max(0, value_us) / 1_000_000.0
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:06.3f}"


def battle_buff_property_label(property_id: str) -> str:
    return _PROPERTY_LABELS.get(property_id, property_id)


def format_battle_buff_value(property_id: str, value: float) -> str:
    if property_id in _PERCENT_PROPERTIES:
        return f"{value * 100:+g}%"
    return f"{value:+,.3f}".rstrip("0").rstrip(".")


def _raw_modifier_lines(
    interval: BattleInferredBuffInterval,
    decision: BattleBuffProjectionDecision,
) -> tuple[str, ...]:
    lines = []
    applied_ids = set(decision.applied_property_ids)
    for modifier in interval.modifiers:
        property_id = normalize_battle_buff_property_id(modifier.property_id)
        label = battle_buff_property_label(property_id)
        if modifier.magnitude_value is not None:
            total = float(modifier.magnitude_value) * max(1, interval.stacks)
            value = format_battle_buff_value(property_id, total)
            if interval.stacks > 1:
                value += (
                    f"({format_battle_buff_value(property_id, float(modifier.magnitude_value))}"
                    f" × {interval.stacks}중첩)"
                )
        elif modifier.calculation_asset_path:
            value = (
                "Calculation:"
                + modifier.calculation_asset_path.rsplit("/", 1)[-1]
            )
        else:
            value = "수치 미분석"
        usage = "속성값에 투영됨" if property_id in applied_ids else "속성값에 투영되지 않음"
        lines.append(
            f"  - {label} {value} ({property_id}, {usage}, 수치 신뢰도"
            f" {modifier.value_confidence})"
        )
    return tuple(lines) or ("  - 추출된 속성 보정이 없습니다.",)


class BattleHitBuffExplanationService:
    """Describe active intervals and their exact per-hit projection decision."""

    @classmethod
    def build(
        cls,
        hit: BattleAnalysisHit,
        intervals: Sequence[BattleInferredBuffInterval],
        *, projection=None, allow_projection_fallback: bool = True,
    ) -> str:
        if projection is None and allow_projection_fallback:
            projection = BattleBuffAttributeProjectionService.project_hit(hit, intervals)
        if projection is None:
            return "이 히트의 원본 Buff 상세가 생성되지 않았습니다; 해당 구간의 히트별 분석을 먼저 불러오세요."
        active_by_id = {row.interval_id: row for row in intervals}
        decisions = tuple(
            decision
            for decision in projection.decisions
            if decision.interval_id in active_by_id
        )
        counts = {
            status: sum(row.status == status for row in decisions)
            for status in _STATUS_LABELS
        }
        damage_name = preferred_battle_damage_name(
            hit.damage_name,
            hit.skill_name,
            hit.ability_id,
        )
        lines = [
            f"{hit.character_name} · {damage_name}",
            f"히트 시각: {_time(hit.relative_time_us)}    대상: {hit.target_name}",
            (
                f"히트 시점 추정 Buff: {len(decisions)}개    "
                f"투영됨 {counts['applied']} / 미채택 {counts['not_applied']} / "
                f"확인 대기 {counts['unresolved']}"
            ),
            "기준: 이는 고정된 장비 세팅·동작·히트별 추정이며, nte-core 런타임 실측 Buff가 아닙니다.",
            "공식 소비 기준: 투영됨은 히트별 속성값에 들어갔다는 뜻일 뿐이며, 현재 피해 공식에 소비되는지 여부는"
            "피해 공식에 나열된 곱연산 구간과 출처 항을 기준으로 합니다.",
            "",
            "【히트별 속성값에 투영된 보너스 요약】",
        ]
        if projection.modifiers:
            for modifier in projection.modifiers:
                sources = "、".join(modifier.buff_names)
                lines.append(
                    f"- {battle_buff_property_label(modifier.property_id)} "
                    f"{format_battle_buff_value(modifier.property_id, modifier.additive_value)}"
                    f"({modifier.property_id}, {_SCOPE_LABELS.get(modifier.target_scope, modifier.target_scope)},"
                    f"신뢰도 {modifier.confidence})\n"
                    f"  출처: {sources}"
                )
        else:
            lines.append("- 이 히트의 속성값에 투영된 Buff 수치가 없습니다.")

        decision_by_status = {
            status: tuple(row for row in decisions if row.status == status)
            for status in _STATUS_LABELS
        }
        for status, title in _STATUS_LABELS.items():
            matching = decision_by_status[status]
            if not matching:
                continue
            lines.extend(("", f"【{title} Buff】"))
            for decision in matching:
                interval = active_by_id[decision.interval_id]
                lines.append(
                    f"- {interval.buff_name} ×{interval.stacks}"
                    f"(출처 캐릭터: {interval.source_character_name};"
                    f"적용 대상: {_SCOPE_LABELS.get(interval.target_scope, interval.target_scope)};"
                    f"구간: {_time(interval.start_us)}—{_time(interval.end_us)};"
                    f"상태 {interval.state_confidence} / 수치 {interval.value_confidence})"
                )
                lines.extend(_raw_modifier_lines(interval, decision))
                if decision.reasons:
                    lines.append(f"  판정: {'；'.join(decision.reasons)}")
                lines.extend((
                    f"  ID: {interval.source_effect_definition_id}",
                    f"  에셋: {interval.buff_asset_path}",
                    f"  추정 근거: {interval.inference_basis}",
                ))
        if not decisions:
            lines.extend((
                "",
                "【근거 범위】",
                "- 이 히트에는 히트 시점에 유효한 추정 Buff 구간이 매칭되지 않았습니다.",
            ))
        return "\n".join(lines)
