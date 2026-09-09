# 重放已由真实战报和静态等级曲线共同验证的特殊伤害。
"""Narrow per-hit adapters for non-direct battle damage channels."""

from __future__ import annotations

from collections.abc import Mapping

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitBuffProjection,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleHitReplayTerm,
    BattleSkillDamageEvidence,
)
from src.services.battle_hit_replay_support import (
    dot_final_replay_factors,
    literal_replay_term,
)
from src.services.battle_weave_source_service import find_paired_weave_source_hit
from src.services.battle_special_replay_numeric import (
    mitigation_input, numeric_replay,
)


_ELEMENT_PENETRATION_PROPERTIES = {
    "chaos": "DamagePenetrateChaos",
    "cosmos": "DamagePenetrateCosmos",
    "incantation": "DamagePenetrateIncantation",
    "lakshana": "DamagePenetrateLakshana",
    "nature": "DamagePenetrateNature",
    "psyche": "DamagePenetratePsyche",
    "psychically": "DamagePenetratePsychically",
}

_ORDINARY_SCORCH_DAMAGE_ID = "buff_reaction_5_new"
_ZANKOU_SCORCH_DAMAGE_ID = "buff_reaction_5_new_1036"


def _factor(
    factor_id: str,
    label: str,
    value: float,
    basis: str,
    formula: str,
    *,
    terms: tuple[BattleHitReplayTerm, ...] = (),
) -> BattleHitReplayFactor:
    return BattleHitReplayFactor(
        factor_id=factor_id,
        label=label,
        value=float(value),
        evidence_basis=basis,
        formula=formula,
        terms=terms,
    )


