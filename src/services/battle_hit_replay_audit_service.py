# 用同场实际逐击补充噩梦层数与伤害归属诊断，不改写原始伤害事实。
"""Narrow post-processing for replay evidence that the formula cannot own."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from math import isfinite, log

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitReplayFactor,
    BattleHitReplayResult,
)
from src.services.battle_hit_replay_support import (
    settle_replay_damage,
    replay_error_percent,
    replay_signed_error_percent,
)
from src.services.battle_analysis_progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)
from src.services.battle_hit_local_crit_inference_service import (
    BattleHitLocalCritInferenceService,
)
from src.domain.native_analysis import BattleComputeBackend


_NIGHTMARE_MARKER = "lacrimosa_blood_damage"
_EROSION_ID = "ge_player_zankou_dotdamage"
_OBSERVED_UNIT_WINDOW_US = 10_000_000
_RECENT_APPLICATION_WINDOW_US = 700_000
_DUPLICATE_DAMAGE_WINDOW_US = 700_000
_DARK_STAR_EFFECT_ID = "buff_reaction_4_new"


def _target_key(hit: BattleAnalysisHit) -> tuple[str, str]:
    return (
        str(hit.scope_half or "").casefold(),
        str(hit.target_id or "unknown").casefold(),
    )


def _factor(
    result: BattleHitReplayResult,
    factor_id: str,
) -> BattleHitReplayFactor | None:
    return next(
        (row for row in result.factors if row.factor_id == factor_id),
        None,
    )


def _is_nightmare_application(hit: BattleAnalysisHit) -> bool:
    effect = hit.gameplay_effect_id.casefold()
    ability = hit.ability_id.casefold()
    return bool(
        hit.character_id == 1004
        and hit.classification == "direct"
        and _NIGHTMARE_MARKER not in effect
        and "qte" not in effect
        and "qte" not in ability
        and "steal" not in effect
        and ability != "ga_lacrimosa_steal"
    )


class BattleHitReplayAuditService:
    """Apply evidence-only replay refinements after deterministic formulas."""

    @classmethod
    def postprocess(
        cls,
        analysis: BattleAnalysisSnapshot,
        results: tuple[BattleHitReplayResult, ...],
        *,
        progress_callback: BattleAnalysisProgressCallback | None = None,
        compute_backend: BattleComputeBackend | None = None,
    ) -> tuple[BattleHitReplayResult, ...]:
        def report(completed: int, message: str) -> None:
            report_battle_analysis_progress(
                progress_callback,
                phase="replay_audit",
                message=message,
                completed=completed,
                total=6,
            )

        report(0, "히트별 치명타와 귀속 근거를 감사하는 중…")
        adjusted = BattleHitLocalCritInferenceService.apply(
            analysis, results, backend=compute_backend, checkpoint=lambda: report(0, "히트별 치명타 근거를 감사하는 중…"),
        )
        report(1, "중복 피해 귀속을 검사하는 중…")
        conflicts = cls.damage_attribution_conflict_ids(
            analysis.hits,
            progress_callback=progress_callback,
        )
        report(2, "노바 HP 차이 결산을 대조하는 중…")
        adjusted = cls.apply_dark_star_hp_remainder_observation(
            analysis, adjusted, conflict_ids=conflicts,
        )
        report(3, "악몽 중첩 근거를 대조하는 중…")
        adjusted = cls.apply_nightmare_observed_layer_adjustment(
            analysis, adjusted, conflict_ids=conflicts,
        )
        report(4, "蚀心 결산 근거를 대조하는 중…")
        adjusted = cls.apply_erosion_settlement_adjustment(
            analysis, adjusted, conflict_ids=conflicts,
        )
        report(5, "히트별 귀속 충돌을 표시하는 중…")
        adjusted = cls.apply_damage_attribution_conflicts(
            analysis, adjusted, conflict_ids=conflicts,
        )
        report(6, "히트별 공식 리플레이 감사 완료")
        return adjusted

    @classmethod
    def apply_dark_star_hp_remainder_observation(
        cls,
        analysis: BattleAnalysisSnapshot,
        results: tuple[BattleHitReplayResult, ...],
        *,
        conflict_ids: frozenset[str] | None = None,
    ) -> tuple[BattleHitReplayResult, ...]:
        """Recover one missing structured Dark Star additional settlement.

        nte-core models a four-wrapper server settlement as primary damage plus
        a separately typed follow-up. Older saved axes may contain only the
        primary value while their target HP transition still covers both. This
        refinement exposes an exact formula-comparable observation without
        mutating the raw hit, totals, attribution, or persisted battle axis.
        """

        hits_by_event = {hit.event_id: hit for hit in analysis.hits}
        conflicts = (
            conflict_ids
            if conflict_ids is not None
            else cls.damage_attribution_conflict_ids(analysis.hits)
        )
        replacements: dict[str, BattleHitReplayResult] = {}
        for result in results:
            hit = hits_by_event.get(result.event_id)
            selected = result.selected_damage
            if (
                hit is None
                or result.event_id in conflicts
                or hit.direction != "outgoing"
                or hit.is_follow_up
                or hit.gameplay_effect_id.casefold() != _DARK_STAR_EFFECT_ID
                or "黯星" not in result.formula_type
                or selected is None
                or selected <= 0.0
                or result.observed_damage <= 0.0
                or hit.target_hp_before is None
                or hit.target_hp_after is None
                or not hit.target_id
                or hit.target_id.casefold() in {"unknown", "unknown-target"}
                or (hit.overkill_damage or 0.0) > 0.0
                or hit.damage_overlap_correction > 0.0
            ):
                continue
            before = float(hit.target_hp_before)
            after = float(hit.target_hp_after)
            if (
                not isfinite(before)
                or not isfinite(after)
                or before <= after
                or after <= 0.0
            ):
                continue
            hp_delta = before - after
            remainder = hp_delta - hit.damage
            tolerance = max(1.0, abs(selected) * 0.000_001)
            reported_matches = abs(hit.damage - selected) <= tolerance
            if (
                remainder <= 0.0
                or reported_matches
                or abs(remainder - selected) > tolerance
            ):
                continue
            observed = float(remainder)
            signed_error = replay_signed_error_percent(observed, selected)
            absolute_error = replay_error_percent(observed, selected)
            expected = result.expected_damage
            corrected_expected = (
                expected * observed / selected
                if expected is not None and selected > 0.0
                else None
            )
            basis = (
                f"동일 대상 HP 차이 {hp_delta:g}에서 같은 배치 주 피해 {hit.damage:g}를 빼면 "
                f"{observed:g}이(가) 나옵니다. 이는 toolkit 4단계 서버 측 결산의"
                "노바 추가 피해 모델 및 이 히트의 공식 후보와 정확히 일치합니다"
            )
            replacements[result.event_id] = replace(
                result,
                observed_damage=observed,
                selected_error_percent=absolute_error,
                signed_error_percent=signed_error,
                confidence="高",
                corrected_expected_damage=corrected_expected,
                reported_damage=hit.damage,
                observed_damage_source="target_hp_transition_remainder",
                observed_damage_basis=basis,
                missing_evidence=tuple(dict.fromkeys((
                    *result.missing_evidence,
                    (
                        f"원본 히트는 여전히 주 피해 {hit.damage:g}를 보고합니다. 공식 비교에는"
                        f"대상 HP 변화에서 엄격히 분리한 노바 추가 피해 {observed:g}만 사용하며,"
                        "원본 축·피해 합계·HP 상한 감소는 덮어쓰지 않습니다"
                    ),
                ))),
            )
        return tuple(replacements.get(row.event_id, row) for row in results)

    @classmethod
    def apply_erosion_settlement_adjustment(
        cls,
        analysis: BattleAnalysisSnapshot,
        results: tuple[BattleHitReplayResult, ...],
        *,
        conflict_ids: frozenset[str] | None = None,
    ) -> tuple[BattleHitReplayResult, ...]:
        """Choose only single-share or formal-full erosion settlement modes.

        The formal stack remains forward-replayed. This adjustment changes the
        current hit's formula coefficient only; it never back-writes the stack
        or opens an unconstrained 1..10 nearest-damage search.
        """

        hits_by_event = {hit.event_id: hit for hit in analysis.hits}
        conflicts = (
            conflict_ids
            if conflict_ids is not None
            else cls.damage_attribution_conflict_ids(analysis.hits)
        )
        replacements: dict[str, BattleHitReplayResult] = {}
        for result in results:
            hit = hits_by_event.get(result.event_id)
            stack_factor = _factor(result, "state_coefficient")
            if (
                hit is None
                or result.event_id in conflicts
                or hit.gameplay_effect_id.casefold() != _EROSION_ID
                or "蚀心" not in result.formula_type
                or stack_factor is None
                or stack_factor.value <= 1.0
                or result.non_critical_damage is None
                or result.non_critical_damage <= 0.0
                or result.observed_damage <= 0.0
            ):
                continue
            formal_layers = float(stack_factor.value)
            single_noncritical = settle_replay_damage(
                result.non_critical_damage / formal_layers
            )
            single_critical = (
                None
                if result.critical_damage is None
                else settle_replay_damage(result.critical_damage / formal_layers)
            )
            candidates: list[tuple[str, float, bool, float]] = [
                ("정식 전량", formal_layers, False, result.non_critical_damage),
                ("단일분", 1.0, False, single_noncritical),
            ]
            if result.critical_damage is not None:
                candidates.append(
                    ("정식 전량", formal_layers, True, result.critical_damage)
                )
            if single_critical is not None:
                candidates.append(("단일분", 1.0, True, single_critical))
            ranked: list[tuple[float, str, float, bool, float]] = sorted(
                (
                    abs(log(result.observed_damage / predicted)),
                    mode,
                    coefficient,
                    is_critical,
                    predicted,
                )
                for mode, coefficient, is_critical, predicted in candidates
                if predicted > 0.0
            )
            if not ranked:
                continue
            best = ranked[0]
            separation = (
                ranked[1][0] - best[0] if len(ranked) > 1 else float("inf")
            )
            _loss, mode, coefficient, inferred_critical, selected = best
            error = replay_error_percent(result.observed_damage, selected)
            signed_error = replay_signed_error_percent(
                result.observed_damage,
                selected,
            )
            if error > 20.0 or separation < 0.015:
                replacements[result.event_id] = replace(
                    result,
                    critical_state="ambiguous",
                    confidence="低",
                    missing_evidence=tuple(dict.fromkeys((
                        *result.missing_evidence,
                        "蚀心 단일분/정식 전량과 치명타 후보가 유일하게 분리되지 않아, 정방향 상태 공식을 유지하고 저신뢰로 출력합니다",
                    ))),
                )
                continue
            confidence = (
                "高"
                if error <= 2.0 and separation >= 0.03
                else "中"
            )
            basis = (
                f"정식 상태 머신은 이 히트 전에 {formal_layers:g}중첩이었습니다. 과거의 순수 자체 틱 축은"
                f"蚀心이 단일분 또는 정식 전량으로 결산될 수 있음을 증명하며, 이 히트는 이 두 유형 중 {mode}(으)로만 매칭되었습니다."
                "치명타는 틱 전체 단위로 한 번만 선택하며, 이 선택은 정식 중첩 수에 반영되지 않습니다"
            )
            factors = tuple(
                replace(
                    row,
                    label="蚀心 이번 틱 유효 결산 계수",
                    value=coefficient,
                    evidence_basis=basis,
                    formula="有效系数 ∈ {1, 正式层数}",
                )
                if row.factor_id == "state_coefficient"
                else row
                for row in result.factors
            )
            noncritical = (
                result.non_critical_damage
                if coefficient == formal_layers
                else single_noncritical
            )
            critical = (
                result.critical_damage
                if coefficient == formal_layers
                else single_critical
            )
            expected = (
                None
                if result.critical_rate is None
                else noncritical
                if critical is None
                else (
                    noncritical * (1.0 - result.critical_rate)
                    + critical * result.critical_rate
                )
            )
            corrected_expected = (
                expected * result.observed_damage / selected
                if expected is not None and selected > 0.0
                else None
            )
            replacements[result.event_id] = replace(
                result,
                non_critical_damage=noncritical,
                critical_damage=critical,
                selected_damage=selected,
                selected_error_percent=error,
                signed_error_percent=signed_error,
                critical_state=(
                    "critical" if inferred_critical else "non_critical"
                ),
                confidence=confidence,
                factors=factors,
                missing_evidence=tuple(dict.fromkeys((
                    *result.missing_evidence,
                    "蚀心 결산 모드는 제약된 단일분/정식 전량 역산에서 나온 것으로, 통제된 전투 리포트로 엔진 내부 배치 의미를 확인해야 합니다",
                ))),
                expected_damage=expected,
                corrected_expected_damage=corrected_expected,
            )
        return tuple(replacements.get(row.event_id, row) for row in results)

    @classmethod
    def apply_nightmare_observed_layer_adjustment(
        cls,
        analysis: BattleAnalysisSnapshot,
        results: tuple[BattleHitReplayResult, ...],
        *,
        conflict_ids: frozenset[str] | None = None,
    ) -> tuple[BattleHitReplayResult, ...]:
        hits_by_event = {hit.event_id: hit for hit in analysis.hits}
        conflicts = (
            conflict_ids
            if conflict_ids is not None
            else cls.damage_attribution_conflict_ids(analysis.hits)
        )
        applications: dict[tuple[str, str], list[int]] = defaultdict(list)
        for hit in analysis.hits:
            if _is_nightmare_application(hit):
                applications[_target_key(hit)].append(hit.relative_time_us)
        for times in applications.values():
            times.sort()

        samples: dict[tuple[str, str], list[tuple[int, float]]] = defaultdict(list)
        replacements: dict[str, BattleHitReplayResult] = {}
        ordered = sorted(
            results,
            key=lambda row: (
                hits_by_event[row.event_id].relative_time_us,
                hits_by_event[row.event_id].sequence,
                row.event_id,
            ),
        )
        for original in ordered:
            hit = hits_by_event[original.event_id]
            result = original
            stack_factor = _factor(result, "state_coefficient")
            if (
                original.event_id not in conflicts
                and "噩梦" in original.formula_type
                and stack_factor is not None
                and stack_factor.value >= 2.0
                and original.non_critical_damage is not None
            ):
                target = _target_key(hit)
                recent_samples = [
                    row for row in samples[target]
                    if hit.relative_time_us - row[0] <= _OBSERVED_UNIT_WINDOW_US
                ]
                samples[target] = recent_samples
                if recent_samples and cls._has_recent_application(
                    applications[target],
                    hit.relative_time_us,
                ):
                    current_layers = int(round(stack_factor.value))
                    current_match = cls._best_unit_match(
                        result,
                        current_layers,
                        recent_samples,
                    )
                    missing_match = cls._best_unit_match(
                        result,
                        current_layers - 1,
                        recent_samples,
                    )
                    if (
                        missing_match is not None
                        and missing_match[0] <= 0.01
                        and (
                            current_match is None
                            or current_match[0] >= 0.03
                        )
                    ):
                        result = cls._replace_nightmare_layers(
                            result,
                            stack_factor,
                            current_layers - 1,
                            inferred_critical=missing_match[1],
                            reference_unit=missing_match[2],
                        )
                        replacements[result.event_id] = result
                        stack_factor = _factor(result, "state_coefficient")

            if (
                original.event_id not in conflicts
                and "噩梦" in result.formula_type
                and stack_factor is not None
                and result.non_critical_damage is not None
                and stack_factor.value > 0.0
            ):
                sample = cls._plausible_observed_unit(result, stack_factor.value)
                if sample is not None:
                    samples[_target_key(hit)].append((hit.relative_time_us, sample))
        return tuple(replacements.get(row.event_id, row) for row in results)

    @staticmethod
    def _has_recent_application(times: Sequence[int], at_us: int) -> bool:
        position = bisect_right(times, at_us)
        if position <= 0:
            return False
        delta = at_us - times[position - 1]
        return 0 <= delta <= _RECENT_APPLICATION_WINDOW_US

    @staticmethod
    def _best_unit_match(
        result: BattleHitReplayResult,
        layers: int,
        samples: Sequence[tuple[int, float]],
    ) -> tuple[float, bool, float] | None:
        if layers <= 0 or result.observed_damage <= 0.0:
            return None
        crit = _factor(result, "critical")
        hypotheses = [(result.observed_damage / layers, False)]
        if crit is not None and crit.value > 1.0:
            hypotheses.append((result.observed_damage / layers / crit.value, True))
        matches = (
            (abs(unit - reference) / reference, is_critical, reference)
            for unit, is_critical in hypotheses
            for _at_us, reference in samples
            if reference > 0.0
        )
        return min(matches, default=None, key=lambda row: row[0])

    @staticmethod
    def _plausible_observed_unit(
        result: BattleHitReplayResult,
        layers: float,
    ) -> float | None:
        formula_unit = result.non_critical_damage / layers
        if formula_unit <= 0.0:
            return None
        crit = _factor(result, "critical")
        candidates = [result.observed_damage / layers]
        if crit is not None and crit.value > 1.0:
            candidates.append(result.observed_damage / layers / crit.value)
        plausible = tuple(
            (abs(unit - formula_unit) / formula_unit, unit)
            for unit in candidates
            if abs(unit - formula_unit) / formula_unit <= 0.30
        )
        return min(plausible, default=(0.0, None), key=lambda row: row[0])[1]

    @staticmethod
    def _replace_nightmare_layers(
        result: BattleHitReplayResult,
        stack_factor: BattleHitReplayFactor,
        layers: int,
        *,
        inferred_critical: bool,
        reference_unit: float,
    ) -> BattleHitReplayResult:
        ratio = layers / stack_factor.value
        assert result.non_critical_damage is not None
        noncrit = settle_replay_damage(result.non_critical_damage * ratio)
        critical = (
            None
            if result.critical_damage is None
            else settle_replay_damage(result.critical_damage * ratio)
        )
        selected = critical if inferred_critical and critical is not None else noncrit
        state = "critical" if inferred_critical and critical is not None else "non_critical"
        expected = (
            None
            if result.critical_rate is None
            else noncrit
            if critical is None
            else noncrit * (1.0 - result.critical_rate) + critical * result.critical_rate
        )
        corrected_expected = (
            expected * result.observed_damage / selected
            if expected is not None and selected > 0.0
            else None
        )
        basis = (
            f"히트별 정방향 리플레이는 원래 {stack_factor.value:g}중첩이었습니다. 이 히트 전 0.7초 안에"
            f"악몽 부여 hit가 있고, 같은 대상의 이전 악몽 실제 피해로 역산한 1중첩은 약 {reference_unit:g}입니다."
            "현재 실제 피해는 서버가 1중첩 적게 받았음만 뒷받침합니다. 이 히트의 표시/한계 이득 기준만 보정하며,"
            "이후 상태에는 반영하지 않습니다."
        )
        factors = tuple(
            replace(row, value=float(layers), evidence_basis=basis)
            if row.factor_id == "state_coefficient"
            else row
            for row in result.factors
        )
        return replace(
            result,
            non_critical_damage=noncrit,
            critical_damage=critical,
            selected_damage=selected,
            selected_error_percent=replay_error_percent(
                result.observed_damage,
                selected,
            ),
            signed_error_percent=replay_signed_error_percent(
                result.observed_damage,
                selected,
            ),
            critical_state=state,
            confidence="中",
            factors=factors,
            missing_evidence=tuple(dict.fromkeys((
                *result.missing_evidence,
                "악몽 중첩은 같은 전투의 실제 피해로 역산해 보정한 것이며, 런타임 Buff 중첩을 대신하지 않습니다",
            ))),
            expected_damage=expected,
            corrected_expected_damage=corrected_expected,
        )

    @classmethod
    def apply_damage_attribution_conflicts(
        cls,
        analysis: BattleAnalysisSnapshot,
        results: tuple[BattleHitReplayResult, ...],
        *,
        conflict_ids: frozenset[str] | None = None,
    ) -> tuple[BattleHitReplayResult, ...]:
        conflicts = (
            conflict_ids
            if conflict_ids is not None
            else cls.damage_attribution_conflict_ids(analysis.hits)
        )
        reason = (
            "같은 서버 측 HP 결산의 중복 귀속 후보: 같은 대상에 700ms 안에 다른 피해 항이"
            "같은 피해를 보고했고 HP 구간이 겹치거나 결산 끝점이 일치합니다. 이는 피해가 실제로 두 번 발생했다는 뜻이 아닙니다."
            "원본 히트와 공식 후보는 유지하지만, 이 히트는 치명타 또는 중첩 보정에 참여하지 않습니다"
        )
        return tuple(
            replace(
                row,
                critical_state="ambiguous",
                confidence="低",
                missing_evidence=tuple(dict.fromkeys((*row.missing_evidence, reason))),
            )
            if row.event_id in conflicts
            else row
            for row in results
        )

    @staticmethod
    def damage_attribution_conflict_ids(
        hits: Sequence[BattleAnalysisHit],
        *,
        progress_callback: BattleAnalysisProgressCallback | None = None,
    ) -> frozenset[str]:
        """Return raw event IDs whose damage ownership is formally conflicted."""

        ordered = sorted(
            (
                hit for hit in hits
                if getattr(hit, "direction", "") == "outgoing"
            ),
            key=lambda row: (row.relative_time_us, row.sequence, row.event_id),
        )
        # 同目标的有序局部列表避免每击复制全轴尾部，也不扫描其他目标。
        candidates: dict[
            tuple[str, str], list[tuple[BattleAnalysisHit, str, float, float]]
        ] = defaultdict(list)
        for hit in ordered:
            if hit.target_hp_before is not None and hit.target_hp_after is not None:
                low, high = sorted((hit.target_hp_after, hit.target_hp_before))
                candidates[_target_key(hit)].append((
                    hit, hit.gameplay_effect_id.casefold(), low, high,
                ))
        positions: dict[tuple[str, str], int] = defaultdict(int)
        conflicts: set[str] = set()
        for index, first in enumerate(ordered):
            if index % 64 == 0:
                report_battle_analysis_progress(
                    progress_callback,
                    phase="replay_audit_conflicts",
                    message="중복 피해 귀속 창을 스캔하는 중…",
                    completed=index,
                    total=len(ordered),
                )
            if first.target_hp_before is None or first.target_hp_after is None:
                continue
            target = _target_key(first)
            group = candidates[target]
            position = positions[target]
            positions[target] = position + 1
            _first, first_effect, first_low, first_high = group[position]
            tolerance = max(0.5, abs(first.damage) * 0.000_001)
            for second_index in range(position + 1, len(group)):
                second, second_effect, second_low, second_high = group[second_index]
                delta = second.relative_time_us - first.relative_time_us
                if delta > _DUPLICATE_DAMAGE_WINDOW_US:
                    break
                if first_effect == second_effect:
                    continue
                if abs(first.damage - second.damage) > tolerance:
                    continue
                overlaps = max(first_low, second_low) < min(first_high, second_high)
                same_endpoint = abs(
                    first.target_hp_after - second.target_hp_after
                ) <= tolerance
                if overlaps or same_endpoint:
                    conflicts.update((first.event_id, second.event_id))
        report_battle_analysis_progress(
            progress_callback,
            phase="replay_audit_conflicts",
            message="중복 피해 귀속 스캔 완료",
            completed=len(ordered),
            total=len(ordered),
        )
        return frozenset(conflicts)
