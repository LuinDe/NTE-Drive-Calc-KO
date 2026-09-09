# 将候选原伤害比与覆纹自身倍率比合并为完整的固定轴覆纹反事实。
"""Link a recorded Weave packet to its paired source-hit counterfactual."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from src.domain.battle_counterfactual import BattleBuildHitCounterfactual
from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleQuantificationGap,
)
from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_weave_source_service import BattleWeaveSourceIndex


def link_weave(
    rows: Sequence[BattleBuildHitCounterfactual],
    hits: Mapping[str, BattleAnalysisHit],
) -> tuple[BattleBuildHitCounterfactual, ...]:
    """Multiply Weave's own candidate ratio by its recorded source-hit ratio."""

    rows_by_event = {row.event_id: row for row in rows}
    sources = BattleWeaveSourceIndex(tuple(hits.values()))
    linked: list[BattleBuildHitCounterfactual] = []
    for row in rows:
        hit = hits.get(row.event_id)
        source = (
            None
            if hit is None or hit.classification != "weave"
            else sources.find(hit)
        )
        source_row = None if source is None else rows_by_event.get(source.event_id)
        if source_row is None:
            linked.append(row)
            continue
        source_ratio = source_row.quantification
        if (
            source_ratio.status == "not_applicable"
            or (
                source_ratio.quantified_ratio == 1.0
                and not source_ratio.gaps
            )
        ):
            linked.append(row)
            continue
        quantification = _combine_ratios(row.quantification, source_ratio)
        ratio = quantification.quantified_ratio
        linked.append(replace(
            row,
            known_projection_damage=(
                None if ratio is None else row.baseline_damage * ratio
            ),
            candidate_damage=(
                row.baseline_damage * ratio
                if ratio is not None
                and quantification.status in {"complete", "not_applicable"}
                else None
            ),
            quantification=quantification,
            candidate_formula_damage=(
                None
                if row.candidate_formula_damage is None
                or source_ratio.quantified_ratio is None
                else row.candidate_formula_damage
                * source_ratio.quantified_ratio
            ),
            source_event_id=source.event_id,
        ))
    return tuple(linked)


def _combine_ratios(
    weave: BattleCounterfactualRatio,
    source: BattleCounterfactualRatio,
) -> BattleCounterfactualRatio:
    ratios = tuple(
        ratio for ratio in (weave.quantified_ratio, source.quantified_ratio)
        if ratio is not None
    )
    gaps = _unique((
        *weave.gaps,
        *source.gaps,
        *((_missing_source_gap(),) if source.quantified_ratio is None else ()),
    ))
    included = _unique_text((
        *weave.included_dimension_ids,
        *source.included_dimension_ids,
        *(("weave_recorded_source_damage",)
          if source.quantified_ratio is not None else ()),
    ))
    cancelled = tuple(
        value for value in _unique_text((
            *weave.cancelled_dimension_ids,
            *source.cancelled_dimension_ids,
        ))
        if value not in set(included)
    )
    ratio = None if not ratios else _product(ratios)
    explanation = (
        "먼저 페어링된 원본 피해의 후보/기준선 비율로 헥스 기록값을 연동하고, "
        "그 위에 헥스 자체의 후보 배율을 중첩합니다."
    )
    if ratio is None:
        return BattleCounterfactualRatio.unavailable(
            method="linked_weave_source_unavailable",
            confidence="低",
            dependency_scope="mechanic_specific",
            cancelled_dimension_ids=cancelled,
            gaps=gaps or (_missing_source_gap(),),
            explanation="헥스 또는 페어링된 원본 피해에 정량화 가능한 후보 공식이 없습니다.",
        )
    if gaps and not included:
        return BattleCounterfactualRatio.unavailable(
            method="linked_weave_source_unavailable",
            confidence="低",
            dependency_scope="mechanic_specific",
            cancelled_dimension_ids=cancelled,
            gaps=gaps,
            explanation="헥스 또는 페어링된 원본 피해에 정량화 가능한 후보 공식이 없습니다.",
        )
    if gaps:
        return BattleCounterfactualRatio.partial(
            ratio,
            method="linked_weave_source_partial",
            confidence=_minimum_confidence(weave.confidence, source.confidence),
            dependency_scope="mechanic_specific",
            included_dimension_ids=included,
            cancelled_dimension_ids=cancelled,
            gaps=gaps,
            explanation=f"{explanation} 아직 미해석 의존 항목이 있어 결과를 부분 정량화로 표시합니다.",
        )
    return BattleCounterfactualRatio.complete(
        ratio,
        method=weave.method,
        confidence=_minimum_confidence(weave.confidence, source.confidence),
        dependency_scope="mechanic_specific",
        included_dimension_ids=included,
        cancelled_dimension_ids=cancelled,
        explanation=explanation,
    )


def _product(values: Sequence[float]) -> float:
    result = 1.0
    for value in values:
        result *= float(value)
    return result


def _unique(
    values: Sequence[BattleQuantificationGap],
) -> tuple[BattleQuantificationGap, ...]:
    return tuple(dict.fromkeys(values))


def _unique_text(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _minimum_confidence(*values: str) -> str:
    order = {"未解析": 0, "低": 1, "中": 2, "高": 3}
    normalized = tuple(value if value in order else "低" for value in values)
    return min(normalized, key=order.__getitem__) if normalized else "未解析"


def _missing_source_gap() -> BattleQuantificationGap:
    return BattleQuantificationGap(
        code="linked_weave_source_unavailable",
        dimension_id="weave_recorded_source_hit",
        dependency_scope="mechanic_specific",
        property_ids=(),
        explanation="헥스 또는 페어링된 원본 피해에 정량화 가능한 후보 공식이 없습니다.",
    )


__all__ = ["link_weave"]