class BattleSpecialHitReplayService:
    """Replay only special channels whose current formula is fully bounded."""

    @classmethod
    @numeric_replay
    def replay(
        cls,
        *,
        channel_id: str,
        formula_label: str,
        hit: BattleAnalysisHit,
        evidence: BattleSkillDamageEvidence | None,
        projection: BattleHitBuffProjection,
        values: Mapping[str, float],
        analysis: BattleAnalysisSnapshot,
    ) -> BattleHitReplayResult | None:
        if channel_id == "reaction_hexed":
            return cls._replay_weave(
                hit=hit,
                projection=projection,
                values=values,
                analysis=analysis,
                formula_label=formula_label,
            )
        if evidence is None:
            return None
        if channel_id == "reaction_nova":
            return cls._replay_dark_star(
                hit=hit,
                evidence=evidence,
                projection=projection,
                values=values,
                analysis=analysis,
                formula_label=formula_label,
            )
        if channel_id in {"reaction_creation", "reaction_scorch"}:
            return cls._replay_standard_reaction(
                channel_id=channel_id,
                hit=hit,
                evidence=evidence,
                projection=projection,
                values=values,
                analysis=analysis,
                formula_label=formula_label,
            )
        return None

    @staticmethod
    def _replay_weave(
        *,
        hit: BattleAnalysisHit,
        projection: BattleHitBuffProjection,
        values: Mapping[str, float],
        analysis: BattleAnalysisSnapshot,
        formula_label: str,
    ) -> BattleHitReplayResult:
        triggering_hit = find_paired_weave_source_hit(hit, analysis.hits)
        if triggering_hit is None:
            return BattleHitReplayResult(
                event_id=hit.event_id,
                observed_damage=hit.damage,
                non_critical_damage=None,
                critical_damage=None,
                selected_damage=None,
                selected_error_percent=None,
                critical_state="unreplayable",
                confidence="未解析",
                factors=(),
                missing_evidence=("헥스와 같은 정식 이벤트에 기록된 원본 피해가 없습니다",),
                formula_type=formula_label,
            )
        baseline = next(
            (
                row
                for row in analysis.baselines
                if row.character_id == hit.character_id
            ),
            None,
        )
        lingke_passive = bool(
            baseline is not None
            and "PASSIVE-1072-GA_Radio072_Passive_1"
            in baseline.enabled_team_passive_ids
        )
        numbers = yield ("special_weave_v1", {
            "ring_strength": float(values.get("MagBase", 0.0)),
            "lingke_passive": lingke_passive, "source_damage": triggering_hit.damage,
            "observed": hit.damage,
        })
        ring_strength = numbers["ring_strength"]
        strength_multiplier = numbers["strength_multiplier"]
        base_extra_ratio = numbers["extra_ratio"]
        followup_multiplier = numbers["followup_multiplier"]
        predicted = numbers["selected"]
        signed_error, absolute_error = numbers["signed_error"], numbers["absolute_error"]
        confidence = (
            "高" if absolute_error is not None and absolute_error <= 2.0
            else "中" if absolute_error is not None and absolute_error <= 5.0
            else "低"
        )
        unresolved = sum(row.status == "unresolved" for row in projection.decisions)
        missing = (
            ()
            if unresolved == 0
            else (f"헥스 관련 Buff {unresolved}개가 아직 구조화되지 않았습니다",)
        )
        factors = (
            _factor(
                "recorded_direct_damage",
                "원본 피해 실제값",
                triggering_hit.damage,
                f"정식 히트 {triggering_hit.event_id}",
                "같은 이벤트에 기록된 유효 원본 피해를 직접 사용",
            ),
            _factor(
                "weave_strength",
                "헥스 사이클 강도 구간",
                strength_multiplier,
                f"원본 피해 출처 캐릭터 사이클 강도 {ring_strength:g}",
                "1 + 20% × 사이클 강도 / (사이클 강도 + 180)",
            ),
            _factor(
                "weave_followup",
                "헥스 추가 배율",
                followup_multiplier,
                (
                    "링코 돌파 패시브「약점 감지」해금됨"
                    if lingke_passive
                    else "기본 헥스 규칙"
                ),
                f"(1 + {base_extra_ratio:.0%}) × 헥스 사이클 강도 구간 - 1",
            ),
        )
        return BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=predicted,
            critical_damage=None,
            selected_damage=predicted,
            selected_error_percent=absolute_error,
            critical_state="not_applicable",
            confidence=confidence,
            factors=factors,
            missing_evidence=missing,
            formula_type=formula_label,
            critical_rate=0.0,
            expected_damage=predicted,
            corrected_expected_damage=(hit.damage if predicted > 0.0 else None),
            signed_error_percent=signed_error,
            critical_policy="disabled",
        )

    @staticmethod
    def _reaction_attribute(
        hit: BattleAnalysisHit,
        analysis: BattleAnalysisSnapshot,
        evidence_attribute: str = "",
    ) -> str:
        evidence_value = str(evidence_attribute).casefold()
        if evidence_value in _ELEMENT_PENETRATION_PROPERTIES:
            return evidence_value
        attribute = str(hit.damage_attribute).casefold()
        if attribute in _ELEMENT_PENETRATION_PROPERTIES:
            return attribute
        counts: dict[str, int] = {}
        for row in analysis.hits:
            candidate = str(row.damage_attribute).casefold()
            if (
                row.character_id == hit.character_id
                and row.direction == "outgoing"
                and candidate in _ELEMENT_PENETRATION_PROPERTIES
            ):
                counts[candidate] = counts.get(candidate, 0) + 1
        if counts:
            return max(counts, key=lambda key: (counts[key], key))
        return attribute if attribute else "normal"

    @staticmethod
    def _mitigation_context(
        *,
        hit: BattleAnalysisHit,
        projection: BattleHitBuffProjection,
        values: Mapping[str, float],
        analysis: BattleAnalysisSnapshot,
        evidence_attribute: str = "",
    ) -> tuple[str, dict, str, tuple[BattleHitReplayTerm, ...]]:
        condition = analysis.target_condition
        assert condition is not None
        attribute = BattleSpecialHitReplayService._reaction_attribute(
            hit,
            analysis,
            evidence_attribute,
        )
        defense_penetration = max(0.0, float(values.get("DefIgnore", 0.0)))
        defense_basis = (
            "사용자 시나리오·표시 레벨 근사" if condition.enemy_defense_base is None
            else "대상 속성 팩 DefBase/6"
        )
        baseline = next(
            (
                row
                for row in analysis.baselines
                if row.character_id == hit.character_id
            ),
            None,
        )
        character_level = 80.0 if baseline is None else baseline.character_level
        defense_terms = (
            ()
            if condition.enemy_defense_base is None
            else (
                literal_replay_term(
                    "character:level", "CharacterLevel", "캐릭터 레벨",
                    character_level, "character", "캐릭터",
                    is_percent=False, basis="고정 캐릭터 레벨",
                ),
                literal_replay_term(
                    "target:DefBase", "DefBase", "DefBase",
                    condition.enemy_defense_base, "target", "적",
                    is_percent=False, basis=defense_basis,
                ),
                literal_replay_term(
                    "target:DefUp", "DefUp", "방어 증가",
                    condition.enemy_defense_up, "target", "적",
                    is_percent=True, basis=defense_basis,
                ),
                literal_replay_term(
                    "target:DefAdd", "DefAdd", "추가 방어",
                    condition.enemy_defense_add, "target", "적",
                    is_percent=False, basis=defense_basis,
                ),
                literal_replay_term(
                    "attacker:DefIgnore", "DefIgnore", "방어 관통",
                    defense_penetration, "resolved", "공격자",
                    is_percent=True, basis="히트 시 캐릭터 속성",
                ),
                literal_replay_term(
                    "target:DefReduction", "DefReduction", "방어 감소",
                    condition.defense_reduction, "target", "적",
                    is_percent=True, basis=defense_basis,
                ),
            )
        )
        inputs = mitigation_input(condition, character_level, values, projection,
                                  attribute, clamp_defense=True)
        if attribute not in _ELEMENT_PENETRATION_PROPERTIES:
            inputs["resistance_additions"] = []
            inputs["resistance_penetration"] = 0.0
        return attribute, inputs, defense_basis, defense_terms

    @classmethod
    def _replay_standard_reaction(
        cls,
        *,
        channel_id: str,
        hit: BattleAnalysisHit,
        evidence: BattleSkillDamageEvidence,
        projection: BattleHitBuffProjection,
        values: Mapping[str, float],
        analysis: BattleAnalysisSnapshot,
        formula_label: str,
    ) -> BattleHitReplayResult:
        ordinary_scorch = (
            channel_id == "reaction_scorch"
            and evidence.damage_id.casefold() == _ORDINARY_SCORCH_DAMAGE_ID
        )
        zankou_scorch = (
            channel_id == "reaction_scorch"
            and evidence.damage_id.casefold() == _ZANKOU_SCORCH_DAMAGE_ID
        )
        if zankou_scorch and evidence.state_multiplier <= 0.0:
            return BattleHitReplayResult(
                event_id=hit.event_id,
                observed_damage=hit.damage,
                non_critical_damage=None,
                critical_damage=None,
                selected_damage=None,
                selected_error_percent=None,
                critical_state="unreplayable",
                confidence="未解析",
                factors=(),
                missing_evidence=(
                    "잔홍 패시브의 중첩별 지속 피해 부여 이벤트와 발동 시점의 스코치 피해·"
                    "원소·지속 시간 스냅샷이 없습니다; 주기 정산 hit는 부여 이벤트를 대체할 수 없습니다",
                ),
                formula_type=formula_label,
                formula_damage_attribute=hit.damage_attribute,
            )
        if ordinary_scorch and (
            hit.damage_attribute.casefold() not in _ELEMENT_PENETRATION_PROPERTIES
        ):
            return BattleHitReplayResult(
                event_id=hit.event_id,
                observed_damage=hit.damage,
                non_critical_damage=None,
                critical_damage=None,
                selected_damage=None,
                selected_error_percent=None,
                critical_state="unreplayable",
                confidence="未解析",
                factors=(),
                missing_evidence=(
                    "일반 스코치 정식 피해 항목은 원소 속성이 고정되어 있지 않고, 이번 히트도 확인 가능한 피해 속성을 제공하지 않았습니다;"
                    "대상 저항과 캐릭터 관통을 추측할 수 없습니다",
                ),
                formula_type=formula_label,
                formula_damage_attribute="",
            )
        level_multiplier = evidence.level_multiplier
        if level_multiplier is None:
            return BattleHitReplayResult(
                event_id=hit.event_id,
                observed_damage=hit.damage,
                non_critical_damage=None,
                critical_damage=None,
                selected_damage=None,
                selected_error_percent=None,
                critical_state="unreplayable",
                confidence="未解析",
                factors=(),
                missing_evidence=(f"{formula_label}의 공식 16단계 레벨 기초값이 없습니다",),
                formula_type=formula_label,
            )
        attribute, mitigation, defense_basis, defense_terms = cls._mitigation_context(
            hit=hit, projection=projection, values=values, analysis=analysis,
            evidence_attribute=evidence.damage_attribute,
        )
        numbers = yield ("special_reaction_v1", {
            "observed": hit.damage, "level_multiplier": level_multiplier,
            "ring_strength": float(values.get("MagBase", 0.0)),
            "mitigation": mitigation, "scorch": channel_id == "reaction_scorch",
            "state_multiplier": evidence.state_multiplier,
            "dot_final_multiplier": evidence.dot_final_multiplier,
            "crit_damage": float(values.get("CritDamageBase", 0.50)),
        })
        ring_strength, ring_multiplier = numbers["ring_strength"], numbers["ring_multiplier"]
        defense, resistance, vulnerability = (numbers[key] for key in
                                              ("defense", "resistance", "vulnerability"))
        stack_multiplier, crit_damage = numbers["stack_multiplier"], numbers["crit_damage"]
        non_critical, critical, selected = (numbers[key] for key in
                                           ("noncritical", "critical", "selected"))
        critical_state, critical_rate = numbers["critical_state"], numbers["critical_rate"]
        expected = numbers["expected"]
        signed_error, absolute_error = numbers["signed_error"], numbers["absolute_error"]
        confidence = (
            "高" if absolute_error is not None and absolute_error <= 2.0
            else "中" if absolute_error is not None and absolute_error <= 5.0
            else "低"
        )
        unresolved = sum(row.status == "unresolved" for row in projection.decisions)
        missing = []
        if unresolved:
            missing.append(f"{formula_label} 관련 Buff {unresolved}개가 아직 구조화되지 않았습니다")
        if zankou_scorch and evidence.state_multiplier_label:
            missing.append(
                f"스코치 중첩 수는 히트별 순방향 리플레이로 산출 (신뢰도 {evidence.state_confidence}),"
                "런타임 대상 Buff 중첩 수로 덮어쓰기 대기"
            )
        condition = analysis.target_condition
        assert condition is not None
        stack_factors = (
            ()
            if channel_id != "reaction_scorch" or not evidence.state_multiplier_label
            else (_factor(
                "state_coefficient",
                evidence.state_multiplier_label,
                stack_multiplier,
                evidence.state_multiplier_basis,
                (
                    "min(정산 전 잔홍 스코치 중첩 수, 3) × 단일 중첩 피해"
                    if zankou_scorch else "일반 스코치 고정 1중첩 × 단일 중첩 피해"
                ),
            ),)
        )
        factors = (
            _factor(
                "skill",
                "레벨 기초값",
                level_multiplier,
                evidence.evidence_basis,
                "공식 사이클 피해 곡선에서 캐릭터 레벨에 따라 단계 선택",
            ),
            *stack_factors,
            _factor(
                "scaling",
                "사이클 강도 구간",
                ring_multiplier,
                f"히트 귀속 캐릭터 사이클 강도 {ring_strength:g}",
                "1 + 사이클 강도 / 600",
            ),
            _factor(
                "defense",
                "방어 구간",
                defense,
                defense_basis,
                "캐릭터 레벨과 적 방어력",
                terms=defense_terms,
            ),
            _factor(
                "resistance",
                "저항 구간",
                resistance,
                f"{attribute} 속성 저항과 관통",
                "저항 구간별 함수(대상 저항 - 속성 관통)",
            ),
            _factor(
                "vulnerability",
                "취약 구간",
                vulnerability,
                "적 받는 피해 증가",
                "1 + 취약",
            ),
            *(
                dot_final_replay_factors(evidence)
                if channel_id == "reaction_scorch" else ()
            ),
            _factor(
                "critical",
                "치명 피해 배율",
                1.0 + crit_damage if channel_id == "reaction_scorch" else 1.0,
                "스코치 고정 50% 치명 확률" if channel_id == "reaction_scorch" else "블라썸은 치명타 없음",
                "1 + 치명 피해" if channel_id == "reaction_scorch" else "1로 고정",
            ),
        )
        return BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=non_critical,
            critical_damage=critical,
            selected_damage=selected,
            selected_error_percent=absolute_error,
            critical_state=critical_state,
            confidence=confidence,
            factors=factors,
            missing_evidence=tuple(missing),
            formula_type=formula_label,
            critical_rate=critical_rate,
            expected_damage=expected,
            corrected_expected_damage=(
                numbers["corrected_expected"]
            ),
            signed_error_percent=signed_error,
            critical_policy=(
                "fixed" if channel_id == "reaction_scorch" else "disabled"
            ),
            formula_damage_attribute=attribute,
        )

    @staticmethod
    def _replay_dark_star(
        *,
        hit: BattleAnalysisHit,
        evidence: BattleSkillDamageEvidence,
        projection: BattleHitBuffProjection,
        values: Mapping[str, float],
        analysis: BattleAnalysisSnapshot,
        formula_label: str,
    ) -> BattleHitReplayResult:
        condition = analysis.target_condition
        assert condition is not None
        level_multiplier = evidence.level_multiplier
        if level_multiplier is None:
            return BattleHitReplayResult(
                event_id=hit.event_id,
                observed_damage=hit.damage,
                non_critical_damage=None,
                critical_damage=None,
                selected_damage=None,
                selected_error_percent=None,
                critical_state="unreplayable",
                confidence="未解析",
                factors=(),
                missing_evidence=("노바의 공식 16단계 레벨 기초값이 없습니다",),
                formula_type=formula_label,
            )
        numbers = yield ("special_nova_v1", {
            "observed": hit.damage, "level_multiplier": level_multiplier,
            "ring_strength": float(values.get("MagBase", 0.0)),
            "mitigation": mitigation_input(condition, 80.0, values, projection,
                                           "psychically", clamp_defense=True),
        })
        ring_strength, ring_multiplier = numbers["ring_strength"], numbers["ring_multiplier"]
        resistance, vulnerability = numbers["resistance"], numbers["vulnerability"]
        predicted = numbers["selected"]
        signed_error, absolute_error = numbers["signed_error"], numbers["absolute_error"]
        confidence = (
            "高" if absolute_error is not None and absolute_error <= 2.0
            else "中" if absolute_error is not None and absolute_error <= 5.0
            else "低"
        )
        unresolved = sum(
            row.status == "unresolved" for row in projection.decisions
        )
        missing = (
            ()
            if unresolved == 0
            else (f"노바 관련 Buff {unresolved}개가 아직 구조화되지 않았습니다",)
        )
        factors = (
            _factor(
                "skill",
                "레벨 기초값",
                level_multiplier,
                evidence.evidence_basis,
                "공식 사이클 피해 곡선에서 캐릭터 레벨에 따라 단계 선택",
            ),
            _factor(
                "scaling",
                "사이클 강도 구간",
                ring_multiplier,
                f"히트 귀속 캐릭터 사이클 강도 {ring_strength:g}",
                "1 + 사이클 강도 / 600",
            ),
            _factor("damage_up", "피해 증가 구간", 1.0, "노바는 캐릭터 통용 피해 증강을 읽지 않음", "1로 고정"),
            _factor("defense", "방어 구간", 1.0, "노바는 정신 피해", "1로 고정"),
            _factor(
                "resistance",
                "저항 구간",
                resistance,
                "사용자가 확인한 정신 저항 및 히트 시 저항 감소/관통",
                "저항 구간 함수(정신 저항 - 정신 관통)",
            ),
            _factor(
                "vulnerability",
                "취약 구간",
                vulnerability,
                "사용자가 확인한 대상 조건 및 적 Buff",
                "1 + 적 받는 피해 증가",
            ),
            _factor("independent", "독립 최종 곱연산 구간", 1.0, "현재 노바 독립 보정 없음", "1로 고정"),
        )
        return BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=predicted,
            critical_damage=None,
            selected_damage=predicted,
            selected_error_percent=absolute_error,
            critical_state="not_applicable",
            confidence=confidence,
            factors=factors,
            missing_evidence=missing,
            formula_type=formula_label,
            critical_rate=0.0,
            expected_damage=predicted,
            corrected_expected_damage=(hit.damage if predicted > 0.0 else None),
            signed_error_percent=signed_error,
            critical_policy="disabled",
        )
