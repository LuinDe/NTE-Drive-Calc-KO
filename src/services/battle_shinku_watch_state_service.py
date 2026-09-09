# 按真红主动命中与切人弱锚估算凝视层数，保留无法恢复的状态缺口。
"""Forward Watch estimates from frozen timing, never from observed damage."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit, BattleAnalysisSnapshot, BattleSkillDamageEvidence,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE, projected_range_duration_us,
)


_WATCH = "ge_player_shinku_watch_damage"
_WATCH_EX = "ge_player_shinku_watchex_damage"
_WATCH_DAMAGE_IDS = frozenset({_WATCH, _WATCH_EX})
_PAIRED_SETTLEMENT_WINDOW_US = 150_000
_SAME_SETTLEMENT_US = 1_000
_ENTRY_DELAY_US = 2_000_000
_STACK_PERIOD_US = 1_000_000


@dataclass(frozen=True, slots=True)
class _WatchEstimate:
    stacks: int
    basis: str


def _selected_effects(character: Mapping[str, Any]) -> frozenset[str]:
    profile = character.get("profile") or {}
    if profile.get("awakening_selection_initialized"):
        return frozenset(str(value) for value in (
            profile.get("selected_awaken_effect_ids") or ()
        ))
    level = int(profile.get("awakening_level") or character.get("awakening_level") or 0)
    return frozenset(f"Effect{index}" for index in range(1, level + 1))


def _is_active_shinku_hit(hit: BattleAnalysisHit) -> bool:
    effect = hit.gameplay_effect_id.casefold()
    return bool(
        hit.character_id == 1076 and hit.direction == "outgoing"
        and hit.classification == "direct" and not hit.is_follow_up
        and effect.startswith("ge_player_shinku_")
        and effect not in _WATCH_DAMAGE_IDS
        and not any(token in effect for token in ("reaction", "qte", "passive"))
    )


def _unknown(reason: str) -> _WatchEstimate:
    return _WatchEstimate(0, "위압의 응시 중첩 수 미해석: " + reason + (
        "; 정식 중첩당 스킬 곡선을 유지하며, 기본 1중첩·각성 상한·실측 피해로 중첩 수를 역산하지 않습니다."
    ))


def _estimate_one(
    hit: BattleAnalysisHit,
    *,
    analysis: BattleAnalysisSnapshot,
    context_hits: Sequence[BattleAnalysisHit],
    selected: frozenset[str],
) -> _WatchEstimate:
    if not getattr(analysis, "axis_complete", False):
        return _unknown("히트 축이 불완전하여 연속적인 증가와 리셋의 약한 앵커를 세울 수 없습니다")
    # The triggering attack and Watch can share one server settlement.
    earlier = tuple(
        row for row in context_hits
        if row.relative_time_us < hit.relative_time_us - _SAME_SETTLEMENT_US
        and row.scope_half == hit.scope_half
        and (
            _is_active_shinku_hit(row)
            or (
                row.direction == "outgoing"
                and row.gameplay_effect_id.casefold() in _WATCH_DAMAGE_IDS
                and row.relative_time_us
                < hit.relative_time_us - _PAIRED_SETTLEMENT_WINDOW_US
            )
        )
    )
    if not earlier:
        return _unknown("이 히트 이전에 신쿠의 능동 명중이나 응시 소비 앵커가 없어 전투 시작 전 누적 중첩을 알 수 없습니다")
    anchor = max(earlier, key=lambda row: (row.relative_time_us, row.sequence))
    retains_off_field = "Effect3" in selected
    if not retains_off_field:
        # Only inferred foreground E/Q actions are weak switch evidence.
        # Teammate damage, QTE and autonomous A hits are not switch facts.
        switched_out = any(
            action.character_id != 1076 and action.input_kind in {"E", "Q"}
            and anchor.relative_time_us < action.start_us < hit.relative_time_us
            and any(
                row.event_id in action.evidence_event_ids
                and row.scope_half == hit.scope_half
                and row.direction == "outgoing" and row.classification == "direct"
                and not row.is_follow_up
                for row in context_hits
            )
            for action in getattr(analysis, "inferred_actions", ())
        )
        if switched_out:
            return _unknown(
                "세 번째 각성 항목이 비활성이고, 최근 신쿠 명중 이후 팀원이 스킬로 교체 등장한 약한 앵커가 나타났습니다; "
                "교체로 빠질 때 중첩이 초기화되어야 하지만, 복귀 후 공격 없이 머문 구간의 시작점이 수집되지 않았습니다"
            )
    active_us = projected_range_duration_us(
        anchor.relative_time_us, hit.relative_time_us,
        intervals=getattr(analysis, "time_stop_intervals", ()),
        mode=ACTIVE_TIME_MODE,
    )
    if active_us < _ENTRY_DELAY_US:
        return _unknown("최근 능동 명중 이후 정지 시간을 차감한 공백이 2초 미만이라 약한 타이밍 모델로는 이 히트를 설명할 수 없습니다")
    # Explicit weak convention: first stack at the 2 s entry boundary, then
    # each complete second. Real first-tick phase is not in this capture.
    stacks = 1 + (active_us - _ENTRY_DELAY_US) // _STACK_PERIOD_US
    trigger_evade = any(
        _is_active_shinku_hit(row) and row.target_id == hit.target_id
        and row.scope_half == hit.scope_half
        and abs(row.relative_time_us - hit.relative_time_us) <= _SAME_SETTLEMENT_US
        and "perfectevade" in row.gameplay_effect_id.casefold()
        for row in context_hits
    )
    if trigger_evade:
        stacks += 1
    cap = 16 if "Effect4" in selected else 8
    stacks = min(cap, stacks)
    return _WatchEstimate(stacks, (
        f"위압의 응시 약한 추론: 직전 신쿠 능동 명중/응시 소비 {anchor.event_id} "
        f"지점을 리셋 앵커로 삼아, 이번 전투의 정지 시간 투영을 차감한 공백 {active_us / 1_000_000:.3f}초; "
        "공백 구간에 빗나간 능동 공격이 없고 기록되지 않은 추가 극한 회피도 없다고 가정하며, "
        "2초를 채우고 진입할 때 첫 중첩을 얻은 뒤 1초가 찰 때마다 1중첩씩 증가한다고 가정합니다. "
        + ("이번 동일 배치의 극한 반격이 추가 회피 중첩 1회의 약한 앵커를 제공합니다. " if trigger_evade else "")
        + ("세 번째 각성 항목이 활성이라 캐릭터 교체로도 중첩이 초기화되지 않고 비출전 중 증가가 허용됩니다; " if retains_off_field else
           "세 번째 각성 항목이 비활성이고, 이 구간을 끊는 교체 등장의 약한 앵커는 발견되지 않았습니다; ")
        + f"고정된 각성 기준으로 상한 {cap}중첩을 적용해 {stacks}중첩으로 추정합니다. "
        "이는 Core 실측 중첩 수가 아니며, 빗나간 공격·캐릭터 교체·첫 중첩 시점 때문에 추정이 어긋날 수 있습니다."
    ))


def apply_shinku_watch_state_boundary(
    evidence: Sequence[BattleSkillDamageEvidence],
    *,
    analysis: BattleAnalysisSnapshot,
    character: Mapping[str, Any] | None,
) -> tuple[BattleSkillDamageEvidence, ...]:
    """Use only a bounded weak estimate; keep missing anchors unresolved."""
    context_hits = tuple(getattr(analysis, "timeline_hits", ()) or analysis.hits)
    hits = {row.event_id: row for row in context_hits}
    selected = _selected_effects(character or {})
    estimates: dict[str, _WatchEstimate] = {}
    for row in evidence:
        if row.damage_id.casefold() not in _WATCH_DAMAGE_IDS:
            continue
        hit = hits.get(row.event_id)
        if hit is None or character is None:
            estimates[row.event_id] = _unknown("고정된 캐릭터 또는 이 히트의 원본 식별 정보가 없습니다")
            continue
        if row.damage_id.casefold() == _WATCH_EX:
            paired = tuple(
                other for other in context_hits
                if other.gameplay_effect_id.casefold() == _WATCH
                and other.target_id == hit.target_id and other.scope_half == hit.scope_half
                and abs(other.relative_time_us - hit.relative_time_us)
                <= _PAIRED_SETTLEMENT_WINDOW_US
            )
            if len(paired) != 1:
                estimates[row.event_id] = _unknown(
                    "추가 응시가 같은 대상의 제한된 시간 구간 안에서 유일한 주 응시와 페어링되지 않습니다"
                )
                continue
            hit = paired[0]
        estimates[row.event_id] = _estimate_one(
            hit, analysis=analysis, context_hits=context_hits, selected=selected,
        )
    return tuple(
        replace(
            row, state_multiplier=float(estimates[row.event_id].stacks),
            state_multiplier_label=(
                "위압의 응시 정산 중첩 수 (약한 추론)" if estimates[row.event_id].stacks else
                "위압의 응시 정산 중첩 수 (미해석)"
            ),
            state_multiplier_basis=estimates[row.event_id].basis,
            state_confidence="低" if estimates[row.event_id].stacks else "未解析",
        )
        if row.event_id in estimates else row
        for row in evidence
    )


__all__ = ["apply_shinku_watch_state_boundary"]
