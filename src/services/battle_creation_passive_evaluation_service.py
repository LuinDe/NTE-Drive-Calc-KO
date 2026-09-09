# 以现有 Buff 反事实契约展示创生生命周期、空间与资源被动。
"""Conservative fixed-axis evaluations for creation lifecycle passives."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.battle_buff_counterfactual import (
    BattleBuffCounterfactualResult,
    BattleDamageCoverage,
)
from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
    BattleQuantificationGap,
)
from src.domain.battle_report import BattleAnalysisHit, BattleAnalysisSnapshot
from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService,
    EnabledCharacterPassive,
)
from src.services.battle_creation_passive_beneficiary_service import (
    BattleCreationPassiveBeneficiaryService,
)
from src.services.battle_creation_passive_result_support import (
    build_creation_passive_result,
)


CREATION_PASSIVE_EVALUATION_VERSION = "battle-creation-passive-evaluation-v4"

_SUPPORTED_ADAPTERS = frozenset({
    "creation-volley",
    "creation-radius",
    "creation-time-stop",
    "creation-cap",
    "edgar-charge-reaction",
})


def _hit_damage_coverage(
    all_hits: Sequence[BattleAnalysisHit],
    covered_hits: Sequence[BattleAnalysisHit],
    unresolved_hits: Sequence[BattleAnalysisHit] = (),
) -> BattleDamageCoverage:
    basis = sum(
        max(0.0, float(hit.damage))
        for hit in all_hits
        if hit.direction == "outgoing"
    )
    covered_ids = {hit.event_id for hit in covered_hits}
    covered = min(basis, sum(
        max(0.0, float(hit.damage)) for hit in covered_hits
    ))
    unresolved = min(
        max(0.0, basis - covered),
        sum(
            max(0.0, float(hit.damage))
            for hit in unresolved_hits
            if hit.event_id not in covered_ids
        ),
    )
    return BattleDamageCoverage(basis, covered, unresolved)


_CREATION_EFFECT_IDS = frozenset({
    "ge_actorreaction_1_damage",
    "ge_actorreaction_1_1019_damage",
})
_CREATION_LABELS = frozenset({
    "创生",
    "创生花",
    "blossom damage",
    "replica vita pistil",
})


@dataclass(frozen=True, slots=True)
class BattleCreationPassiveAttribution:
    """Events a dedicated lifecycle model proved would disappear."""

    adapter_id: str
    event_ids: tuple[str, ...]
    complete: bool
    evidence_basis: str

    def __post_init__(self) -> None:
        if self.adapter_id not in _SUPPORTED_ADAPTERS:
            raise ValueError(f"unsupported creation passive adapter: {self.adapter_id}")
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("event_ids must be unique")
        if not self.evidence_basis.strip():
            raise ValueError("evidence_basis is required")


@dataclass(frozen=True, slots=True)
class BattleCreationPassiveEvidence:
    """Capabilities frozen before evaluating lifecycle counterfactuals."""

    single_target_confirmed: bool = False
    time_stop_axis_complete: bool = False
    target_positions_complete: bool = False
    plant_identity_complete: bool = False
    volley_identity_complete: bool = False
    lifecycle_complete: bool = False
    creation_cap_order_complete: bool = False
    future_action_axis_complete: bool = False
    edgar_trigger_axis_complete: bool = False
    edgar_trigger_event_ids: tuple[str, ...] = ()
    attributions: tuple[BattleCreationPassiveAttribution, ...] = ()

    def __post_init__(self) -> None:
        adapter_ids = tuple(row.adapter_id for row in self.attributions)
        if len(set(adapter_ids)) != len(adapter_ids):
            raise ValueError("one attribution is allowed per adapter_id")
        if len(set(self.edgar_trigger_event_ids)) != len(
            self.edgar_trigger_event_ids
        ):
            raise ValueError("edgar_trigger_event_ids must be unique")


@dataclass(frozen=True, slots=True)
class _MechanicPolicy:
    gap_specs: tuple[tuple[str, str], ...]
    unavailable_explanation: str


_POLICIES = {
    "creation-volley": _MechanicPolicy(
        gap_specs=(
            (
                "creation_plant_identity_missing",
                "블라썸 개체의 안정된 식별자가 없어 꽃 히트를 각 개체로 되돌릴 수 없습니다.",
            ),
            (
                "creation_volley_identity_missing",
                "개별 일제 발사 식별자가 없어 기본 5송이와 추가 5송이를 구분할 수 없습니다.",
            ),
            (
                "creation_lifecycle_missing",
                "생성·만료·덮어쓰기 순서가 없어 1초와 2초 발사 일정을 다시 계산할 수 없습니다.",
            ),
        ),
        unavailable_explanation=(
            "나나리 P1은 팀 블라썸의 꽃 수와 발사 간격을 바꿉니다; 정식 블라썸 GE가 없으면,"
            "표시 라벨만으로 식별한 피해를 절반 근사에 포함할 수 없습니다."
        ),
    ),
    "creation-radius": _MechanicPolicy(
        gap_specs=((
            "target_position_missing",
            "꽃 히트 지점·기본 범위·각 대상 위치가 없어 범위 +400으로 새로 생긴 히트를 식별할 수 없습니다.",
        ),),
        unavailable_explanation=(
            "민트 P1은 블라썸 꽃 범위만 넓히고 송이당 배율은 바꾸지 않습니다;"
            "다중 대상에서 위치가 없으면 이득을 정량화할 수 없습니다."
        ),
    ),
    "creation-time-stop": _MechanicPolicy(
        gap_specs=(
            (
                "time_stop_axis_missing",
                "완전한 시간 정지 구간 축이 없어 패시브의 실제 적용 창을 확인할 수 없습니다.",
            ),
            (
                "creation_plant_identity_missing",
                "개체 식별자가 없어 시간 정지 중 히트와 같은 개체의 후속 히트를 연결할 수 없습니다.",
            ),
            (
                "creation_lifecycle_missing",
                "시간 정지 중 남은 수명·발사 일정·덮어쓰기 순서가 없습니다.",
            ),
            (
                "future_action_axis_missing",
                "시간 정지 중 공격 지속을 제거하면 후속 발사와 새 개체 덮어쓰기가 바뀌는데, 미래 동작 축이 없습니다.",
            ),
        ),
        unavailable_explanation=(
            "호토리 P1은 시간 정지 중 개체의 생명 주기를 바꿉니다; 관측된 시간 정지 중 히트는,"
            "패시브 제거 후 반드시 사라지는 총피해와 같지 않습니다."
        ),
    ),
    "creation-cap": _MechanicPolicy(
        gap_specs=(
            (
                "creation_plant_identity_missing",
                "원본 개체와 구원 P1 추가 개체의 안정된 식별자가 없습니다.",
            ),
            (
                "creation_cap_order_missing",
                "3개체/6개체 상한에서의 생성·덮어쓰기 순서가 없습니다.",
            ),
            (
                "creation_lifecycle_missing",
                "개체별 남은 수명과 실제 발사 횟수가 없습니다.",
            ),
        ),
        unavailable_explanation=(
            "구원 P1은 개체를 추가하는 동시에 필드 상한도 바꿉니다; 개체 식별자가 없으면,"
            "전체 블라썸 피해를 단순히 2배 하거나 2로 나눌 수 없습니다."
        ),
    ),
    "edgar-charge-reaction": _MechanicPolicy(
        gap_specs=(
            (
                "charge_trigger_axis_missing",
                "지원 스킬이 발동하는 차지와 30초 재사용 대기시간의 정식 자원 이벤트 축이 없습니다.",
            ),
            (
                "creation_energy_suppression_missing",
                "재사용 대기 중 꽃이 둔화된 대상에 명중할 때 억제되는 일반 차지 에너지 회복 정보가 없습니다.",
            ),
            (
                "charge_efficiency_missing",
                "실제로 에너지를 얻은 캐릭터의 발동 시점 에너지 충전 효율이 없습니다.",
            ),
            (
                "future_action_axis_missing",
                "자원 차액이 후속 Q나 다른 피해 동작을 바꾸는지 확인할 수 없습니다.",
            ),
        ),
        unavailable_explanation=(
            "에드가 P1은 먼저 차지 에너지와 재사용 대기 중 꽃 히트 에너지 회복을 바꾸며,"
            "그다음에는 미래 동작까지 바꿀 수 있습니다; 자원 축이 없으면 기초 에너지 120을 피해로 환산할 수 없습니다."
        ),
    ),
}


def _is_creation_hit(hit: BattleAnalysisHit) -> bool:
    if hit.direction != "outgoing" or hit.damage <= 0.0:
        return False
    if str(hit.gameplay_effect_id or "").casefold() in _CREATION_EFFECT_IDS:
        return True
    labels = (
        str(hit.attack_type or "").strip().casefold(),
        str(hit.damage_name or "").strip().casefold(),
        str(hit.damage_component or "").strip().casefold(),
    )
    return any(label in _CREATION_LABELS for label in labels)


def _is_formal_creation_hit(hit: BattleAnalysisHit) -> bool:
    return (
        hit.direction == "outgoing"
        and hit.damage > 0.0
        and str(hit.gameplay_effect_id or "").casefold()
        in _CREATION_EFFECT_IDS
    )


def _gap(code: str, explanation: str, adapter_id: str) -> BattleQuantificationGap:
    return BattleQuantificationGap(
        code=code,
        dimension_id=f"creation_passive:{adapter_id}",
        dependency_scope="mechanic_specific",
        property_ids=(),
        explanation=explanation,
    )


class BattleCreationPassiveEvaluationService:
    """Return merge-ready Buff results without inventing lifecycle state."""

    @classmethod
    def calculate(
        cls,
        analysis: BattleAnalysisSnapshot,
        build: Mapping[str, Any] | None,
        *,
        evidence: BattleCreationPassiveEvidence | None = None,
    ) -> tuple[BattleBuffCounterfactualResult, ...]:
        frozen_evidence = evidence or BattleCreationPassiveEvidence()
        creation_hits = tuple(hit for hit in analysis.hits if _is_creation_hit(hit))
        attributions = {
            row.adapter_id: row for row in frozen_evidence.attributions
        }
        hit_damage = sum(
            max(0.0, float(hit.damage))
            for hit in analysis.hits
            if hit.direction == "outgoing"
        )
        team_damage = max(
            max(0.0, float(analysis.effective_damage)),
            hit_damage,
        )
        results = []
        for enabled in BattleCharacterPassiveService.enabled_passives(build):
            adapter_id = enabled.definition.adapter_id
            if adapter_id not in _SUPPORTED_ADAPTERS:
                continue
            results.append(cls._calculate_one(
                analysis=analysis,
                enabled=enabled,
                creation_hits=creation_hits,
                team_damage=team_damage,
                hit_damage=hit_damage,
                evidence=frozen_evidence,
                attribution=attributions.get(adapter_id),
            ))
        return tuple(results)

    @classmethod
    def _calculate_one(
        cls,
        *,
        analysis: BattleAnalysisSnapshot,
        enabled: EnabledCharacterPassive,
        creation_hits: tuple[BattleAnalysisHit, ...],
        team_damage: float,
        hit_damage: float,
        evidence: BattleCreationPassiveEvidence,
        attribution: BattleCreationPassiveAttribution | None,
    ) -> BattleBuffCounterfactualResult:
        adapter_id = enabled.definition.adapter_id
        if adapter_id == "edgar-charge-reaction":
            return cls._calculate_edgar(
                enabled,
                team_damage,
                hit_damage,
                evidence,
            )
        if not creation_hits and analysis.axis_complete:
            return cls._zero(
                enabled,
                team_damage,
                (),
                method="no_observed_creation_hits",
                explanation=(
                    "현재 고정 축에 블라썸 꽃의 적 대상 히트가 없으므로,"
                    "이 패시브를 제거해도 관측된 피해는 전혀 삭제되지 않습니다."
                ),
                coverage_basis_damage=hit_damage,
            )
        if not creation_hits:
            return cls._unavailable(
                enabled,
                team_damage,
                (),
                (_gap(
                    "creation_hit_axis_incomplete",
                    "히트 축이 불완전하므로 블라썸 꽃 히트가 관측되지 않았다고 해서 0으로 증명할 수 없습니다.",
                    adapter_id,
                ),),
                "현재 블라썸 히트가 관측되지 않았지만 히트 축이 불완전하므로, 누락된 이벤트를 0 이득으로 간주하지 않습니다.",
                coverage_basis_damage=hit_damage,
            )
        if adapter_id == "creation-radius" and evidence.single_target_confirmed:
            return cls._zero(
                enabled,
                team_damage,
                creation_hits,
                method="confirmed_single_target_spatial_zero",
                explanation=(
                    "단일 대상이 확인되었습니다; 범위 +400은 송이당 배율을 바꾸지 않으며,"
                    "같은 대상이 중복 히트되게 하지도 않으므로 고정 축 피해 이득은 0입니다."
                ),
                coverage_basis_damage=hit_damage,
            )
        has_time_stop = any(
            start is not None and end is not None and end > start
            for start, end in analysis.time_stop_intervals
        )
        if (
            adapter_id == "creation-time-stop"
            and evidence.time_stop_axis_complete
            and not has_time_stop
        ):
            return cls._zero(
                enabled,
                team_damage,
                creation_hits,
                method="no_time_stop_zero",
                explanation=(
                    "이번 전투에는 완전한 시간 정지 구간이 없으므로, 시간 정지 중 공격 지속 패시브의 현재 고정 축 피해는"
                    "0입니다."
                ),
                coverage_basis_damage=hit_damage,
            )
        if attribution is not None:
            return cls._from_attribution(
                enabled,
                creation_hits,
                analysis.hits,
                team_damage,
                hit_damage,
                attribution,
            )
        if adapter_id == "creation-volley":
            formal_hits = tuple(
                hit
                for hit in creation_hits
                if _is_formal_creation_hit(hit)
            )
            if formal_hits:
                return cls._nanally_half_approximation(
                    enabled,
                    creation_hits,
                    formal_hits,
                    analysis.hits,
                    team_damage,
                )
        policy = _POLICIES[adapter_id]
        gaps = cls._policy_gaps(adapter_id, policy, evidence)
        return cls._unavailable(
            enabled,
            team_damage,
            creation_hits,
            gaps,
            policy.unavailable_explanation,
            coverage_basis_damage=hit_damage,
        )

    @classmethod
    def _nanally_half_approximation(
        cls,
        enabled: EnabledCharacterPassive,
        creation_hits: tuple[BattleAnalysisHit, ...],
        formal_hits: tuple[BattleAnalysisHit, ...],
        all_hits: tuple[BattleAnalysisHit, ...],
        team_damage: float,
    ) -> BattleBuffCounterfactualResult:
        formal_creation_damage = sum(
            max(0.0, float(hit.damage)) for hit in formal_hits
        )
        observed_creation_damage = sum(
            max(0.0, float(hit.damage)) for hit in creation_hits
        )
        approximate_gain = formal_creation_damage * 0.5
        unavailable_damage = max(
            0.0,
            observed_creation_damage - formal_creation_damage,
        )
        proven_unchanged_damage = max(
            0.0,
            team_damage - observed_creation_damage,
        )
        gaps = (
            _gap(
                "nanally_fire_interval_unmodeled",
                "근사는 발사당 꽃 수가 5송이에서 10송이로 늘어나는 것만 처리하며, 2초에서 1초로의 빈도 변화는 리플레이하지 않았습니다.",
                enabled.definition.adapter_id,
            ),
            _gap(
                "nanally_creation_lifecycle_unmodeled",
                "개체·일제 발사·생성·만료·덮어쓰기 순서가 없어 정식 정밀 생명 주기 반사실을 구축할 수 없습니다.",
                enabled.definition.adapter_id,
            ),
            _gap(
                "nanally_unattributed_damage_unquantified",
                "표시 라벨만으로 식별되고 정식 블라썸 GE가 없는 히트는 미정량화 상태로 유지됩니다.",
                enabled.definition.adapter_id,
            ),
        )
        quantification = BattleDamageQuantification.from_buckets(
            status="partial",
            partially_quantified_damage=formal_creation_damage,
            unavailable_damage=unavailable_damage,
            proven_unchanged_damage=proven_unchanged_damage,
            quantified_increment=approximate_gain,
            gaps=gaps,
        )
        return cls._result(
            enabled,
            team_damage,
            formal_hits,
            tuple(hit for hit in creation_hits if hit not in formal_hits),
            all_hits,
            quantification,
            confidence="低",
            method="approximate_nanally_creation_count_halving",
            explanation=(
                "저신뢰 근사: 팀의 정식 GE_ActorReaction_1_Damage 및"
                " GE_ActorReaction_1_1019_Damage 히트에 꽃 수 2배 가정을 적용해,"
                "패시브가 있는 후보 피해의 절반을 패시브 없는 피해로 삼으므로,"
                "이득은 해당 부분 관측 피해의 50%로 기록됩니다. 이 결과는 2초에서 1초로의 빈도 변화·"
                "개체/일제 발사 생명 주기를 무시하므로 결코 정식 정밀 반사실이 아닙니다."
            ),
        )

    @staticmethod
    def _policy_gaps(
        adapter_id: str,
        policy: _MechanicPolicy,
        evidence: BattleCreationPassiveEvidence,
    ) -> tuple[BattleQuantificationGap, ...]:
        available = {
            "creation_plant_identity_missing": evidence.plant_identity_complete,
            "creation_volley_identity_missing": evidence.volley_identity_complete,
            "creation_lifecycle_missing": evidence.lifecycle_complete,
            "creation_cap_order_missing": evidence.creation_cap_order_complete,
            "future_action_axis_missing": evidence.future_action_axis_complete,
            "time_stop_axis_missing": evidence.time_stop_axis_complete,
            "target_position_missing": evidence.target_positions_complete,
        }
        gaps = tuple(
            _gap(code, explanation, adapter_id)
            for code, explanation in policy.gap_specs
            if not available.get(code, False)
        )
        if gaps:
            return gaps
        return (_gap(
            f"{adapter_id.replace('-', '_')}_attribution_missing",
            "상태 근거는 완전하다고 선언되었지만, 호출자가 패시브 제거 후 사라지는 정식 이벤트 집합을 전달하지 않았습니다.",
            adapter_id,
        ),)

    @classmethod
    def _from_attribution(
        cls,
        enabled: EnabledCharacterPassive,
        creation_hits: tuple[BattleAnalysisHit, ...],
        all_hits: tuple[BattleAnalysisHit, ...],
        team_damage: float,
        hit_damage: float,
        attribution: BattleCreationPassiveAttribution,
    ) -> BattleBuffCounterfactualResult:
        by_event = {hit.event_id: hit for hit in creation_hits}
        unknown_ids = tuple(
            event_id
            for event_id in attribution.event_ids
            if event_id not in by_event
        )
        if unknown_ids:
            raise ValueError(
                f"attribution contains non-creation events: {unknown_ids!r}"
            )
        direct_hits = tuple(by_event[event_id] for event_id in attribution.event_ids)
        direct_damage = sum(float(hit.damage) for hit in direct_hits)
        creation_damage = sum(float(hit.damage) for hit in creation_hits)
        other_creation_damage = max(0.0, creation_damage - direct_damage)
        proven_unchanged = max(0.0, team_damage - creation_damage)
        evidence_basis = attribution.evidence_basis.strip()
        if attribution.complete and not direct_hits:
            return cls._zero(
                enabled,
                team_damage,
                creation_hits,
                method="complete_lifecycle_attribution_zero",
                explanation=(
                    "전용 상태 모델이 현재 고정 축에 이 패시브로 인해 추가된 이벤트가 없음을 입증했습니다."
                    f"{evidence_basis}"
                ),
                coverage_basis_damage=hit_damage,
            )
        if attribution.complete:
            quantification = BattleDamageQuantification.from_buckets(
                status="complete",
                fully_quantified_damage=direct_damage,
                proven_unchanged_damage=team_damage - direct_damage,
                quantified_increment=direct_damage,
            )
            return cls._result(
                enabled,
                team_damage,
                direct_hits,
                (),
                all_hits,
                quantification,
                confidence="高",
                method="complete_lifecycle_event_attribution",
                explanation=(
                    "전용 상태 모델이 패시브 제거 시 사라지는 완전한 이벤트 집합을 제시했습니다;"
                    f"해당 집합만 삭제하고 나머지 실제 히트는 그대로 둡니다. {evidence_basis}"
                ),
            )
        if direct_damage > 0.0:
            gap = _gap(
                "creation_lifecycle_attribution_incomplete",
                "패시브 파생 이벤트 일부만 확인되었고, 나머지 블라썸 꽃은 여전히 완전한 생명 주기 귀속이 없습니다.",
                enabled.definition.adapter_id,
            )
            quantification = BattleDamageQuantification.from_buckets(
                status="partial",
                partially_quantified_damage=direct_damage,
                unavailable_damage=other_creation_damage,
                proven_unchanged_damage=proven_unchanged,
                quantified_increment=direct_damage,
                gaps=(gap,),
            )
            return cls._result(
                enabled,
                team_damage,
                direct_hits,
                tuple(hit for hit in creation_hits if hit not in direct_hits),
                all_hits,
                quantification,
                confidence="中",
                method="partial_lifecycle_event_attribution",
                explanation=(
                    "정식 이벤트 귀속이 확인된 직접 피해만 보고합니다;"
                    f"미귀속 꽃 피해는 정량화 불가 상태로 유지됩니다. {evidence_basis}"
                ),
            )
        return cls._unavailable(
            enabled,
            team_damage,
            creation_hits,
            (_gap(
                "creation_lifecycle_attribution_incomplete",
                "아직 직접 귀속할 수 있는 정식 이벤트가 없고, 이벤트 집합도 완전하다고 입증되지 않았습니다.",
                enabled.definition.adapter_id,
            ),),
            f"미귀속 히트는 원래 값을 유지하며 패시브 피해를 꾸며 내지 않습니다. {evidence_basis}",
            coverage_basis_damage=hit_damage,
        )

    @classmethod
    def _calculate_edgar(
        cls,
        enabled: EnabledCharacterPassive,
        team_damage: float,
        hit_damage: float,
        evidence: BattleCreationPassiveEvidence,
    ) -> BattleBuffCounterfactualResult:
        event_ids = evidence.edgar_trigger_event_ids
        if evidence.edgar_trigger_axis_complete and not event_ids:
            return cls._zero(
                enabled,
                team_damage,
                (),
                method="complete_resource_axis_without_trigger",
                explanation=(
                    "완전한 자원 이벤트 축에 지원 스킬로 발동한 차지가 없습니다; 이 구간에는 기초 에너지"
                    " 120이 지급되거나 30초 억제 창이 열리지 않습니다."
                ),
                coverage_basis_damage=hit_damage,
            )
        policy = _POLICIES[enabled.definition.adapter_id]
        gaps = tuple(
            _gap(code, explanation, enabled.definition.adapter_id)
            for code, explanation in policy.gap_specs
            if not (
                code == "charge_trigger_axis_missing"
                and evidence.edgar_trigger_axis_complete
            )
            and not (
                code == "future_action_axis_missing"
                and evidence.future_action_axis_complete
            )
        )
        if not gaps:
            gaps = (_gap(
                "edgar_resource_consumer_missing",
                "에너지 충전 효율·억제된 에너지 회복·자원 차액의 동작 소비 결과가 여전히 없습니다.",
                enabled.definition.adapter_id,
            ),)
        quantification = BattleDamageQuantification.from_buckets(
            status="unavailable",
            unavailable_damage=team_damage,
            gaps=gaps,
        )
        return build_creation_passive_result(
            enabled,
            team_damage,
            event_ids,
            quantification,
            affected_hits=len(event_ids),
            quantified_hits=0,
            confidence="低",
            method="resource_to_damage_unavailable",
            explanation=policy.unavailable_explanation,
            damage_coverage=BattleDamageCoverage(
                basis_damage=max(0.0, hit_damage),
                unresolved_damage=max(0.0, hit_damage),
            ),
        )

    @classmethod
    def _zero(
        cls,
        enabled: EnabledCharacterPassive,
        team_damage: float,
        observed_hits: Sequence[BattleAnalysisHit],
        *,
        method: str,
        explanation: str,
        coverage_basis_damage: float,
    ) -> BattleBuffCounterfactualResult:
        quantification = BattleDamageQuantification.from_buckets(
            status="not_applicable",
            proven_unchanged_damage=team_damage,
            quantified_increment=0.0,
        )
        return build_creation_passive_result(
            enabled,
            team_damage,
            tuple(hit.event_id for hit in observed_hits),
            quantification,
            affected_hits=len(observed_hits),
            quantified_hits=len(observed_hits),
            confidence="高",
            method=method,
            explanation=explanation,
            damage_coverage=BattleDamageCoverage(
                basis_damage=max(0.0, coverage_basis_damage),
            ),
        )

    @classmethod
    def _unavailable(
        cls,
        enabled: EnabledCharacterPassive,
        team_damage: float,
        creation_hits: Sequence[BattleAnalysisHit],
        gaps: tuple[BattleQuantificationGap, ...],
        explanation: str,
        *,
        coverage_basis_damage: float,
    ) -> BattleBuffCounterfactualResult:
        creation_damage = min(
            team_damage,
            sum(max(0.0, float(hit.damage)) for hit in creation_hits),
        )
        quantification = BattleDamageQuantification.from_buckets(
            status="unavailable",
            unavailable_damage=creation_damage,
            proven_unchanged_damage=team_damage - creation_damage,
            gaps=gaps,
        )
        return build_creation_passive_result(
            enabled,
            team_damage,
            tuple(hit.event_id for hit in creation_hits),
            quantification,
            affected_hits=len(creation_hits),
            quantified_hits=0,
            confidence="低",
            method="creation_lifecycle_state_unavailable",
            explanation=explanation,
            damage_coverage=BattleDamageCoverage(
                basis_damage=max(0.0, coverage_basis_damage),
                unresolved_damage=min(
                    max(0.0, coverage_basis_damage),
                    creation_damage,
                ),
            ),
        )

    @classmethod
    def _result(
        cls,
        enabled: EnabledCharacterPassive,
        team_damage: float,
        direct_hits: Sequence[BattleAnalysisHit],
        unavailable_hits: Sequence[BattleAnalysisHit],
        all_hits: Sequence[BattleAnalysisHit],
        quantification: BattleDamageQuantification,
        *,
        confidence: str,
        method: str,
        explanation: str,
    ) -> BattleBuffCounterfactualResult:
        beneficiaries, unattributed, complete_unattributed = (
            BattleCreationPassiveBeneficiaryService.calculate(
                all_hits,
                direct_hits,
                unavailable_hits,
                team_damage=team_damage,
                quantification=quantification,
            )
        )
        return build_creation_passive_result(
            enabled,
            team_damage,
            tuple(hit.event_id for hit in direct_hits),
            quantification,
            affected_hits=len(direct_hits),
            quantified_hits=len(direct_hits),
            confidence=confidence,
            method=method,
            explanation=explanation,
            beneficiaries=beneficiaries,
            quantified_unattributed_damage_gain=unattributed,
            unattributed_damage_gain=complete_unattributed,
            damage_coverage=_hit_damage_coverage(
                all_hits,
                direct_hits,
                unavailable_hits,
            ),
        )

__all__ = [
    "CREATION_PASSIVE_EVALUATION_VERSION",
    "BattleCreationPassiveAttribution",
    "BattleCreationPassiveEvidence",
    "BattleCreationPassiveEvaluationService",
]
