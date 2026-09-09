# 对一个 Buff 移除候选批量计算安全的逐击公式比。
"""Representative formula execution for one Buff counterfactual group."""

from __future__ import annotations

from src.services.battle_weave_source_service import BattleWeaveSourceIndex

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleQuantificationGap,
)
from src.domain.native_analysis import DirectFormulaBackend, available_battle_compute
from src.services.battle_buff_projection_memo import BattleBuffProjectionMemo
from src.services.battle_buff_candidate_projection_batch import PreparedBuffCandidateProjection
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_buff_interval_index import (
    BattleBuffIntervalIndex,
    BattleBuffIntervalQuery,
)
from src.services.battle_direct_formula_batch_service import (
    BattleDirectFormulaBatchService,
)
from src.services.battle_native_counterfactual import compare_counterfactual_batch
from src.services.battle_hit_replay_service import BattleHitReplayService
from src.services.battle_selected_hit_replay_context import (
    PreparedReplayAuditContext,
    PreparedReplayAuditInputs,
)
from src.services.battle_topple_hit_replay_service import (
    BattleToppleCharacterConfig,
)
from src.services.battle_analysis_progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)
from src.services.battle_hit_buff_projection_cache import (
    BattleHitBuffProjectionCache,
)


def _progressive_hits(
    hits: Sequence[BattleAnalysisHit],
    callback: BattleAnalysisProgressCallback | None,
    *,
    phase: str,
    message: str,
) -> Iterator[BattleAnalysisHit]:
    for ordinal, hit in enumerate(hits, start=1):
        if ordinal == 1 or (ordinal - 1) % 64 == 0:
            report_battle_analysis_progress(
                callback,
                phase=phase,
                message=message,
            )
        yield hit


def _resolve_projection_gap(
    ratio: BattleCounterfactualRatio,
    *,
    group_projection: BattleHitBuffProjection,
    group_intervals: Sequence[BattleInferredBuffInterval],
) -> BattleCounterfactualRatio:
    applied = any(
        decision.status == "applied"
        for decision in group_projection.decisions
    )
    unresolved = tuple(
        reason
        for decision in group_projection.decisions
        if decision.status == "unresolved"
        for reason in decision.reasons
    )
    if ratio.status != "not_applicable" and applied:
        return ratio
    beneficiary_unknown = any("逐击角色未知" in reason for reason in unresolved)
    lacks_formula = not any(row.modifiers for row in group_intervals)
    if not applied and not unresolved and not lacks_formula:
        return BattleCounterfactualRatio.not_applicable(
            method="buff_projection_not_applied",
            dependency_scope=ratio.dependency_scope,
            cancelled_dimension_ids=ratio.cancelled_dimension_ids,
            explanation="정식 버프 보정이 이 히트에 적용되지 않음이 확인되어 제거 전후 배율이 같습니다.",
        )
    explanation = (
        "逐击角色未知，无法确认该击是否属于来源角色之外的队友。"
        if beneficiary_unknown
        else (
            "버프 보정은 투영되었지만 현재 히트별 공식이 이 변화를 아직 매핑하지 않았습니다."
            if applied
            else (
                "버프에 해석되지 않은 수치 또는 Calculation이 있어 이 히트가 영향을 받지 않는다고 증명할 수 없습니다."
                if unresolved
                else "버프에 계산할 수 있는 속성 보정이 없어 이득을 0으로 기록할 수 없습니다."
            )
        )
    )
    gap = BattleQuantificationGap(
        code=(
            "team_others_beneficiary_unknown"
            if beneficiary_unknown
            else "formula_family_unsupported"
        ),
        dimension_id="buff_projection",
        dependency_scope="mechanic_specific",
        property_ids=tuple(sorted({
            modifier.property_id
            for row in group_intervals
            for modifier in row.modifiers
        })),
        explanation=explanation,
    )
    return BattleCounterfactualRatio.unavailable(
        method="buff_projection_unavailable",
        confidence="低",
        dependency_scope="mechanic_specific",
        cancelled_dimension_ids=ratio.cancelled_dimension_ids,
        gaps=(gap,),
        explanation=explanation,
    )


