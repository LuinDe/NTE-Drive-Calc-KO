# 计算固定轴逐击反事实的组件感知倍率。
"""Shared component-aware ratios for fixed-axis hit counterfactuals."""
from __future__ import annotations

from collections.abc import Mapping
from math import isfinite

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleQuantificationGap,
    DependencyScope,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_damage_composition_service import (
    classify_battle_hit_channel,
)
from src.services.battle_fixed_critical_ratio_service import (
    CONTINUOUS_DIRECT_CHANNEL_IDS,
    continuous_direct_attribute,
    fixed_half_critical_counterfactual,
)
from src.services.battle_hit_counterfactual_formula_support import (
    ELEMENT_PROPERTY as _ELEMENT_PROPERTY,
    SCALING_PROPERTIES as _SCALING_PROPERTIES,
    critical_ratio,
    increase_factor,
    projected_values,
    scaling_id,
    scaling_ratio,
)
from src.services.battle_replay_formula_ratio_service import (
    paired_replay_formula,
    structured_formula_ratio,
)
from src.services.battle_reaction_counterfactual_ratio_service import (
    compare_standard_reaction,
)
from src.services.battle_hit_counterfactual_target_support import (
    defense_ratio,
    level_changed,
    resistance_ratio,
    target_resistance_delta,
)
from src.services.damage_calculation_service import (
    calculate_weave_strength_multiplier,
)


_PENETRATION_PROPERTY = {
    "chaos": "DamagePenetrateChaos",
    "cosmos": "DamagePenetrateCosmos",
    "incantation": "DamagePenetrateIncantation",
    "lakshana": "DamagePenetrateLakshana",
    "nature": "DamagePenetrateNature",
    "psyche": "DamagePenetratePsyche",
    "psychically": "DamagePenetratePsychically",
}
_ALL_ELEMENT_PROPERTIES = frozenset(_ELEMENT_PROPERTY.values())
_ALL_PENETRATION_PROPERTIES = frozenset(_PENETRATION_PROPERTY.values())
_ALL_SCALING_PROPERTIES = frozenset(
    property_id
    for group in _SCALING_PROPERTIES.values()
    for property_id in group
)
_CRITICAL_PROPERTIES = frozenset({"CritBase", "CritDamageBase"})
_SUPPORTED_CHANNELS = frozenset({
    "direct",
    "direct_follow_up",
    "reaction_hexed",
    "special_kuhara_formula",
    *CONTINUOUS_DIRECT_CHANNEL_IDS,
})
_KUHARA_FORMULA_EFFECTS = frozenset({
    "ge_player_kuhara_seed_damage",
    "ge_player_kuhara_budboom_damage",
    "ge_player_kuhara_budend_damage",
    "ge_player_kuhara_seedreaction_damage",
})
_STANDARD_RING_CHANNELS = frozenset({
    "reaction_creation",
    "reaction_nova",
    "reaction_scorch",
})
_DIMENSION_PROPERTIES = {
    "scaling": _ALL_SCALING_PROPERTIES,
    "critical": _CRITICAL_PROPERTIES,
    "damage_increase": frozenset({
        "DamageUpGeneralBase",
        *_ALL_ELEMENT_PROPERTIES,
    }),
    "ring_strength": frozenset({"MagBase"}),
    "target_defense": frozenset({"DefIgnore"}),
    "target_resistance": _ALL_PENETRATION_PROPERTIES,
}


