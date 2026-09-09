# 上下半各自唯一命中同一期时，只合并赛季身份，不合并敌方属性。
"""Resolve one outer-realm season across two independently matched halves."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from src.services.battle_outer_realm_period_service import BattleOuterRealmPeriod


_HALVES = ("upper", "lower")


def infer_mixed_outer_realm(
    *,
    static_database_path: Any,
    combat_context_kind: str,
    floor: int | None,
    evidence: Mapping[str, Any] | None,
    range_start_us: int | None,
    range_end_us: int | None,
    infer_half: Callable[..., Any],
    battle_occurred_at_utc: object = None,
    outer_realm_period: BattleOuterRealmPeriod | None = None,
) -> Any | None:
    """Return a season-only encounter when both complete halves resolve alike."""

    del range_start_us, range_end_us
    if str(combat_context_kind or "").strip().casefold() != "abyss" or floor is None:
        return None
    selected_rows = tuple(
        row
        for row in (evidence or {}).get("hits") or ()
    )
    grouped = {
        half: tuple(
            row
            for row in selected_rows
            if str(row.get("abyss_half") or "").strip().casefold() == half
        )
        for half in _HALVES
    }
    if any(not grouped[half] for half in _HALVES):
        return None

    resolved = []
    unresolved = []
    for half in _HALVES:
        half_evidence = dict(evidence or {})
        half_evidence["hits"] = grouped[half]
        inferred = infer_half(
            static_database_path=static_database_path,
            combat_context_kind=combat_context_kind,
            floor=floor,
            evidence=half_evidence,
            range_start_us=None,
            range_end_us=None,
            battle_occurred_at_utc=battle_occurred_at_utc,
        )
        if inferred is None or inferred.environment_kind != "outer_realm":
            unresolved.append(half)
            continue
        resolved.append(inferred)

    if not resolved:
        return None

    config_ids = {
        item.environment_ref.split("|", 1)[0]
        for item in resolved
        if item.environment_ref
    }
    if len(config_ids) != 1:
        return None
    config_id = next(iter(config_ids))
    if unresolved and (
        outer_realm_period is None or outer_realm_period.config_id != config_id
    ):
        return None
    marker = f"第{int(floor)}层"
    prefix = resolved[0].environment_name.partition(marker)[0]
    partial = bool(unresolved)
    unresolved_text = "、".join("전반" if half == "upper" else "후반" for half in unresolved)
    return replace(
        resolved[0],
        environment_ref=f"{config_id}|{int(floor)}|mixed",
        environment_name=f"{prefix}{marker}上下半",
        confidence=(
            "中" if partial else
            "低" if any(item.confidence == "低" for item in resolved)
            else "中" if any(item.confidence == "中" for item in resolved)
            else "高"
        ),
        inference_basis=(
            f"{outer_realm_period.inference_basis}; {unresolved_text}대상 매핑이 여전히 충돌하지만,"
            "정식 시간 구간으로 이미 유일하게 확정된 궤외 회차를 거꾸로 부정하지는 않습니다;"
            "매칭된 하프의 적 속성만 투영합니다."
            if partial and outer_realm_period is not None
            else (
                "전체 조우에 전·후반이 모두 포함됩니다; 두 하프의 대상별 초기 최대 HP를 보면,"
                f"각각 같은 궤외 설정 {config_id}이(가) 선택됩니다. 시즌 환경만 병합하고,"
                "두 하프의 적 속성은 병합하지 않습니다."
            )
        ),
        scope_half="",
        targets=tuple(target for item in resolved for target in item.targets),
        identities=tuple(identity for item in resolved for identity in item.identities),
        target_condition=None,
        target_conditions_by_half=tuple(
            condition
            for item in resolved
            for condition in item.target_conditions_by_half
        ),
        target_mapping_conditions_by_half=tuple(
            condition
            for item in resolved
            for condition in item.target_mapping_conditions_by_half
        ),
        ambiguous=False if partial else any(item.ambiguous for item in resolved),
        ambiguity_alternatives=tuple(
            alternative
            for item in resolved
            for alternative in item.ambiguity_alternatives
        ) if not partial else (),
        alternative_environment_refs=tuple(
            alternative
            for item in resolved
            for alternative in item.alternative_environment_refs
        ) if not partial else (),
        selection_mode=(
            "battle_time_partial_mixed" if partial else
            "ambiguous_default" if any(item.ambiguous for item in resolved)
            else "unique_hard"
        ),
        default_reason=(
            f"전투 리포트 시간으로 궤외 회차가 유일하게 확정됩니다; {unresolved_text}대상 매핑은 여전히 불완전합니다."
            if partial
            else "전·후반은 기본적으로 모두 완전한 근거에서 오며, 같은 궤외 설정에 속합니다."
        ),
    )
