# 只移除真实逐击已用正式 Ability 或 GE 明确标记的创生派生被动伤害。
"""Conservative fixed-axis counterfactuals for explicit creation-passive hits."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.battle_buff_counterfactual import (
    BattleBuffBeneficiaryResult,
    BattleBuffCounterfactualResult,
    BattleDamageCoverage,
)
from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
    BattleQuantificationGap,
)
from src.domain.battle_report import BattleAnalysisHit, BattleAnalysisSnapshot


CREATION_PASSIVE_COUNTERFACTUAL_MODEL_VERSION = (
    "battle-creation-passive-counterfactual-v1"
)


@dataclass(frozen=True, slots=True)
class _ExplicitPassiveRule:
    passive_id: str
    passive_name: str
    source_character_id: int
    source_character_name: str
    ability_ids: frozenset[str]
    gameplay_effect_ids: frozenset[str]
    downstream_gap_code: str
    downstream_gap_explanation: str


_RULES = (
    _ExplicitPassiveRule(
        passive_id="PASSIVE-1010-GA_Nanally_Passive_2",
        passive_name="绝对「公正」的决斗",
        source_character_id=1010,
        source_character_name="娜娜莉",
        ability_ids=frozenset({"ga_nanally_passive_2"}),
        gameplay_effect_ids=frozenset({"ge_nanally010_lv1_damage"}),
        downstream_gap_code="nanally_passive_downstream_unresolved",
        downstream_gap_explanation=(
            "입증된 추가 공격은 실제 축에서 삭제할 수 있습니다; 다만 그 에너지 회복·후속 동작 가용성 등,"
            "히트 시 발동하는 나머지 연동은 아직 리플레이하지 않았으므로 여기서는 직접 피해 하한만 제시합니다."
        ),
    ),
    _ExplicitPassiveRule(
        passive_id="PASSIVE-1055-GA_Kuhara_Passive_2",
        passive_name="风声为我所用",
        source_character_id=1055,
        source_character_name="九原",
        ability_ids=frozenset({"ga_kuhara_passive_2"}),
        gameplay_effect_ids=frozenset({"ge_player_kuhara_seedreaction_damage"}),
        downstream_gap_code="kuhara_rose_downstream_unresolved",
        downstream_gap_explanation=(
            "입증된 15% 추가 정산은 실제 축에서 삭제할 수 있습니다; 최초 계약 체결·대상별 장미 계약 상태·"
            "대상 HP 진행 및 후속 발동 연동은 아직 전체 반사실 리플레이를 거치지 않았습니다."
        ),
    ),
    _ExplicitPassiveRule(
        passive_id="PASSIVE-1075-GA_Oneiroi_Passive_1",
        passive_name="镜象",
        source_character_id=1075,
        source_character_name="伊洛伊",
        ability_ids=frozenset({"ga_oneiroi_passive_1"}),
        gameplay_effect_ids=frozenset({"ge_actorreaction_1_1019_damage"}),
        downstream_gap_code="oneiroi_replica_downstream_unresolved",
        downstream_gap_explanation=(
            "입증된 복제 블라썸 꽃은 실제 축에서 삭제할 수 있습니다; 복제 개체 생명 주기·시간 정지 진행·"
            "대상 선택 및 복제 꽃이 발동하는 구원 추가 정산 등 하위 연동은 아직 이벤트 체인이 구축되지 않았습니다."
        ),
    ),
)


def _matches(rule: _ExplicitPassiveRule, hit: BattleAnalysisHit) -> bool:
    ability_id = str(hit.ability_id or "").casefold()
    gameplay_effect_id = str(hit.gameplay_effect_id or "").casefold()
    return (
        ability_id in rule.ability_ids
        or gameplay_effect_id in rule.gameplay_effect_ids
    )


def _source_name(
    analysis: BattleAnalysisSnapshot,
    rule: _ExplicitPassiveRule,
) -> str:
    return next(
        (
            row.character_name
            for row in analysis.baselines
            if row.character_id == rule.source_character_id
        ),
        rule.source_character_name,
    )


class BattleCreationPassiveCounterfactualService:
    """Remove only explicit passive-owned events from the frozen real axis."""

    @classmethod
    def calculate(
        cls,
        analysis: BattleAnalysisSnapshot,
    ) -> tuple[BattleBuffCounterfactualResult, ...]:
        outgoing_hits = tuple(
            hit
            for hit in analysis.hits
            if hit.direction == "outgoing" and hit.damage > 0.0
        )
        baseline_hit_damage = sum(float(hit.damage) for hit in outgoing_hits)
        baseline_damage = (
            float(analysis.effective_damage)
            if analysis.effective_damage > 0.0
            else baseline_hit_damage
        )
        enabled_passive_ids = {
            passive_id
            for baseline in analysis.baselines
            for passive_id in baseline.enabled_team_passive_ids
        }
        results = []
        for rule in _RULES:
            matched = tuple(hit for hit in outgoing_hits if _matches(rule, hit))
            if not matched:
                if rule.passive_id in enabled_passive_ids:
                    results.append(cls._without_observed_event(
                        analysis,
                        rule,
                        baseline_damage=baseline_damage,
                        baseline_hit_damage=baseline_hit_damage,
                    ))
                continue
            direct_gain = sum(float(hit.damage) for hit in matched)
            beneficiaries = cls._beneficiaries(
                outgoing_hits,
                matched,
                baseline_damage=baseline_damage,
                gap_code=rule.downstream_gap_code,
                gap_explanation=rule.downstream_gap_explanation,
            )
            without_direct = max(0.0, baseline_damage - direct_gain)
            attributed_gain = sum(
                row.quantified_damage_gain or 0.0 for row in beneficiaries
            )
            unattributed_gain = max(0.0, direct_gain - attributed_gain)
            gap = BattleQuantificationGap(
                code=rule.downstream_gap_code,
                dimension_id=f"{rule.passive_id}:downstream",
                dependency_scope="mechanic_specific",
                property_ids=(),
                explanation=rule.downstream_gap_explanation,
            )
            passive_key = (
                f"character_passive:{rule.source_character_id}:"
                f"{rule.passive_id.split('-', 2)[-1]}"
            )
            quantification = BattleDamageQuantification.from_buckets(
                status="partial",
                partially_quantified_damage=direct_gain,
                unavailable_damage=max(0.0, baseline_damage - direct_gain),
                quantified_increment=direct_gain,
                gaps=(gap,),
            )
            results.append(BattleBuffCounterfactualResult(
                buff_key=passive_key,
                source_character_id=rule.source_character_id,
                source_character_name=_source_name(analysis, rule),
                buff_name=rule.passive_name,
                buff_asset_path=f"confirmed:{rule.passive_id}",
                source_effect_definition_id=passive_key,
                target_scope="team",
                interval_count=0,
                coverage_seconds=0.0,
                affected_hits=len(matched),
                quantified_hits=len(matched),
                baseline_damage=baseline_damage,
                without_quantified_effect_damage=without_direct,
                quantified_damage_gain=direct_gain,
                quantified_gain_percent=(
                    direct_gain / without_direct * 100.0
                    if without_direct > 0.0
                    else 0.0
                ),
                without_buff_damage=None,
                damage_gain=None,
                gain_percent=None,
                confidence="高",
                method="explicit_fixed_axis_event_removal",
                explanation=(
                    "실제 고정 축에서 정식 Ability/GE로 이 패시브 소속임이 명확히 입증된"
                    f" {len(matched)}개 히트만 삭제합니다; 직접 피해는 정량화되었고, 하위 상태와 파생"
                    "연동은 미정량화 상태로 유지됩니다."
                ),
                quantification=quantification,
                beneficiaries=beneficiaries,
                quantified_unattributed_damage_gain=unattributed_gain,
                unattributed_damage_gain=None,
                evidence_event_ids=tuple(hit.event_id for hit in matched),
                damage_coverage=BattleDamageCoverage(
                    basis_damage=baseline_hit_damage,
                    covered_damage=min(baseline_hit_damage, direct_gain),
                ),
            ))
        return tuple(results)

    @staticmethod
    def _without_observed_event(
        analysis: BattleAnalysisSnapshot,
        rule: _ExplicitPassiveRule,
        *,
        baseline_damage: float,
        baseline_hit_damage: float,
    ) -> BattleBuffCounterfactualResult:
        passive_key = (
            f"character_passive:{rule.source_character_id}:"
            f"{rule.passive_id.split('-', 2)[-1]}"
        )
        if analysis.axis_complete:
            quantification = BattleDamageQuantification.from_buckets(
                status="not_applicable",
                proven_unchanged_damage=baseline_damage,
                quantified_increment=0.0,
            )
            without_damage = baseline_damage
            gain = 0.0
            confidence = "高"
            method = "complete_axis_without_explicit_passive_event"
            explanation = (
                "이 패시브는 해금되었지만, 완전한 고정 축에는 정식 Ability/GE로 표시된 파생 히트가 없습니다;"
                "이 구간의 직접 메커니즘 피해는 0입니다."
            )
        else:
            gap = BattleQuantificationGap(
                code="explicit_passive_hit_axis_incomplete",
                dimension_id=f"{rule.passive_id}:event_axis",
                dependency_scope="mechanic_specific",
                property_ids=(),
                explanation=(
                    "히트 축이 불완전하므로 정식 패시브 히트가 관측되지 않았다고 해서 이득이 0임을 증명할 수는 없습니다."
                ),
            )
            quantification = BattleDamageQuantification.from_buckets(
                status="unavailable",
                unavailable_damage=baseline_damage,
                gaps=(gap,),
            )
            without_damage = None
            gain = None
            confidence = "低"
            method = "explicit_passive_hit_axis_incomplete"
            explanation = gap.explanation
        return BattleBuffCounterfactualResult(
            buff_key=passive_key,
            source_character_id=rule.source_character_id,
            source_character_name=_source_name(analysis, rule),
            buff_name=rule.passive_name,
            buff_asset_path=f"confirmed:{rule.passive_id}",
            source_effect_definition_id=passive_key,
            target_scope="team",
            interval_count=0,
            coverage_seconds=0.0,
            affected_hits=0,
            quantified_hits=0,
            baseline_damage=baseline_damage,
            without_quantified_effect_damage=without_damage,
            quantified_damage_gain=gain,
            quantified_gain_percent=gain,
            without_buff_damage=without_damage,
            damage_gain=gain,
            gain_percent=gain,
            confidence=confidence,
            method=method,
            explanation=explanation,
            quantification=quantification,
            quantified_unattributed_damage_gain=gain,
            unattributed_damage_gain=gain,
            damage_coverage=BattleDamageCoverage(
                basis_damage=baseline_hit_damage,
            ),
        )

    @staticmethod
    def _beneficiaries(
        outgoing_hits: tuple[BattleAnalysisHit, ...],
        matched_hits: tuple[BattleAnalysisHit, ...],
        *,
        baseline_damage: float,
        gap_code: str,
        gap_explanation: str,
    ) -> tuple[BattleBuffBeneficiaryResult, ...]:
        provider_keys = tuple(sorted(
            {
                (hit.character_id, hit.character_name)
                for hit in matched_hits
                if hit.character_id is not None and hit.character_id > 0
            },
            key=lambda item: (item[0], item[1]),
        ))
        team_without = max(
            0.0,
            baseline_damage - sum(float(hit.damage) for hit in matched_hits),
        )
        return tuple(
            BattleCreationPassiveCounterfactualService._beneficiary(
                outgoing_hits=outgoing_hits,
                matched_hits=matched_hits,
                provider_character_id=provider_character_id,
                provider_character_name=provider_character_name,
                team_without_damage=team_without,
                gap_code=gap_code,
                gap_explanation=gap_explanation,
            )
            for provider_character_id, provider_character_name in provider_keys
        )

    @staticmethod
    def _beneficiary(
        *,
        outgoing_hits: tuple[BattleAnalysisHit, ...],
        matched_hits: tuple[BattleAnalysisHit, ...],
        provider_character_id: int,
        provider_character_name: str,
        team_without_damage: float,
        gap_code: str,
        gap_explanation: str,
    ) -> BattleBuffBeneficiaryResult:
        provider_hits = tuple(
            hit
            for hit in outgoing_hits
            if hit.character_id == provider_character_id
            and hit.character_name == provider_character_name
        )
        removed_hits = tuple(
            hit
            for hit in matched_hits
            if hit.character_id == provider_character_id
            and hit.character_name == provider_character_name
        )
        provider_baseline = sum(float(hit.damage) for hit in provider_hits)
        direct_gain = sum(float(hit.damage) for hit in removed_hits)
        provider_without = max(0.0, provider_baseline - direct_gain)
        gap = BattleQuantificationGap(
            code=gap_code,
            dimension_id=f"provider:{provider_character_id}:downstream",
            dependency_scope="mechanic_specific",
            property_ids=(),
            explanation=gap_explanation,
        )
        quantification = BattleDamageQuantification.from_buckets(
            status="partial",
            partially_quantified_damage=direct_gain,
            unavailable_damage=max(0.0, provider_baseline - direct_gain),
            quantified_increment=direct_gain,
            gaps=(gap,),
        )
        return BattleBuffBeneficiaryResult(
            character_id=provider_character_id,
            character_name=provider_character_name or "귀속 불가",
            affected_hits=len(removed_hits),
            quantified_hits=len(removed_hits),
            baseline_damage=provider_baseline,
            without_quantified_effect_damage=provider_without,
            quantified_damage_gain=direct_gain,
            quantified_recipient_gain_percent=(
                direct_gain / provider_without * 100.0
                if provider_without > 0.0
                else 0.0
            ),
            quantified_team_contribution_percent=(
                direct_gain / team_without_damage * 100.0
                if team_without_damage > 0.0
                else 0.0
            ),
            without_buff_damage=None,
            damage_gain=None,
            recipient_gain_percent=None,
            team_contribution_percent=None,
            quantification=quantification,
            damage_coverage=BattleDamageCoverage(
                basis_damage=provider_baseline,
                covered_damage=min(provider_baseline, direct_gain),
            ),
        )


__all__ = [
    "BattleCreationPassiveCounterfactualService",
    "CREATION_PASSIVE_COUNTERFACTUAL_MODEL_VERSION",
]