class BattleBuffCounterfactualBatchExecutor:
    """Return one safe formula ratio for every active observed hit."""

    @classmethod
    def calculate_ratios(
        cls,
        *,
        analysis: BattleAnalysisSnapshot,
        outgoing_hits: Sequence[BattleAnalysisHit],
        active_hits: Sequence[BattleAnalysisHit],
        group_intervals: tuple[BattleInferredBuffInterval, ...],
        interval_index: BattleBuffIntervalIndex,
        original_projection_by_event: Mapping[str, BattleHitBuffProjection],
        audit_inputs: PreparedReplayAuditInputs,
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        topple_character_configs: (
            Mapping[int, BattleToppleCharacterConfig] | None
        ),
        progress_callback: BattleAnalysisProgressCallback | None = None,
        direct_formula_backend: DirectFormulaBackend | None = None,
        projection_memo: BattleBuffProjectionMemo | None = None,
        prepared_candidate: PreparedBuffCandidateProjection | None = None,
    ) -> dict[str, BattleCounterfactualRatio]:
        if not active_hits:
            return {}
        if projection_memo is None:
            projection_memo = BattleBuffProjectionMemo()
        projection_memo.bind_backend(direct_formula_backend, progress_callback)
        active_ids = frozenset(hit.event_id for hit in active_hits)
        if prepared_candidate is None:
            prepared_candidate = PreparedBuffCandidateProjection.create(
                interval_index=interval_index, group_intervals=group_intervals,
                active_hits=active_hits, memo=projection_memo, evidence_by_event=audit_inputs.evidence_by_event,
                weave_sources=BattleWeaveSourceIndex(outgoing_hits),
            )
            prepared_candidate.candidate_cache.prepare((*active_hits, *prepared_candidate.formula_hits.values()))
            prepared_candidate.group_cache.prepare(prepared_candidate.consumer_hits.values())
        prepared_candidate.require_inputs(interval_index, group_intervals, active_hits, projection_memo)
        without_interval_index = prepared_candidate.without_index
        candidate_projection_cache = prepared_candidate.candidate_cache
        group_projection_cache = prepared_candidate.group_cache
        formula_hits = prepared_candidate.formula_hits
        consumer_hits = prepared_candidate.consumer_hits

        formula_character_id_by_event = {
            hit.event_id: formula_hits[hit.event_id].character_id
            for hit in _progressive_hits(
                active_hits,
                progress_callback,
                phase="buff_counterfactual_prepare",
                message="현재 버프 그룹의 히트별 공식 식별 정보를 정리하는 중…",
            )
        }
        candidate_projection_by_event = {
            hit.event_id: candidate_projection_cache.project(hit)
            for hit in _progressive_hits(
                active_hits,
                progress_callback,
                phase="buff_counterfactual_prepare",
                message="버프 제거 후의 히트별 속성을 투영하는 중…",
            )
        }
        candidate_formula_projection_by_event = {}
        for hit in _progressive_hits(
            active_hits,
            progress_callback,
            phase="buff_counterfactual_prepare",
            message="현재 버프 그룹의 공식 투영을 병합하는 중…",
        ):
            event_id = hit.event_id
            formula_hit = formula_hits[event_id]
            candidate_formula_projection_by_event[event_id] = (
                candidate_projection_by_event[event_id]
                if formula_hit is hit
                else candidate_projection_cache.project(formula_hit)
            )
        group_projection_by_event = {}
        for hit in _progressive_hits(
                active_hits,
                progress_callback,
                phase="buff_counterfactual_prepare",
                message="현재 버프 그룹의 히트별 적용 범위를 대조하는 중…",
        ):
            group_projection_by_event[hit.event_id] = (
                group_projection_cache.project(consumer_hits[hit.event_id])
            )
        target_condition_by_event = {
            event_id: target_condition
            for event_id, target_condition
            in audit_inputs.target_condition_by_event.items()
            if event_id in active_ids
        }

        def compare_hits(hits, candidates):
            jobs = [
                cls._comparison_input(
                    hit, candidates.get(hit.event_id),
                    formula_character_id_by_event=formula_character_id_by_event,
                    original_projection_by_event=original_projection_by_event,
                    candidate_projection_by_event=candidate_formula_projection_by_event,
                    target_condition_by_event=target_condition_by_event, audit_inputs=audit_inputs,
                )
                for hit in _progressive_hits(hits, progress_callback, phase="buff_counterfactual_compare",
                                             message="히트별 반사실 비교를 일괄 준비하는 중…")
            ]
            ratios = compare_counterfactual_batch(
                jobs, backend=available_battle_compute(direct_formula_backend),
                checkpoint=lambda: report_battle_analysis_progress(
                    progress_callback, phase="buff_counterfactual_compare", message="히트별 반사실을 일괄 비교하는 중…",
                ),
            )
            return {hit.event_id: ratio for hit, ratio in zip(hits, ratios, strict=True)}

        if not any(interval.modifiers for interval in group_intervals):
            raw_ratios = compare_hits(active_hits, {})
            return {
                hit.event_id: _resolve_projection_gap(
                    raw_ratios[hit.event_id],
                    group_projection=group_projection_by_event[hit.event_id],
                    group_intervals=group_intervals,
                )
                for hit in _progressive_hits(
                    active_hits,
                    progress_callback,
                    phase="buff_counterfactual_compare",
                    message="정식 보정이 없는 히트별 근거를 표시하는 중…",
                )
            }
        full_context = audit_inputs.select(active_ids)

        if full_context.requires_full_axis:
            without_analysis = replace(
                analysis,
                buff_intervals=without_interval_index.intervals,
                hit_replays=(),
                buff_counterfactuals=(),
            )
            candidate_by_event = cls._replay(
                analysis=replace(without_analysis, hits=tuple(outgoing_hits)),
                replay_hits=outgoing_hits,
                skill_evidence=skill_evidence,
                topple_character_configs=topple_character_configs,
                direct_formula_backend=direct_formula_backend,
                audit_inputs=audit_inputs,
                buff_projection_cache=candidate_projection_cache,
                audit_context=None,
                interval_index=without_interval_index,
                projection_by_event=None,
                progress_callback=progress_callback,
            )
            raw_ratios = compare_hits(active_hits, candidate_by_event)
        else:
            batches = BattleDirectFormulaBatchService.plan(
                active_hits,
                formula_character_id_by_event=formula_character_id_by_event,
                baselines=audit_inputs.baselines_by_character,
                evidence_by_event=audit_inputs.evidence_by_event,
                original_replay_by_event=audit_inputs.baseline_replay_by_event,
                original_projection_by_event=original_projection_by_event,
                candidate_projection_by_event=candidate_projection_by_event,
                candidate_formula_projection_by_event=(
                    candidate_formula_projection_by_event
                ),
                target_condition_by_event=target_condition_by_event,
                progress_callback=progress_callback,
            )
            representatives = tuple(batch.representative for batch in batches)
            representative_ids = frozenset(
                hit.event_id for hit in representatives
            )
            candidate_by_event = cls._replay(
                analysis=replace(
                    analysis,
                    hits=representatives,
                    hit_replays=(),
                    buff_counterfactuals=(),
                ),
                replay_hits=representatives,
                skill_evidence=skill_evidence,
                topple_character_configs=topple_character_configs,
                direct_formula_backend=direct_formula_backend,
                audit_inputs=audit_inputs,
                buff_projection_cache=candidate_projection_cache,
                audit_context=audit_inputs.select(representative_ids),
                interval_index=without_interval_index,
                projection_by_event=candidate_formula_projection_by_event,
                progress_callback=progress_callback,
            )
            raw_ratios: dict[str, BattleCounterfactualRatio] = {}
            fallback_hits: list[BattleAnalysisHit] = []
            representative_ratios = compare_hits(representatives, candidate_by_event)
            for ordinal, batch in enumerate(batches, start=1):
                if ordinal == 1 or (ordinal - 1) % 64 == 0:
                    report_battle_analysis_progress(
                        progress_callback,
                        phase="buff_counterfactual_compare",
                        message="중복 제거된 대표 히트의 반사실을 비교하는 중…",
                    )
                representative = batch.representative
                representative_ratio = representative_ratios[representative.event_id]
                raw_ratios[representative.event_id] = representative_ratio
                if len(batch.members) == 1:
                    continue
                if BattleDirectFormulaBatchService.ratio_can_be_shared(
                    representative_ratio
                ):
                    for hit in _progressive_hits(
                        batch.members[1:],
                        progress_callback,
                        phase="buff_counterfactual_compare",
                        message="같은 공식의 히트별 이득을 채워 넣는 중…",
                    ):
                        raw_ratios[hit.event_id] = representative_ratio
                else:
                    fallback_hits.extend(batch.members[1:])
            if fallback_hits:
                fallback_ids = frozenset(hit.event_id for hit in fallback_hits)
                fallback_candidates = cls._replay(
                    analysis=replace(
                        analysis,
                        hits=tuple(fallback_hits),
                        hit_replays=(),
                        buff_counterfactuals=(),
                    ),
                    replay_hits=fallback_hits,
                    skill_evidence=skill_evidence,
                    topple_character_configs=topple_character_configs,
                    direct_formula_backend=direct_formula_backend,
                    audit_inputs=audit_inputs,
                    buff_projection_cache=candidate_projection_cache,
                    audit_context=audit_inputs.select(fallback_ids),
                    interval_index=without_interval_index,
                    projection_by_event=candidate_formula_projection_by_event,
                    progress_callback=progress_callback,
                )
                raw_ratios.update(compare_hits(fallback_hits, fallback_candidates))

        return {
            hit.event_id: _resolve_projection_gap(
                raw_ratios[hit.event_id],
                group_projection=group_projection_by_event[hit.event_id],
                group_intervals=group_intervals,
            )
            for hit in _progressive_hits(
                active_hits,
                progress_callback,
                phase="buff_counterfactual_compare",
                message="현재 버프 그룹의 히트별 반사실을 집계하는 중…",
            )
        }

    @staticmethod
    def _replay(
        *,
        analysis: BattleAnalysisSnapshot,
        replay_hits: Sequence[BattleAnalysisHit],
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        topple_character_configs: (
            Mapping[int, BattleToppleCharacterConfig] | None
        ),
        audit_inputs: PreparedReplayAuditInputs,
        audit_context: PreparedReplayAuditContext | None,
        interval_index: BattleBuffIntervalQuery,
        projection_by_event: Mapping[str, BattleHitBuffProjection] | None,
        progress_callback: BattleAnalysisProgressCallback | None,
        direct_formula_backend: DirectFormulaBackend | None = None,
        buff_projection_cache: BattleHitBuffProjectionCache | None = None,
    ) -> dict[str, BattleHitReplayResult]:
        results = BattleHitReplayService.replay(
            analysis,
            skill_evidence,
            topple_character_configs=topple_character_configs,
            direct_formula_backend=direct_formula_backend,
            prepared_audit_context=audit_context,
            prepared_audit_inputs=audit_inputs,
            buff_interval_index=interval_index,
            buff_projection_cache=buff_projection_cache,
            projection_by_event=projection_by_event,
            progress_callback=progress_callback,
            progress_phase="buff_counterfactual_replay",
            progress_message="현재 버프 그룹의 고정 축 히트를 리플레이하는 중…",
        )
        expected_ids = {hit.event_id for hit in replay_hits}
        return {
            result.event_id: result
            for result in results
            if result.event_id in expected_ids
        }

    @staticmethod
    def _comparison_input(
        hit: BattleAnalysisHit,
        candidate_replay: BattleHitReplayResult | None,
        *,
        formula_character_id_by_event: Mapping[str, int | None],
        original_projection_by_event: Mapping[str, BattleHitBuffProjection],
        candidate_projection_by_event: Mapping[str, BattleHitBuffProjection],
        target_condition_by_event: Mapping[str, BattleTargetCondition | None],
        audit_inputs: PreparedReplayAuditInputs,
    ) -> dict:
        event_id = hit.event_id
        formula_character_id = formula_character_id_by_event[event_id]
        baseline = (
            None
            if formula_character_id is None
            else audit_inputs.baselines_by_character.get(formula_character_id)
        )
        return dict(
            hit=hit,
            original_baseline=baseline,
            candidate_baseline=baseline,
            original_projection=original_projection_by_event[event_id],
            candidate_projection=candidate_projection_by_event[event_id],
            skill_evidence=audit_inputs.evidence_by_event.get(event_id),
            original_replay=audit_inputs.baseline_replay_by_event.get(event_id),
            candidate_replay=candidate_replay,
            target_condition=target_condition_by_event.get(event_id),
        )


__all__ = ["BattleBuffCounterfactualBatchExecutor"]