def _changed(
    original: Mapping[str, float],
    candidate: Mapping[str, float],
    property_ids: set[str] | frozenset[str] | tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(sorted(
        property_id
        for property_id in property_ids
        if abs(
            float(candidate.get(property_id, 0.0))
            - float(original.get(property_id, 0.0))
        ) > 1e-12
    ))


def _safe_ratio(candidate: float, original: float) -> float | None:
    if original <= 0.0 or candidate < 0.0:
        return None
    ratio = candidate / original
    return ratio if isfinite(ratio) and ratio >= 0.0 else None


def _gap(
    code: str,
    dimension_id: str,
    scope: DependencyScope,
    property_ids: tuple[str, ...],
    explanation: str,
) -> BattleQuantificationGap:
    return BattleQuantificationGap(
        code=code,
        dimension_id=dimension_id,
        dependency_scope=scope,
        property_ids=property_ids,
        explanation=explanation,
    )


class BattleHitCounterfactualRatioService:
    """Quantify only changed dimensions and cancel unchanged shared factors."""

    @classmethod
    def compare(
        cls,
        *,
        hit: BattleAnalysisHit,
        original_baseline: BattleCharacterBaseline | None,
        candidate_baseline: BattleCharacterBaseline | None,
        original_projection: BattleHitBuffProjection | None = None,
        candidate_projection: BattleHitBuffProjection | None = None,
        skill_evidence: BattleSkillDamageEvidence | None = None,
        original_replay: BattleHitReplayResult | None = None,
        candidate_replay: BattleHitReplayResult | None = None,
        target_condition: BattleTargetCondition | None = None,
    ) -> BattleCounterfactualRatio:
        """Compare one hit using its already-resolved frozen target condition.

        The caller owns target routing and must pass the result of
        ``BattleTargetInstanceMappingService.analysis_for_hit``. This Service
        never looks up a primary target or display-only monster identity.
        """
        structured = structured_formula_ratio(
            paired_replay_formula(original_replay, candidate_replay),
        )
        if structured is not None:
            return structured

        channel_id, _channel_label = classify_battle_hit_channel(hit)
        if cls.is_kuhara_formula_hit(hit):
            channel_id = "special_kuhara_formula"
        original = projected_values(original_baseline, original_projection)
        candidate = projected_values(candidate_baseline, candidate_projection)
        if not original or not candidate:
            gap = _gap(
                "scaling_dependency_unresolved",
                "character_panel",
                "character_only",
                (),
                "원본 또는 후보 캐릭터 패널이 없어 캐릭터 측 곱연산 구간을 비교할 수 없습니다.",
            )
            return BattleCounterfactualRatio.unavailable(
                method="component_ratio_unavailable",
                confidence="低",
                dependency_scope="character_only",
                cancelled_dimension_ids=(),
                gaps=(gap,),
                explanation=gap.explanation,
            )

        changed_properties = set(_changed(
            original,
            candidate,
            set(original) | set(candidate),
        ))
        fixed_critical = fixed_half_critical_counterfactual(
            channel_id=channel_id,
            changed_properties=changed_properties,
            original=original,
            candidate=candidate,
            replay=original_replay,
        )
        if fixed_critical is not None:
            return cls._apply_projection_evidence_boundary(
                fixed_critical,
                original_projection,
                candidate_projection,
            )
        if channel_id in _STANDARD_RING_CHANNELS:
            return cls._apply_projection_evidence_boundary(
                compare_standard_reaction(
                    channel_id=channel_id,
                    hit=hit,
                    original=original,
                    candidate=candidate,
                    changed_properties=changed_properties,
                    original_baseline=original_baseline,
                    candidate_baseline=candidate_baseline,
                    original_projection=original_projection,
                    candidate_projection=candidate_projection,
                    replay=original_replay,
                    target_condition=target_condition,
                    penetration_properties=_PENETRATION_PROPERTY,
                    ring_strength_ratio=cls._ring_strength_ratio,
                ),
                original_projection,
                candidate_projection,
            )
        if changed_properties == {"MagBase"} and cls.supports_ring_strength(
            hit,
            original_replay,
        ):
            ratio = cls._ring_strength_ratio(
                channel_id=channel_id,
                original_strength=max(0.0, original.get("MagBase", 0.0)),
                candidate_strength=max(0.0, candidate.get("MagBase", 0.0)),
                replay=original_replay,
            )
            if ratio is not None:
                return cls._apply_projection_evidence_boundary(
                    BattleCounterfactualRatio.complete(
                        ratio,
                        method="structured_ring_ratio",
                        confidence="高",
                        dependency_scope="character_only",
                        included_dimension_ids=("ring_strength",),
                        explanation=(
                            "이 히트에 저장된 정식 사이클 공식 분기에 따라 사이클 강도 곱연산 구간만 교체합니다."
                        ),
                    ),
                    original_projection,
                    candidate_projection,
                )
            gap = _gap(
                "ring_strength_dependency_unresolved",
                "ring_strength",
                "mechanic_specific",
                ("MagBase",),
                "히트에 사이클 공식 표시는 있지만 비교할 수 있는 원본 사이클 강도 인자가 없습니다.",
            )
            return BattleCounterfactualRatio.unavailable(
                method="structured_ring_ratio_unavailable",
                confidence="低",
                dependency_scope="mechanic_specific",
                cancelled_dimension_ids=(),
                gaps=(gap,),
                explanation=gap.explanation,
            )

        if channel_id in {
            "other_reflected_projectile",
            "special_fadia_shared_damage",
        }:
            explanation = (
                "투사체 반사는 속성 한계 이득에서 명시적으로 제외되며, 그 출처 연동을 추측하지 않습니다."
                if channel_id == "other_reflected_projectile"
                else (
                    "파디아 공유 피해에는 300%/600%와 MAXHP 상한이라는 사실이 있지만,"
                    "히트별 피격 출처 연동 근거가 없습니다."
                )
            )
            gap = _gap(
                (
                    "reflected_projectile_unsupported"
                    if channel_id == "other_reflected_projectile"
                    else "fadia_shared_source_unresolved"
                ),
                "source_linkage",
                "mechanic_specific",
                tuple(sorted(changed_properties)),
                explanation,
            )
            return BattleCounterfactualRatio.unavailable(
                method="unsupported_source_linkage",
                confidence="低",
                dependency_scope="mechanic_specific",
                cancelled_dimension_ids=(),
                gaps=(gap,),
                explanation=explanation,
            )
        if channel_id not in _SUPPORTED_CHANNELS:
            gap = _gap(
                "formula_family_unsupported",
                "formula_family",
                "mechanic_specific",
                (),
                "현재 히트 공식 계열은 아직 공용 곱연산 구간 비교에 연결되지 않았습니다.",
            )
            return BattleCounterfactualRatio.unavailable(
                method="component_ratio_unavailable",
                confidence="低",
                dependency_scope="mechanic_specific",
                cancelled_dimension_ids=(),
                gaps=(gap,),
                explanation=gap.explanation,
            )

        included: list[str] = []
        cancelled: list[str] = []
        gaps: list[BattleQuantificationGap] = []
        component_ratio = 1.0
        handled_properties: set[str] = set()
        target_sensitive_change = False
        mechanic_specific_change = False

        resolved_scaling_id = scaling_id(
            skill_evidence,
            original_replay,
            candidate_replay,
            channel_id=channel_id,
        )
        handled_properties.update(_ALL_SCALING_PROPERTIES)
        all_scaling_changes = _changed(
            original,
            candidate,
            _ALL_SCALING_PROPERTIES,
        )
        scaling_properties = (
            ()
            if resolved_scaling_id is None
            else _SCALING_PROPERTIES[resolved_scaling_id]
        )
        scaling_changes = _changed(original, candidate, scaling_properties)
        if resolved_scaling_id is None and all_scaling_changes:
            gaps.append(_gap(
                "scaling_dependency_unresolved",
                "scaling",
                "character_only",
                all_scaling_changes,
                "캐릭터 스케일링 속성이 바뀌었지만 이 히트에는 정식 스케일링 속성 근거가 없습니다.",
            ))
        elif scaling_changes:
            ratio = scaling_ratio(
                original,
                candidate,
                scaling_properties,
            )
            if ratio is None:
                gaps.append(_gap(
                    "scaling_dependency_unresolved",
                    "scaling",
                    "character_only",
                    scaling_changes,
                    "캐릭터 스케일링 속성에 유효한 기준값이 없습니다.",
                ))
            else:
                component_ratio *= ratio
                included.append("scaling")
        else:
            cancelled.append("scaling")

        critical_changes = _changed(original, candidate, _CRITICAL_PROPERTIES)
        handled_properties.update(_CRITICAL_PROPERTIES)
        if critical_changes:
            ratio = critical_ratio(
                original,
                candidate,
                original_replay,
                channel_id=channel_id,
            )
            if ratio is None:
                gaps.append(_gap(
                    "critical_policy_unknown",
                    "critical",
                    "character_only",
                    critical_changes,
                    "치명타 곱연산 구간이 바뀌었지만 정식 치명타 정책 또는 고정 확률을 알 수 없습니다.",
                ))
            else:
                component_ratio *= ratio
                included.append("critical")
        else:
            cancelled.append("critical")

        attribute = (
            "nature"
            if channel_id == "special_kuhara_formula"
            else continuous_direct_attribute(hit)
            or hit.damage_attribute.casefold()
        )
        increase_properties = {
            "DamageUpGeneralBase",
            _ELEMENT_PROPERTY.get(attribute, ""),
        } - {""}
        increase_changes = _changed(original, candidate, increase_properties)
        handled_properties.update(_ALL_ELEMENT_PROPERTIES)
        handled_properties.add("DamageUpGeneralBase")
        if increase_changes:
            ratio = _safe_ratio(
                increase_factor(candidate, attribute),
                increase_factor(original, attribute),
            )
            if ratio is None:
                gaps.append(_gap(
                    "damage_increase_dependency_unresolved",
                    "damage_increase",
                    "character_only",
                    increase_changes,
                    "캐릭터 피해 증가 곱연산 구간에 유효한 기준값이 없습니다.",
                ))
            else:
                component_ratio *= ratio
                included.append("damage_increase")
        else:
            cancelled.append("damage_increase")

        weave_changes = _changed(original, candidate, ("MagBase",))
        handled_properties.add("MagBase")
        if weave_changes:
            mechanic_specific_change = True
            gaps.append(_gap(
                "ring_strength_dependency_unresolved",
                "ring_strength",
                "mechanic_specific",
                weave_changes,
                "이 히트에는 확인 가능한 사이클 강도 공식 인자가 저장되어 있지 않아, 이름만으로 이득을 추측할 수 없습니다.",
            ))
        else:
            cancelled.append("weave")

        has_level_change = level_changed(original_baseline, candidate_baseline)
        defense_changes = _changed(original, candidate, ("DefIgnore",))
        handled_properties.add("DefIgnore")
        if defense_changes or has_level_change:
            if attribute == "true":
                mechanic_specific_change = True
                gaps.append(_gap(
                    "true_attribute_override_missing",
                    "target_defense",
                    "mechanic_specific",
                    tuple((
                        *defense_changes,
                        *(("character_level",) if has_level_change else ()),
                    )),
                    (
                        "정적 TRUE에는 독립 저항이 없습니다. 이 채널에는 TRUE 외피를"
                        "정식 캐릭터 속성으로 환원하는 전용 공식 어댑터가 없습니다."
                    ),
                ))
            elif attribute == "psychically":
                cancelled.append("target_defense")
            elif target_condition is None:
                target_sensitive_change = True
                property_ids = tuple((
                    *defense_changes,
                    *(("character_level",) if has_level_change else ()),
                ))
                gaps.append(_gap(
                    "target_defense_dependency_changed",
                    "target_defense",
                    "target_sensitive",
                    property_ids,
                    "방어 곱연산 구간이 바뀌었지만 이 히트에는 고정된 적 방어 프로필이 없습니다.",
                ))
            else:
                target_sensitive_change = True
                ratio = defense_ratio(
                    original,
                    candidate,
                    original_baseline,
                    candidate_baseline,
                    target_condition,
                )
                if ratio is None:
                    gaps.append(_gap(
                        "target_defense_dependency_changed",
                        "target_defense",
                        "target_sensitive",
                        defense_changes,
                        "방어 곱연산 구간에 유효한 기준값이 없습니다.",
                    ))
                else:
                    component_ratio *= ratio
                    included.append("target_defense")
        else:
            cancelled.append("target_defense")

        penetration_property = _PENETRATION_PROPERTY.get(attribute, "")
        penetration_changes = _changed(
            original,
            candidate,
            (() if not penetration_property else (penetration_property,)),
        )
        handled_properties.update(_ALL_PENETRATION_PROPERTIES)
        original_target_delta = target_resistance_delta(
            original_projection,
            attribute,
        )
        candidate_target_delta = target_resistance_delta(
            candidate_projection,
            attribute,
        )
        target_delta_changed = abs(candidate_target_delta - original_target_delta) > 1e-12
        if penetration_changes or target_delta_changed:
            resistance_properties = tuple((
                *penetration_changes,
                *(("target_resistance_modifier",) if target_delta_changed else ()),
            ))
            if attribute == "true":
                mechanic_specific_change = True
                gaps.append(_gap(
                    "true_attribute_override_missing",
                    "target_resistance",
                    "mechanic_specific",
                    resistance_properties,
                    "정적 TRUE에는 읽거나 관통할 수 있는 독립 속성 저항이 없습니다.",
                ))
            elif target_condition is None:
                target_sensitive_change = True
                gaps.append(_gap(
                    "target_resistance_dependency_changed",
                    "target_resistance",
                    "target_sensitive",
                    resistance_properties,
                    "저항 곱연산 구간이 바뀌었지만 이 히트에는 고정된 속성별 저항 프로필이 없습니다.",
                ))
            else:
                target_sensitive_change = True
                ratio = resistance_ratio(
                    original,
                    candidate,
                    attribute,
                    penetration_property,
                    original_target_delta,
                    candidate_target_delta,
                    target_condition,
                )
                if ratio is None:
                    gaps.append(_gap(
                        "target_resistance_dependency_changed",
                        "target_resistance",
                        "target_sensitive",
                        resistance_properties,
                        "저항 곱연산 구간에 유효한 기준값이 없습니다.",
                    ))
                else:
                    component_ratio *= ratio
                    included.append("target_resistance")
        else:
            cancelled.append("target_resistance")
        cancelled.append("target_vulnerability")

        unhandled = tuple(sorted(changed_properties - handled_properties))
        if unhandled:
            mechanic_specific_change = True
            gaps.append(_gap(
                "formula_family_unsupported",
                "unmapped_change",
                "mechanic_specific",
                unhandled,
                "후보가 아직 공용 히트 공식에 매핑되지 않은 속성을 변경했습니다.",
            ))

        included_ids = tuple(dict.fromkeys(included))
        cancelled_ids = tuple(
            dimension_id
            for dimension_id in dict.fromkeys(cancelled)
            if dimension_id not in included_ids
        )
        gap_rows = tuple(gaps)
        scope: DependencyScope = "character_only"
        if mechanic_specific_change:
            scope = "mechanic_specific"
        elif target_sensitive_change:
            scope = "target_sensitive"

        if gap_rows and included_ids:
            return BattleCounterfactualRatio.partial(
                component_ratio,
                method="component_ratio_partial",
                confidence="低",
                dependency_scope=scope,
                included_dimension_ids=included_ids,
                cancelled_dimension_ids=cancelled_ids,
                gaps=gap_rows,
                explanation=(
                    "입력이 완전한 변화 곱연산 구간만 계산합니다. 누락 성분은 이 비율에 들어가지 않았으므로,"
                    "결과가 완전한 이득이나 이득 하한을 나타내지는 않습니다."
                ),
            )
        if gap_rows:
            return BattleCounterfactualRatio.unavailable(
                method="component_ratio_unavailable",
                confidence="低",
                dependency_scope=scope,
                cancelled_dimension_ids=cancelled_ids,
                gaps=gap_rows,
                explanation="이번 관련 변화에 필요한 입력이 없어 후보 비율을 생성할 수 없습니다.",
            )
        if not included_ids:
            return BattleCounterfactualRatio.not_applicable(
                method="component_ratio_not_applicable",
                dependency_scope=scope,
                cancelled_dimension_ids=cancelled_ids,
                explanation="이번 변화가 이 히트에 작용하지 않음이 증명되어 원래 값을 정확히 유지합니다.",
            )
        return cls._apply_projection_evidence_boundary(
            BattleCounterfactualRatio.complete(
                component_ratio,
                method="component_ratio",
                confidence="中",
                dependency_scope=scope,
                included_dimension_ids=included_ids,
                cancelled_dimension_ids=cancelled_ids,
                explanation="모든 변화 곱연산 구간을 정량화했습니다. 나머지 공통 곱연산 구간은 전후 비율에서 상쇄됩니다.",
            ),
            original_projection,
            candidate_projection,
        )

    @staticmethod
    def _apply_projection_evidence_boundary(
        result: BattleCounterfactualRatio,
        original_projection: BattleHitBuffProjection | None,
        candidate_projection: BattleHitBuffProjection | None,
    ) -> BattleCounterfactualRatio:
        """Keep formula completeness separate from inferred state evidence."""

        if result.status not in {"complete", "partial"}:
            return result
        relevant_properties = frozenset(
            property_id
            for dimension_id in result.included_dimension_ids
            for property_id in _DIMENSION_PROPERTIES.get(dimension_id, ())
        )
        if "target_resistance" in result.included_dimension_ids:
            resistance_prefix = "damageresist"
        else:
            resistance_prefix = ""
        uncertain = tuple(sorted({
            modifier.property_id
            for projection in (original_projection, candidate_projection)
            if projection is not None
            for modifier in projection.modifiers
            if modifier.confidence != "高"
            and (
                modifier.property_id in relevant_properties
                or (
                    resistance_prefix
                    and modifier.property_id.casefold().startswith(
                        resistance_prefix
                    )
                )
            )
        }))
        if not uncertain:
            return result
        gap = _gap(
            "buff_state_inferred",
            "buff_state_evidence",
            result.dependency_scope,
            uncertain,
            (
                "관련 곱연산 구간 공식은 계산되었지만 고신뢰가 아닌 Buff/대상 상태가 포함되어 있습니다."
                "수치는 해당 추론 상태가 성립할 때의 조건부 결과만 나타냅니다."
            ),
        )
        return BattleCounterfactualRatio.partial(
            float(result.quantified_ratio),
            method=f"{result.method}_state_inferred",
            confidence="低",
            dependency_scope=result.dependency_scope,
            included_dimension_ids=result.included_dimension_ids,
            cancelled_dimension_ids=result.cancelled_dimension_ids,
            gaps=tuple((*result.gaps, gap)),
            explanation=(
                "공식 변화 곱연산 구간은 정량화되었지만, 관련 Buff/대상 상태가 추론에 불과하므로"
                "결과를 부분 정량화로 표시합니다."
            ),
        )

    @staticmethod
    def supports_ring_strength(
        hit: BattleAnalysisHit,
        replay: BattleHitReplayResult | None,
    ) -> bool:
        """Accept only replay-proven ring consumers; stain remains unsupported."""

        if replay is None or replay.critical_state == "unreplayable":
            return False
        channel_id, _channel_label = classify_battle_hit_channel(hit)
        factor_ids = {factor.factor_id for factor in replay.factors}
        if channel_id == "reaction_hexed":
            return {"weave_strength", "weave_followup"} <= factor_ids
        return channel_id in _STANDARD_RING_CHANNELS and "scaling" in factor_ids

    @staticmethod
    def is_kuhara_formula_hit(hit: BattleAnalysisHit) -> bool:
        identity = hit.gameplay_effect_id.replace("\\", "/").rsplit("/", 1)[-1]
        normalized = identity.casefold().removesuffix("_c")
        return normalized in _KUHARA_FORMULA_EFFECTS

    @staticmethod
    def _ring_strength_ratio(
        *,
        channel_id: str,
        original_strength: float,
        candidate_strength: float,
        replay: BattleHitReplayResult | None,
    ) -> float | None:
        if replay is None:
            return None
        factors = {factor.factor_id: float(factor.value) for factor in replay.factors}
        if channel_id in _STANDARD_RING_CHANNELS:
            if "scaling" not in factors:
                return None
            return _safe_ratio(
                1.0 + candidate_strength / 600.0,
                1.0 + original_strength / 600.0,
            )
        if channel_id != "reaction_hexed":
            return None
        original_zone = factors.get("weave_strength")
        original_followup = factors.get("weave_followup")
        if (
            original_zone is None
            or original_followup is None
            or original_zone <= 0.0
        ):
            return None
        base_multiplier = (original_followup + 1.0) / original_zone
        candidate_followup = (
            base_multiplier * calculate_weave_strength_multiplier(candidate_strength)
            - 1.0
        )
        return _safe_ratio(candidate_followup, original_followup)

__all__ = ["BattleHitCounterfactualRatioService"]
