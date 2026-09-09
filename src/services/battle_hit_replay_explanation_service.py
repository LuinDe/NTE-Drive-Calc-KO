# 把结构化逐击重放结果投影为可复制的完整算式与证据解释。
"""Qt-free presentation of one source-addressable battle-hit replay."""

from __future__ import annotations

from collections.abc import Sequence
from functools import reduce
from operator import mul

from src.domain.battle_counterfactual import BattleBuildHitCounterfactual
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleHitReplayTerm,
    BattleInferredBuffInterval,
)
from src.services.battle_damage_composition_service import (
    classify_battle_hit_channel,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.skill_name_rendering_service import preferred_battle_damage_name


_FORMULA_FACTOR_IDS = (
    "skill",
    "state_coefficient",
    "scaling",
    "damage_up",
    "defense",
    "resistance",
    "vulnerability",
    "independent",
    "dot_final",
)
_REQUIRED_FORMULA_FACTOR_IDS = frozenset({
    "skill",
    "scaling",
    "damage_up",
    "defense",
    "resistance",
    "vulnerability",
    "independent",
})
_REACTION_FORMULA_FACTOR_IDS = (
    "skill",
    "state_coefficient",
    "scaling",
    "defense",
    "resistance",
    "vulnerability",
    "dot_final",
)
_REQUIRED_REACTION_FACTOR_IDS = frozenset({
    "skill",
    "scaling",
    "defense",
    "resistance",
    "vulnerability",
})
_CRIT_STATES = {
    "critical": "예",
    "non_critical": "아니요",
    "not_applicable": "해당 없음",
    "ambiguous": "확정 불가",
    "unreplayable": "추론 불가",
}

_COUNTERFACTUAL_METHOD_LABELS = {
    "structured_expected": "원본/후보 구조화 기대 공식 비율",
    "structured_selected": "원본/후보 구조화 동일 실측 분기 공식 비율",
    "skill_peer_estimate": "동일 스킬 리플레이 완료 히트 중앙값 비율",
    "type_peer_estimate": "동일 캐릭터·동일 피해 유형 중앙값 비율",
    "panel_formula_estimate": "캐릭터 패널·대상 곱연산 구간 비율",
    "role_peer_estimate": "해당 캐릭터 리플레이 가능 히트 중앙값 비율",
    "candidate_derived_daffodill_effect5": "후보 5각성의 통찰 중첩별 추가 결산",
    "component_ratio": "변화 곱연산 구간 완전 비율",
    "component_ratio_partial": "변화 곱연산 구간 정량화 성분 비율",
    "component_ratio_unavailable": "변화 곱연산 구간에 필요한 입력 없음",
    "component_ratio_not_applicable": "이 히트가 변화의 영향을 받지 않음이 증명됨",
}


def _damage(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _percent(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%" if signed else f"{value:.2f}%"


def _factor_value(factor: BattleHitReplayFactor) -> str:
    if factor.factor_id.startswith("topple_character:"):
        return f"{factor.value:,.2f}"
    if factor.factor_id == "skill" and "배율" in factor.label:
        return f"{factor.value * 100:.3f}%"
    if factor.factor_id == "scaling":
        return f"{factor.value:,.3f}"
    return f"{factor.value:.6f}"


def _term_value(term: BattleHitReplayTerm) -> str:
    if term.is_percent:
        return f"{term.value * 100:g}%"
    return f"{term.value:,.3f}"


def _sum_terms(terms: Sequence[BattleHitReplayTerm]) -> tuple[str, str]:
    if not terms:
        return "0", "0"
    return (
        " + ".join(f"{term.source_name}:{term.label}" for term in terms),
        " + ".join(_term_value(term) for term in terms),
    )


def _terms_for_property(
    factor: BattleHitReplayFactor, property_id: str,
) -> tuple[BattleHitReplayTerm, ...]:
    return tuple(
        term for term in factor.terms if term.property_id == property_id
    )


def _term_total(
    factor: BattleHitReplayFactor, property_id: str,
) -> float:
    return sum(term.value for term in _terms_for_property(factor, property_id))


def _source_names(terms: Sequence[BattleHitReplayTerm]) -> str:
    return "、".join(
        dict.fromkeys(f"{term.source_name}:{term.label}" for term in terms)
    ) or "없음"


def _signed_percent_expression(initial: float, changes: Sequence[float]) -> str:
    expression = f"{initial * 100:g}%"
    for value in changes:
        operator = "+" if value >= 0 else "-"
        expression += f" {operator} {abs(value) * 100:g}%"
    return expression


def _group_buff_intervals(
    intervals: Sequence[BattleInferredBuffInterval],
    decision_by_id: dict,
) -> tuple[
    tuple[tuple[BattleInferredBuffInterval, ...], object, int, int], ...
]:
    """Collapse semantically identical interval evidence without changing projection."""

    grouped: dict[tuple, list[BattleInferredBuffInterval]] = {}
    for interval in intervals:
        decision = decision_by_id[interval.interval_id]
        modifier_key = tuple(
            (
                row.property_id,
                row.modifier_operation,
                row.magnitude_kind,
                row.calculation_asset_path,
                row.modifier_group_ordinal,
                row.application_requirement_asset_path,
                row.source_require_tags,
                row.source_ignore_tags,
                row.target_require_tags,
                row.target_ignore_tags,
            )
            for row in interval.modifiers
        )
        key = (
            interval.buff_name,
            interval.source_effect_definition_id,
            interval.buff_asset_path,
            interval.target_scope,
            modifier_key,
            tuple(decision.applied_property_ids),
            tuple(decision.reasons),
        )
        grouped.setdefault(key, []).append(interval)
    return tuple(
        (
            tuple(rows),
            decision_by_id[rows[0].interval_id],
            len(rows),
            sum(max(1, row.stacks) for row in rows),
        )
        for rows in grouped.values()
    )


def _confidence_summary(values: Sequence[str]) -> str:
    return "/".join(dict.fromkeys(value for value in values if value)) or "未知"


def _buff_modifier_text(
    intervals: Sequence[BattleInferredBuffInterval],
    decision: object,
    total_stacks: int,
) -> str:
    applied_property_ids = tuple(getattr(decision, "applied_property_ids", ()))
    property_ids = tuple(dict.fromkeys(
        row.property_id
        for interval in intervals
        for row in interval.modifiers
        if row.magnitude_value is not None
        and (
            not applied_property_ids
            or row.property_id in applied_property_ids
        )
    ))
    rendered: list[str] = []
    for property_id in property_ids:
        contributions: list[tuple[float, str, tuple[str, ...]]] = []
        for interval in intervals:
            for modifier in interval.modifiers:
                if (
                    modifier.property_id != property_id
                    or modifier.magnitude_value is None
                ):
                    continue
                for _ in range(max(1, interval.stacks)):
                    contributions.append((
                        modifier.magnitude_value,
                        modifier.value_confidence or interval.value_confidence,
                        interval.evidence_event_ids,
                    ))
        if not contributions:
            continue
        values = tuple(row[0] for row in contributions)
        if len(values) == 1:
            rendered.append(f"{property_id}={values[0]:g}")
            continue
        if all(value == values[0] for value in values[1:]):
            rendered.append(
                f"{property_id}={values[0]:g}×{total_stacks}="
                f"{values[0] * total_stacks:g}"
            )
            continue
        total = sum(values)
        layer_text = "；".join(
            f"{value:,.6f}".rstrip("0").rstrip(".")
            + f"[{confidence}"
            + (f", {','.join(event_ids)}]" if event_ids else "]")
            for value, confidence, event_ids in contributions
        )
        rendered.append(
            f"{property_id} 합계={total:,.2f} (중첩별: {layer_text})"
        )
    return "、".join(rendered) or "구조화 규칙 적용됨"


def _scaling_formula_lines(factor: BattleHitReplayFactor) -> list[str]:
    prefix = factor.label.split(" ", 1)[0]
    component_ids = {
        "Atk": ("AtkBase", "AtkUp", "AtkAdd"),
        "HPMax": ("HPMaxBase", "HPMaxUp", "HPMaxAdd"),
        "Def": ("DefBase", "DefUp", "DefAdd"),
    }.get(prefix)
    if component_ids is None:
        return [
            f"{factor.label} = {factor.formula or '冻结面板值'}",
            f"  = {_factor_value(factor)}",
        ]
    base = [term for term in factor.terms if term.property_id == component_ids[0]]
    percent = [term for term in factor.terms if term.property_id == component_ids[1]]
    flat = [term for term in factor.terms if term.property_id == component_ids[2]]
    base_names, base_values = _sum_terms(base)
    percent_names, percent_values = _sum_terms(percent)
    flat_names, flat_values = _sum_terms(flat)
    return [
        f"{factor.label} = 기본값 × (1 + 백분율 상승) + 추가 고정값",
        f"  = ({base_names}) × (1 + {percent_names}) + {flat_names}",
        f"  = ({base_values}) × (1 + {percent_values}) + {flat_values}",
        f"  = {_factor_value(factor)}",
    ]


def _defense_formula_lines(factor: BattleHitReplayFactor) -> list[str]:
    if "DefBase/6" not in factor.evidence_basis:
        return []
    level = _term_total(factor, "CharacterLevel")
    defense_base = _term_total(factor, "DefBase")
    defense_up = _term_total(factor, "DefUp")
    defense_add = _term_total(factor, "DefAdd")
    penetration = _term_total(factor, "DefIgnore")
    reduction = _term_total(factor, "DefReduction")
    level_factor = level + 100.0
    panel_defense = defense_base * (1.0 + defense_up) + defense_add
    effective_defense = (
        panel_defense / 6.0 * (1.0 - penetration) * (1.0 - reduction)
    )
    return [
        "방어 구간 = L / (적 유효 방어 + L), L = 캐릭터 레벨 + 100",
        f"  L = {level:g} + 100 = {level_factor:g}",
        "  적 패널 방어 = DefBase × (1 + DefUp) + DefAdd",
        f"    = {defense_base:g} × (1 + {defense_up * 100:g}%) + "
        f"{defense_add:g} = {panel_defense:g}",
        "  적 유효 방어 = 적 패널 방어 / 6 × (1 - 방어 관통) × "
        "(1 - 방어 감소)",
        f"    = {panel_defense:g} / 6 × (1 - {penetration * 100:g}%) × "
        f"(1 - {reduction * 100:g}%) = {effective_defense:g}",
        f"  방어 구간 = {level_factor:g} / ({effective_defense:g} + "
        f"{level_factor:g}) = {_factor_value(factor)}",
    ]


def _resistance_formula_lines(factor: BattleHitReplayFactor) -> list[str]:
    base_terms = tuple(
        term
        for term in factor.terms
        if term.property_id.startswith("Resistance:")
    )
    penetration_terms = tuple(
        term
        for term in factor.terms
        if term.property_id.startswith("DamagePenetrate")
    )
    resistance_modifiers = tuple(
        term
        for term in factor.terms
        if term.property_id.startswith("DamageResist")
    )
    if not base_terms:
        return []
    base = sum(term.value for term in base_terms)
    modifier_values = tuple(term.value for term in resistance_modifiers)
    target_resistance = base + sum(modifier_values)
    penetration = sum(term.value for term in penetration_terms)
    effective = target_resistance - penetration
    target_expression = _signed_percent_expression(base, modifier_values)
    comparison = ">= 0" if effective >= 0 else "< 0"
    branch_formula = (
        "1 - 유효 저항"
        if effective >= 0
        else "1 - 유효 저항 / 1.10"
    )
    branch_substitution = (
        f"1 - {effective * 100:g}%"
        if effective >= 0
        else f"1 - ({effective * 100:g}% / 1.10)"
    )
    return [
        "저항 구간 = 저항 구간 함수(유효 저항)",
        f"  대상 저항 출처: {_source_names((*base_terms, *resistance_modifiers))}",
        f"  대상 저항 = {target_expression} = {target_resistance * 100:g}%",
        f"  속성 관통 출처: {_source_names(penetration_terms)}",
        f"  속성 관통 합계 = {penetration * 100:g}%",
        "  유효 저항 = 대상 저항 - 속성 관통",
        f"    = {target_resistance * 100:g}% - {penetration * 100:g}% "
        f"= {effective * 100:g}%",
        f"  유효 저항 {effective * 100:g}% {comparison}이므로 채택:"
        f"{branch_formula}",
        f"  저항 구간 = {branch_substitution} = {_factor_value(factor)}",
    ]


def _factor_lines(factor: BattleHitReplayFactor) -> list[str]:
    if factor.factor_id.startswith("topple_character:"):
        lines = [
            f"{factor.label} = 레벨 기본값 × 브레이크 강도 구간 × "
            "적 브레이크 상한 구간 × 방어 구간 × 저항 구간",
            f"  = {factor.formula}",
            f"  = {_factor_value(factor)}",
        ]
        if factor.terms:
            lines.append("  이 칸 출처:")
            lines.extend(
                f"    - {term.source_name}:{term.label} = {_term_value(term)}"
                f"({term.evidence_basis})"
                for term in factor.terms
            )
        lines.append(f"  근거: {factor.evidence_basis}")
        return lines
    if factor.factor_id == "scaling":
        lines = _scaling_formula_lines(factor)
    elif factor.factor_id == "defense" and (
        detailed := _defense_formula_lines(factor)
    ):
        lines = detailed
    elif factor.factor_id == "resistance" and (
        detailed := _resistance_formula_lines(factor)
    ):
        lines = detailed
    else:
        lines = [f"{factor.label} = {factor.formula or '结构化计算值'}"]
        if factor.terms:
            names, values = _sum_terms(factor.terms)
            lines.extend((f"  출처 항: {names}", f"  대입 항: {values}"))
        lines.append(f"  = {_factor_value(factor)}")
    lines.append(f"  근거: {factor.evidence_basis}")
    return lines


class BattleHitReplayExplanationService:
    """Build deterministic dialog text without reading UI or storage state."""

    @classmethod
    def build(
        cls,
        hit: BattleAnalysisHit,
        replay: BattleHitReplayResult | None,
        *,
        active_buffs: Sequence[BattleInferredBuffInterval] = (),
        counterfactual: BattleBuildHitCounterfactual | None = None,
        projection=None, allow_projection_fallback: bool = True,
    ) -> str:
        damage_name = preferred_battle_damage_name(
            hit.damage_name,
            hit.skill_name,
            hit.ability_id,
        )
        formula_type = (
            replay.formula_type
            if replay is not None and replay.formula_type != "未分类"
            else classify_battle_hit_channel(hit)[1]
        )
        lines = [
            f"{hit.character_name} · {damage_name}",
            f"히트 ID: {hit.event_id}",
            f"대응 공식 유형: {formula_type}",
            f"대상: {hit.target_name}",
            "",
        ]
        if replay is not None and replay.formula_context_kind:
            lines.extend((
                "【파생 공식 귀속】",
                (
                    f"실행 캐릭터: {replay.formula_action_character_id or '未知'}; 배율 정의 캐릭터:"
                    f"{replay.formula_definition_owner_character_id or '未知'};"
                    f"패널 캐릭터: {replay.formula_panel_character_id or '未知'}"
                ),
                f"스킬 레벨 캐릭터: {replay.formula_skill_level_character_id or '未知'}; 레벨 스킬: {replay.formula_skill_level_ability_id or '未知'}",
                f"공식 속성: {replay.formula_damage_attribute or 'unknown'}; 속성 출처: {replay.formula_damage_attribute_source or 'unknown'}",
                f"추론 유형: {replay.formula_context_kind}; 신뢰도: {replay.formula_context_confidence or '未知'}",
                f"판단 근거: {replay.formula_context_basis}",
                "기준: 여기서는 공식 소비자만 다루며, Core 원본 히트 캐릭터·동작 레인·스킬 구성은 덮어쓰지 않습니다.",
                "",
            ))
        if counterfactual is not None:
            quantification = counterfactual.quantification
            projected_damage = (
                counterfactual.candidate_damage
                if counterfactual.candidate_damage is not None
                else counterfactual.known_projection_damage
            )
            delta = (
                None
                if projected_damage is None
                else projected_damage - counterfactual.baseline_damage
            )
            gain_percent = (
                delta / counterfactual.baseline_damage * 100.0
                if delta is not None and counterfactual.baseline_damage
                else None
            )
            direction = (
                "미정량화"
                if delta is None
                else "提升" if delta > 0 else "감소" if delta < 0 else "변동 없음"
            )
            method = _COUNTERFACTUAL_METHOD_LABELS.get(
                quantification.method,
                quantification.method,
            )
            projection_label = (
                "완전 후보"
                if counterfactual.candidate_damage is not None
                else "정량화된 변화"
                if counterfactual.known_projection_damage is not None
                else "후보 미정량화"
            )
            lines.extend((
                "【조정 후 한계 이득】",
                (
                    f"원본 히트: {counterfactual.baseline_damage:,.2f}    "
                    f"{projection_label}: {_damage(projected_damage)}"
                ),
                (
                    f"{direction}: {_damage(delta)} ("
                    f"{'—' if gain_percent is None else f'{gain_percent:+.2f}%'})    "
                    f"정량화 상태: {quantification.status}    "
                    f"근거 신뢰도: {quantification.confidence}"
                ),
                (
                    f"원본 공식값: {_damage(counterfactual.baseline_formula_damage)}    "
                    f"후보 공식값: {_damage(counterfactual.candidate_formula_damage)}"
                ),
                f"정량화 방법: {method}",
                f"설명: {quantification.explanation}",
                *(
                    (f"휴리스틱 참고: {counterfactual.heuristic_projection_damage:,.2f} (정량화 이득에 포함되지 않음)",)
                    if counterfactual.heuristic_projection_damage is not None
                    else ()
                ),
                *(
                    ("미해결 의존성:" + "；".join(
                        gap.explanation for gap in quantification.gaps
                    ),)
                    if quantification.gaps
                    else ()
                ),
                *(
                    (
                        f"원본 축 트리거 히트: {counterfactual.source_event_id}",
                        "기준: 이는 후보 구성이 기존 트리거 시점에 새로 추가한 파생 결산입니다."
                        "기준선은 0이며, 원본 트리거 히트를 덮어쓰지 않고 새로운 실측 히트도 아닙니다.",
                    )
                    if counterfactual.source_event_id
                    else (
                        "기준: 원본 전투 리포트의 동작과 히트를 고정하고 이 히트의 후보 피해만 바꿉니다."
                        "이는 새로운 실측 히트가 아닙니다.",
                    )
                ),
                "",
            ))
        if replay is None:
            lines.extend((
                (
                    "후보 구성 공식: —"
                    if counterfactual is not None
                    else f"실제 피해: {hit.damage:,.2f}    예상 피해: —    예상 오차: —"
                ),
                "추론 치명타: 추론 불가",
                "",
                (
                    "현재 히트에는 후보 구성 공식이 없습니다. 위 수치는 등급별 추정에서 나온 것입니다."
                    if counterfactual is not None
                    else "현재 히트에는 리플레이 결과가 없습니다. 기록 분석은 최신 모델로 다시 불러와야 합니다."
                ),
            ))
            return "\n".join(lines)

        signed_error = replay.signed_error_percent
        if (
            signed_error is None
            and replay.selected_damage is not None
            and replay.observed_damage > 0
        ):
            signed_error = (
                (replay.selected_damage - replay.observed_damage)
                / replay.observed_damage
                * 100.0
            )
        if counterfactual is None:
            lines.extend((
                (
                    f"실제 피해: {_damage(replay.observed_damage)}    "
                    f"예상 피해: {_damage(replay.selected_damage)}"
                    f"예상 오차: {_percent(signed_error, signed=True)}"
                ),
                "오차 정의: (예상 피해 - 실제 피해) / 실제 피해; 양수는 과대 추정, 음수는 과소 추정입니다.",
                (
                    f"예상 피해 기댓값: {_damage(replay.expected_damage)}"
                    f"실제 피해 기댓값: {_damage(replay.corrected_expected_damage)}"
                ),
                (
                    "기댓값 기준: 내림 처리한 비치명타/치명타 후보를 치명 확률로 가중;"
                    "실제 기댓값은 이번 히트의 부호 있는 오차에 비례해 보정합니다."
                ),
                (
                    f"치명타 추정: {_CRIT_STATES.get(replay.critical_state, replay.critical_state)}"
                    f"(신뢰도 {replay.confidence})"
                ),
                "",
            ))
            if replay.observed_damage_source != "reported_hit":
                reported = (
                    hit.damage
                    if replay.reported_damage is None
                    else replay.reported_damage
                )
                lines.extend((
                    f"공식 비교 관측 출처: {replay.observed_damage_basis}",
                    (
                        (
                            f"전투 리포트 반영 유효 피해: {_damage(reported)};"
                            if replay.observed_damage_source
                            == "reported_hit_before_overkill"
                            else f"원본 히트별 보고값: {_damage(reported)};"
                        )
                        + f"공식 비교 가능 관측값: {_damage(replay.observed_damage)}."
                        "둘은 서로 독립적으로 유지됩니다."
                    ),
                    "",
                ))
        else:
            lines.extend((
                (
                    "후보 구성 치명타 추정:"
                    f"{_CRIT_STATES.get(replay.critical_state, replay.critical_state)}"
                    f"(신뢰도 {replay.confidence})"
                ),
                "",
            ))

        factors = {factor.factor_id: factor for factor in replay.factors}
        formula_factors = [
            factors[factor_id]
            for factor_id in _FORMULA_FACTOR_IDS
            if factor_id in factors
        ]
        reaction_formula_factors = [
            factors[factor_id]
            for factor_id in _REACTION_FORMULA_FACTOR_IDS
            if factor_id in factors
        ]
        lines.append(
            "【후보 구성 피해 공식】"
            if counterfactual is not None
            else "【피해 공식】"
        )
        topple_cells = tuple(
            factor
            for factor in replay.factors
            if factor.factor_id.startswith("topple_character:")
        )
        if topple_cells:
            expression = " + ".join(factor.label for factor in topple_cells)
            substituted = " + ".join(
                _factor_value(factor) for factor in topple_cells
            )
            raw_damage = sum(factor.value for factor in topple_cells)
            lines.extend((
                f"팀 브레이크 피해 = {expression}",
                f"  = {substituted}",
                f"  = floor({raw_damage:,.6f}) = {_damage(replay.non_critical_damage)}",
            ))
        elif _REQUIRED_FORMULA_FACTOR_IDS.issubset(factors):
            expression = " × ".join(factor.label for factor in formula_factors)
            substituted = " × ".join(_factor_value(factor) for factor in formula_factors)
            noncrit = reduce(mul, (factor.value for factor in formula_factors), 1.0)
            lines.extend((
                f"피해 (비치명타) = {expression}",
                f"  = {substituted}",
                f"  = floor({noncrit:,.6f}) = {_damage(replay.non_critical_damage)}",
            ))
            critical = factors.get("critical")
            if critical is not None:
                raw_critical = noncrit * critical.value
                lines.extend((
                    f"피해 (치명타) = 내림 전 피해 × {critical.label}",
                    f"  = {noncrit:,.2f} × {_factor_value(critical)}",
                    f"  = floor({raw_critical:,.6f}) = {_damage(replay.critical_damage)}",
                ))
        elif _REQUIRED_REACTION_FACTOR_IDS.issubset(factors):
            expression = " × ".join(
                factor.label for factor in reaction_formula_factors
            )
            substituted = " × ".join(
                _factor_value(factor) for factor in reaction_formula_factors
            )
            noncrit = reduce(
                mul,
                (factor.value for factor in reaction_formula_factors),
                1.0,
            )
            lines.extend((
                f"피해 (비치명타) = {expression}",
                f"  = {substituted}",
                f"  = floor({noncrit:,.6f}) = {_damage(replay.non_critical_damage)}",
            ))
            critical = factors.get("critical")
            if critical is not None:
                raw_critical = noncrit * critical.value
                lines.extend((
                    f"피해 (치명타) = 내림 전 피해 × {critical.label}",
                    f"  = {noncrit:,.2f} × {_factor_value(critical)}",
                    f"  = floor({raw_critical:,.6f}) = {_damage(replay.critical_damage)}",
                ))
        else:
            missing_target = any(
                "단일 대상 방어 및 저항" in row
                for row in replay.missing_evidence
            )
            if replay.formula_type.startswith("直伤") and missing_target:
                lines.append(
                    "직접 피해 어댑터는 매칭되었지만 이번 히트에 쓸 수 있는 적 방어·저항이 없어,"
                    "당분간 수치 등식을 완성할 수 없습니다."
                )
            else:
                lines.append(
                    "현재 유형에는 아직 완전한 반사실 어댑터가 없어 수치 등식을 안전하게 구성할 수 없습니다."
                )

        if replay.factors:
            lines.extend(("", "【곱연산 구간 공식】"))
            for factor in replay.factors:
                lines.extend(_factor_lines(factor))
                lines.append("")

        if projection is None and allow_projection_fallback:
            projection = BattleBuffAttributeProjectionService.project_hit(hit, active_buffs)
        if projection is None:
            return "\n".join((*lines, "이 히트의 원본 Buff 투영 상세가 생성되지 않았습니다; 공식 결과는 이미 계산된 값을 유지합니다."))
        decision_by_id = {
            row.interval_id: row for row in projection.decisions
        }
        if not allow_projection_fallback:
            lines.append("Buff 투영 기준: 정식 공식 히트 뷰; 브레이크의 캐릭터별 속성은 기여 곱연산 구간을 기준으로 합니다.")
        lines.append(
            "【이번 히트 Buff: 투영됨 (공식에 반영되었는지는 곱연산 구간 참조)】"
            if not any(
                "단일 대상 방어 및 저항" in row
                for row in replay.missing_evidence
            )
            else "【이번 히트 Buff: 투영 가능 (공식 입력 불완전)】"
        )
        if active_buffs:
            applied = tuple(
                interval
                for interval in active_buffs
                if decision_by_id.get(interval.interval_id) is not None
                and decision_by_id[interval.interval_id].status == "applied"
            )
            for intervals, decision, interval_count, total_stacks in (
                _group_buff_intervals(applied, decision_by_id)
            ):
                interval = intervals[0]
                modifier_text = _buff_modifier_text(
                    intervals,
                    decision,
                    total_stacks,
                )
                merge_text = (
                    f", 같은 유형 구간 {interval_count}개 병합"
                    if interval_count > 1
                    else ""
                )
                lines.append(
                    f"- {interval.buff_name} ×{total_stacks}중첩"
                    f"({interval.target_scope}, 상태"
                    f"{_confidence_summary(tuple(row.state_confidence for row in intervals))},"
                    f"수치 {_confidence_summary(tuple(row.value_confidence for row in intervals))}"
                    f"{merge_text}):"
                    f"{modifier_text}\n"
                    f"  ID: {interval.source_effect_definition_id}\n"
                    f"  에셋: {interval.buff_asset_path}"
                )
            if not applied:
                lines.append("- 이번 히트 공식에 반영된 Buff 수치가 없습니다.")
        else:
            lines.append("- 투영 가능한 히트 시 Buff 구간을 찾지 못했습니다.")

        for status, title in (
            ("not_applied", "【이번 히트 Buff: 미채택】"),
            ("unresolved", "【이번 히트 Buff: 확인/구조화 대기】"),
        ):
            matching = tuple(
                interval
                for interval in active_buffs
                if decision_by_id.get(interval.interval_id) is not None
                and decision_by_id[interval.interval_id].status == status
            )
            if not matching:
                continue
            lines.extend(("", title))
            for intervals, decision, interval_count, total_stacks in (
                _group_buff_intervals(matching, decision_by_id)
            ):
                interval = intervals[0]
                reason = "；".join(decision.reasons) or "제외 이유 미기록"
                modifier_text = _buff_modifier_text(
                    intervals,
                    decision,
                    total_stacks,
                )
                if modifier_text != "구조화 규칙 적용됨":
                    reason += f"; 구조화 값: {modifier_text}"
                count_text = (
                    f" 같은 유형 구간 ×{interval_count}"
                    if interval_count > 1
                    else ""
                )
                stack_text = (
                    f", 총 {total_stacks} 중첩"
                    if total_stacks != interval_count
                    else ""
                )
                lines.append(
                    f"- {interval.buff_name}{count_text} ({reason}{stack_text})\n"
                    f"  ID: {interval.source_effect_definition_id}\n"
                    f"  에셋: {interval.buff_asset_path}"
                )

        lines.extend(("", "【근거 범위】"))
        if replay.missing_evidence:
            lines.extend(f"- {row}" for row in replay.missing_evidence)
        else:
            lines.append("- 현재 공식에 기록된 추가 결손은 없습니다; 히트별 관측값이 여전히 최종적인 강력한 근거입니다.")
        if counterfactual is not None:
            lines.append(
                "- 조정 후 수치는 고정 축 반사실에 해당합니다; 원본 실측 히트와 데이터베이스는 변경되지 않습니다."
            )
        return "\n".join(lines)
