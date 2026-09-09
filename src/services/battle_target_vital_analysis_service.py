# 从正式目标血量样本派生最大生命下降事件，并独立推断机制归属。
"""Derive target max-HP settlements without rewriting formal hit evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.battle_report import (
    BattleMaxHpReductionEvent,
    BattleTargetCondition,
    BattleTimelineDamageGroup,
)
from src.services.battle_fadia_hp_stack_service import (
    is_plausible_fadia_observed_source_hp,
    resolve_fadia_source_max_hp,
)
from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService,
)


TARGET_VITAL_MODEL_VERSION = "battle-target-vital-v7"

_LACRIMOSA_ID = 1004
_FADIA_ID = 1039
_LACRIMOSA_NIGHTMARE_EFFECTS = frozenset(
    {
        "ge_player_lacrimosa_blood_damage",
        "ge_player_lacrimosa_blood_damage_lv6",
    }
)
_FADIA_DARK_STAR_EFFECT = "buff_reaction_4_new"
_LACRIMOSA_MATCH_WINDOW_US = 4_000_000
_FADIA_MATCH_WINDOW_US = 5_000_000
_LACRIMOSA_REDUCTION_DAMAGE_RATIO = 2.0
_FADIA_REDUCTION_HP_RATIO = 2.0


def _text(value: Any, fallback: str = "") -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_battle_target_identity(
    row: Mapping[str, Any],
) -> tuple[str, str]:
    """Return one canonical target key and display name for every projection."""

    target_name = _text(row.get("target_name"), "未知目标")
    target_id = _text(
        row.get("target_id"),
        _text(row.get("target_monster_id"), "unknown"),
    )
    return target_id, target_name


def battle_target_identity_mode(rows: Sequence[Mapping[str, Any]]) -> str:
    """Describe whether target samples are instance-scoped or need a fallback."""

    relevant = tuple(
        row
        for row in rows
        if _text(row.get("direction"), "unknown") == "outgoing"
        and _number(row.get("target_max_hp")) is not None
    )
    if not relevant:
        return "no_target_vital_samples"
    explicit = tuple(bool(_text(row.get("target_id"))) for row in relevant)
    if all(explicit):
        return "instance_scoped"
    if any(explicit):
        return "mixed_guarded"
    return "single_target_assumed"


def bind_confirmed_single_target(
    rows: Sequence[Mapping[str, Any]],
    condition: BattleTargetCondition | None,
) -> tuple[tuple[Mapping[str, Any], ...], bool]:
    """Bind missing outgoing identities only under one explicit user target."""

    if condition is None:
        return tuple(rows), False
    selected = tuple(dict.fromkeys(condition.selected_target_ids))
    if (
        len(selected) != 1
        or not condition.primary_target_id
        or selected[0] != condition.primary_target_id
    ):
        return tuple(rows), False
    outgoing = tuple(
        row for row in rows
        if _text(row.get("direction"), "unknown") == "outgoing"
    )
    if not outgoing or any(_text(row.get("target_id")) for row in outgoing):
        return tuple(rows), False
    bound = []
    for row in rows:
        if _text(row.get("direction"), "unknown") != "outgoing":
            bound.append(row)
            continue
        copy = dict(row)
        copy["target_id"] = condition.primary_target_id
        copy["target_name"] = condition.target_name
        bound.append(copy)
    return tuple(bound), True


def _character_id(row: Mapping[str, Any]) -> int | None:
    value = row.get("character_id")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sequence(row: Mapping[str, Any]) -> int:
    try:
        return int(row.get("sequence_order") or row.get("sequence_text") or 0)
    except (TypeError, ValueError):
        return 0


def _event_id(row: Mapping[str, Any]) -> str:
    return f"{_sequence(row)}:primary"


def _target_scope(
    row: Mapping[str, Any],
    target_id: str,
) -> tuple[str, str]:
    return _text(row.get("abyss_half")).casefold(), target_id


def _lacrimosa_awaken_five_enabled(build: Mapping[str, Any] | None) -> bool:
    for character in (build or {}).get("characters") or ():
        try:
            character_id = int(character.get("character_id"))
        except (TypeError, ValueError):
            continue
        if character_id != _LACRIMOSA_ID:
            continue
        profile = character.get("profile")
        profile = profile if isinstance(profile, Mapping) else {}
        if bool(profile.get("awakening_selection_initialized")):
            selected = {
                str(value)
                for value in profile.get("selected_awaken_effect_ids") or ()
            }
            return "Effect5" in selected
        try:
            awakening_level = int(
                profile.get("awakening_level")
                or character.get("awakening_level")
                or 0
            )
        except (TypeError, ValueError):
            awakening_level = 0
        return awakening_level >= 5
    return False


@dataclass(slots=True)
class _TargetState:
    confirmed_max_hp: float | None = None
    last_observed_hp: float | None = None
    settlement_frontier_hp: float | None = None


def _remember_hp_sample(
    state: _TargetState,
    *,
    hp_before: float | None,
    hp_after: float | None,
) -> None:
    observed_hp = hp_after if hp_after is not None else hp_before
    if observed_hp is None:
        return
    state.settlement_frontier_hp = (
        observed_hp
        if state.settlement_frontier_hp is None
        else min(state.settlement_frontier_hp, observed_hp)
    )


def _settlement_frontier_hp(
    state: _TargetState,
    *,
    fallback_hp: float | None,
) -> float:
    if state.settlement_frontier_hp is not None:
        return state.settlement_frontier_hp
    return max(0.0, float(fallback_hp or 0.0))


class BattleTargetVitalAnalysisService:
    """Recognize observed max-HP drops and attribute only known player mechanisms."""

    @staticmethod
    def derive(
        *,
        rows: Sequence[Mapping[str, Any]],
        build: Mapping[str, Any] | None,
        structured_max_hp_reduction: bool = False,
    ) -> tuple[BattleMaxHpReductionEvent, ...]:
        lacrimosa_enabled = _lacrimosa_awaken_five_enabled(build)
        fadia_enabled = BattleCharacterPassiveService.is_unlocked(
            build,
            _FADIA_ID,
            2,
        )
        fadia_reference_hp = (
            resolve_fadia_source_max_hp(build) if fadia_enabled else None
        )
        identity_mode = battle_target_identity_mode(rows)
        states: dict[tuple[str, str], _TargetState] = defaultdict(_TargetState)
        pending_lacrimosa: dict[
            tuple[str, str], list[Mapping[str, Any]]
        ] = defaultdict(list)
        pending_fadia: dict[
            tuple[str, str], list[Mapping[str, Any]]
        ] = defaultdict(list)
        events: list[BattleMaxHpReductionEvent] = []

        ordered = sorted(
            rows,
            key=lambda row: (int(row.get("relative_time_us") or 0), _sequence(row)),
        )
        for row in ordered:
            if _text(row.get("direction"), "unknown") != "outgoing":
                continue
            time_us = int(row.get("relative_time_us") or 0)
            if identity_mode == "mixed_guarded" and not _text(
                row.get("target_id")
            ):
                continue
            target_id, target_name = resolve_battle_target_identity(row)
            target_scope = _target_scope(row, target_id)
            effect = _text(row.get("gameplay_effect_name")).casefold()
            character_id = _character_id(row)
            if (
                character_id == _LACRIMOSA_ID
                and effect in _LACRIMOSA_NIGHTMARE_EFFECTS
            ):
                pending_lacrimosa[target_scope].append(row)
            if (
                fadia_enabled
                and character_id == _FADIA_ID
                and effect == _FADIA_DARK_STAR_EFFECT
                and bool(_text(row.get("target_id")))
            ):
                pending_fadia[target_scope].append(row)

            state = states[target_scope]
            observed_max = _number(row.get("target_max_hp"))
            hp_before = _number(row.get("target_hp_before"))
            hp_after = _number(row.get("target_hp_after"))
            core_max_hp_reduction = _number(row.get("max_hp_reduction"))
            core_calibrated = bool(
                structured_max_hp_reduction
                and core_max_hp_reduction is not None
            )
            core_authoritative = bool(
                core_calibrated and float(core_max_hp_reduction or 0.0) > 0
            )
            fadia_candidates = tuple(
                candidate
                for candidate in pending_fadia[target_scope]
                if 0 <= time_us - int(candidate.get("relative_time_us") or 0)
                <= _FADIA_MATCH_WINDOW_US
            )
            fadia_sample_fallback = bool(
                core_calibrated
                and not core_authoritative
                and fadia_candidates
                and state.confirmed_max_hp is not None
                and observed_max is not None
                and 0.0 < observed_max < state.confirmed_max_hp
                and is_plausible_fadia_observed_source_hp(
                    (state.confirmed_max_hp - observed_max) / 2.0,
                    fadia_reference_hp,
                )
            )
            if (
                observed_max is None or observed_max <= 0
            ) and not (core_authoritative and state.confirmed_max_hp is not None):
                state.last_observed_hp = hp_after if hp_after is not None else hp_before
                continue
            if state.confirmed_max_hp is None:
                state.confirmed_max_hp = (
                    float(observed_max) + float(core_max_hp_reduction or 0.0)
                    if core_authoritative
                    else observed_max
                )
                state.last_observed_hp = hp_after if hp_after is not None else hp_before
                _remember_hp_sample(
                    state,
                    hp_before=hp_before,
                    hp_after=hp_after,
                )
                if not core_authoritative:
                    continue
            if (
                core_calibrated
                and not core_authoritative
                and not fadia_sample_fallback
            ):
                state.last_observed_hp = hp_after if hp_after is not None else hp_before
                if observed_max == state.confirmed_max_hp:
                    _remember_hp_sample(
                        state,
                        hp_before=hp_before,
                        hp_after=hp_after,
                    )
                elif (
                    observed_max is not None
                    and 0.0 < observed_max < state.confirmed_max_hp
                ):
                    # Core v4 的零值阻止正式计伤，但仍接受单调下降样本为
                    # 后续已知机制的新基线，避免把先前未知下降累计给法帝娅。
                    state.confirmed_max_hp = observed_max
                    state.settlement_frontier_hp = None
                    _remember_hp_sample(
                        state,
                        hp_before=hp_before,
                        hp_after=hp_after,
                    )
                continue
            if core_authoritative:
                observed_max = max(
                    0.0,
                    float(state.confirmed_max_hp) - float(core_max_hp_reduction),
                )
            if observed_max > state.confirmed_max_hp:
                continue
            if observed_max == state.confirmed_max_hp:
                state.last_observed_hp = hp_after if hp_after is not None else hp_before
                _remember_hp_sample(
                    state,
                    hp_before=hp_before,
                    hp_after=hp_after,
                )
                continue

            old_max_hp = state.confirmed_max_hp
            max_hp_reduction = old_max_hp - observed_max
            fadia_ratio_plausible = is_plausible_fadia_observed_source_hp(
                max_hp_reduction / 2.0,
                fadia_reference_hp,
            )
            lacrimosa_candidates = tuple(
                candidate
                for candidate in pending_lacrimosa[target_scope]
                if 0 <= time_us - int(candidate.get("relative_time_us") or 0)
                <= _LACRIMOSA_MATCH_WINDOW_US
            )
            if fadia_candidates and fadia_ratio_plausible:
                source_rows = fadia_candidates
                source_character_id = _FADIA_ID
                source_character_name = _text(
                    fadia_candidates[-1].get("character_name"),
                    "法帝娅",
                )
                mechanic_kind = "fadia_dark_star_max_hp_transfer"
                mechanic_name = "파디아 패시브·노바 HP 상한 흡수"
                source_skill_name = "罪感熔炉"
                attribution_confidence = "中"
                basis = (
                    "최대 HP 샘플 하락 전 5초 이내에 파디아 Buff_Reaction_4_new가 존재합니다;"
                    "관측 차이값으로 정산하며, 정적 스킬 매개변수로 수치를 역산하지 않습니다."
                )
            elif lacrimosa_enabled and lacrimosa_candidates:
                source_rows = lacrimosa_candidates
                source_character_id = _LACRIMOSA_ID
                source_character_name = _text(
                    lacrimosa_candidates[-1].get("character_name"),
                    "安魂曲",
                )
                mechanic_kind = "lacrimosa_nightmare_awaken_5"
                mechanic_name = "라크리모사 5각성·악몽 HP 상한 감소"
                source_skill_name = "噩梦"
                attribution_confidence = "中"
                basis = (
                    "고정 장비 세팅에서 Effect5가 활성화되어 있고, 최대 HP 샘플 하락 전 4초 이내에 악몽 피해가 존재합니다;"
                    "관측 차이값으로 정산하며, 설명 배율로 피해를 중복 계산하지 않습니다."
                )
            else:
                source_rows = ()
                source_character_id = None
                source_character_name = "미귀속"
                mechanic_kind = "unattributed_max_hp_reduction"
                mechanic_name = "미귀속 최대 HP 하락"
                source_skill_name = ""
                attribution_confidence = "低"
                basis = "최대 HP 샘플 하락은 확인되었지만, 고정 장비 세팅과 인근 이벤트만으로는 모델링된 메커니즘에 귀속하기에 부족합니다."
                if fadia_candidates and not fadia_ratio_plausible:
                    basis += (
                        " 인근에 파디아 노바가 있지만, 정식 200% 비율로 역산한 출처"
                        " MAXHP가 고정 패널과 느슨한 일치 구간에 있지 않아 파디아에 귀속하지 않습니다."
                    )

            if fadia_sample_fallback:
                basis = (
                    "nte-core v4는 이 행에 max_hp_reduction 0을 제공했지만,"
                    "현재 Core는 파디아 노바의 관측 차이값 의미를 다루지 않습니다;"
                    "같은 하프·같은 대상·5초 이내의 정식 노바 근거가 있는 경우에만,"
                    "파디아 대상 지정 샘플 폴백을 사용합니다. "
                    + basis
                )
            elif core_authoritative:
                basis = (
                    "nte-core v4 최종 generation이 구조화된 max_hp_reduction을 제공합니다;"
                    "대상 최대 HP 샘플은 일관성 검증에만 사용하며, 두 번째 정산을 생성하지 않습니다. "
                    + basis
                )

            settlement_hp = _settlement_frontier_hp(
                state,
                fallback_hp=(
                    state.last_observed_hp
                    if state.last_observed_hp is not None
                    else hp_before
                ),
            )
            hp_ratio = min(1.0, max(0.0, settlement_hp / old_max_hp))
            effective_hp_loss = max_hp_reduction * hp_ratio
            basis += (
                f" 정산 전 HP는 같은 이전 HPMax 부근 히트의 최소 신뢰 HPAfter "
                f"{settlement_hp:g}을(를) 사용합니다; 하락 후 첫 행의 정식 피해는 이 정산을 중복 차감하지 않습니다."
            )

            evidence_ids = tuple(
                dict.fromkeys(
                    (*(_event_id(candidate) for candidate in source_rows), _event_id(row))
                )
            )
            events.append(
                BattleMaxHpReductionEvent(
                    event_id=f"max-hp:{target_id}:{_sequence(row)}",
                    target_id=target_id,
                    target_name=target_name,
                    observed_at_us=time_us,
                    old_max_hp=old_max_hp,
                    new_max_hp=observed_max,
                    max_hp_reduction=max_hp_reduction,
                    hp_before_settlement=settlement_hp,
                    hp_ratio_before=hp_ratio,
                    effective_hp_loss=effective_hp_loss,
                    source_character_id=source_character_id,
                    source_character_name=source_character_name,
                    mechanic_kind=mechanic_kind,
                    mechanic_name=mechanic_name,
                    source_skill_name=source_skill_name,
                    evidence_event_ids=evidence_ids,
                    attribution_confidence=attribution_confidence,
                    calculation_confidence=(
                        "高"
                        if core_authoritative
                        else ("中" if hp_before is not None else "低")
                    ),
                    inference_basis=basis,
                    scope_half=target_scope[0],
                )
            )
            state.confirmed_max_hp = observed_max
            state.last_observed_hp = hp_after if hp_after is not None else hp_before
            state.settlement_frontier_hp = None
            _remember_hp_sample(
                state,
                hp_before=hp_before,
                hp_after=hp_after,
            )
            pending_lacrimosa[target_scope].clear()
            pending_fadia[target_scope] = [
                candidate
                for candidate in pending_fadia[target_scope]
                if candidate not in fadia_candidates
                and time_us - int(candidate.get("relative_time_us") or 0)
                <= _FADIA_MATCH_WINDOW_US
            ]

        return tuple(events)

    @staticmethod
    def estimate_from_descriptions(
        *,
        rows: Sequence[Mapping[str, Any]],
        build: Mapping[str, Any] | None,
        observed_events: Sequence[BattleMaxHpReductionEvent],
    ) -> tuple[BattleMaxHpReductionEvent, ...]:
        """Estimate uncovered triggers without adding them to formal effective damage."""

        identity_mode = battle_target_identity_mode(rows)
        lacrimosa_enabled = _lacrimosa_awaken_five_enabled(build)
        fadia_enabled = BattleCharacterPassiveService.is_unlocked(
            build,
            _FADIA_ID,
            2,
        )
        fadia_hp = resolve_fadia_source_max_hp(build) if fadia_enabled else None
        raw_fadia_rows = {
            _event_id(row): row
            for row in rows
            if _text(row.get("direction"), "unknown") == "outgoing"
            and _character_id(row) == _FADIA_ID
            and _text(row.get("gameplay_effect_name")).casefold()
            == _FADIA_DARK_STAR_EFFECT
        }
        observed_fadia_source_hp: dict[str, float] = {}
        for event in observed_events:
            if (
                event.mechanic_kind != "fadia_dark_star_max_hp_transfer"
                or event.evidence_kind != "observed"
                or event.max_hp_reduction <= 0.0
            ):
                continue
            matched_ids = tuple(sorted(
                (
                    event_id
                    for event_id in event.evidence_event_ids
                    if event_id in raw_fadia_rows
                ),
                key=lambda event_id: (
                    int(raw_fadia_rows[event_id].get("relative_time_us") or 0),
                    _sequence(raw_fadia_rows[event_id]),
                    event_id,
                ),
            ))
            if not matched_ids:
                continue
            observed_fadia_source_hp[matched_ids[-1]] = (
                event.max_hp_reduction / 2.0
            )
        observed_evidence_ids = {
            event_id
            for event in observed_events
            for event_id in event.evidence_event_ids
        }
        fadia_current_hp_by_half: dict[str, float] = {}
        estimates: list[BattleMaxHpReductionEvent] = []
        for row in sorted(
            rows,
            key=lambda item: (
                int(item.get("relative_time_us") or 0),
                _sequence(item),
            ),
        ):
            if _text(row.get("direction"), "unknown") != "outgoing":
                continue
            if identity_mode == "mixed_guarded" and not _text(
                row.get("target_id")
            ):
                continue
            event_id = _event_id(row)
            effect = _text(row.get("gameplay_effect_name")).casefold()
            character_id = _character_id(row)
            is_fadia = (
                fadia_enabled
                and character_id == _FADIA_ID
                and effect == _FADIA_DARK_STAR_EFFECT
                and bool(_text(row.get("target_id")))
            )
            if event_id in observed_evidence_ids:
                observed_source_hp = observed_fadia_source_hp.get(event_id)
                if observed_source_hp is not None:
                    half = _text(row.get("abyss_half")).casefold()
                    fadia_current_hp_by_half[half] = observed_source_hp * 1.1
                continue
            hp_before = _number(row.get("target_hp_before"))
            max_hp = _number(row.get("target_max_hp"))
            damage = max(0.0, float(row.get("damage") or 0.0))
            if (
                lacrimosa_enabled
                and character_id == _LACRIMOSA_ID
                and effect in _LACRIMOSA_NIGHTMARE_EFFECTS
                and damage > 0
            ):
                estimated_reduction = damage * _LACRIMOSA_REDUCTION_DAMAGE_RATIO
                source_character_name = _text(
                    row.get("character_name"),
                    "安魂曲",
                )
                mechanic_kind = "lacrimosa_nightmare_awaken_5_estimated"
                mechanic_name = "라크리모사 5각성·악몽 HP 상한 감소 (설명 기반 예상)"
                source_skill_name = "噩梦"
                basis = (
                    "고정 장비 세팅에서 Effect5가 활성화되어 있습니다; 스킬 설명에 따라 이번 악몽 피해의 200%로 최대 HP 감소를 예상합니다."
                    "대상에 실제로 적용되었는지, 면역인지, 이미 다른 상태로 덮어씌워졌는지는 검증하지 않았습니다."
                )
            elif (
                is_fadia
                and fadia_hp is not None
                and hp_before is not None
                and max_hp is not None
                and max_hp > 0
            ):
                half = _text(row.get("abyss_half")).casefold()
                source_current_hp = fadia_current_hp_by_half.setdefault(
                    half,
                    fadia_hp,
                )
                estimated_reduction = (
                    source_current_hp * _FADIA_REDUCTION_HP_RATIO
                )
                fadia_current_hp_by_half[half] = source_current_hp * 1.1
                source_character_name = _text(
                    row.get("character_name"),
                    "法帝娅",
                )
                mechanic_kind = "fadia_dark_star_max_hp_transfer_estimated"
                mechanic_name = "파디아 패시브·노바 HP 상한 흡수 (설명 기반 예상)"
                source_skill_name = "罪感熔炉"
                basis = (
                    "정식 속성 추출 의미에 따라 파디아의 이번 출처 현재 MAXHP의 200%로"
                    "대상 최대 HP 손실을 예상합니다;"
                    "적의 손실은 아군의 5회 HP 획득 상한에 제한받지 않습니다."
                    "출처의 현재 MAXHP는 고정 PanelHP, 3각성, 이전 본 메커니즘 중첩 수로부터 순차 계산합니다."
                )
            else:
                continue

            if identity_mode == "single_target_assumed":
                basis += (
                    " 현재 행에 대상 인스턴스 ID가 없어 이 예상은 해당 행의 HP 샘플만 사용하며,"
                    "전후 HP 변화를 같은 적의 사실로 간주하지 않습니다."
                )

            target_id, target_name = resolve_battle_target_identity(row)
            if hp_before is not None and max_hp is not None and max_hp > 0:
                old_max_hp = max_hp
                settlement_hp = max(0.0, hp_before)
                estimated_reduction = min(
                    max_hp,
                    max(0.0, estimated_reduction),
                )
                hp_ratio = min(1.0, max(0.0, hp_before / max_hp))
            else:
                old_max_hp = 0.0
                settlement_hp = 0.0
                estimated_reduction = max(0.0, estimated_reduction)
                hp_ratio = 0.5
                basis += " 신뢰할 수 있는 대상 HP 비율이 없어 50% HP 비율 기댓값으로 추정합니다."
            estimates.append(
                BattleMaxHpReductionEvent(
                    event_id=f"max-hp-estimate:{target_id}:{_sequence(row)}",
                    target_id=target_id,
                    target_name=target_name,
                    observed_at_us=int(row.get("relative_time_us") or 0),
                    old_max_hp=old_max_hp,
                    new_max_hp=max(0.0, old_max_hp - estimated_reduction),
                    max_hp_reduction=estimated_reduction,
                    hp_before_settlement=settlement_hp,
                    hp_ratio_before=hp_ratio,
                    effective_hp_loss=estimated_reduction * hp_ratio,
                    source_character_id=character_id,
                    source_character_name=source_character_name,
                    mechanic_kind=mechanic_kind,
                    mechanic_name=mechanic_name,
                    source_skill_name=source_skill_name,
                    evidence_event_ids=(event_id,),
                    attribution_confidence="中",
                    calculation_confidence="低",
                    inference_basis=basis,
                    evidence_kind="description_estimated",
                    included_in_effective_damage=False,
                    scope_half=_target_scope(row, target_id)[0],
                )
            )
        return tuple(estimates)

    @staticmethod
    def timeline_groups(
        events: Sequence[BattleMaxHpReductionEvent],
    ) -> tuple[BattleTimelineDamageGroup, ...]:
        """Project derived settlements as clickable public timeline bars."""

        return tuple(
            BattleTimelineDamageGroup(
                group_id=f"vital:{event.event_id}",
                character_id=event.source_character_id,
                character_name=event.source_character_name,
                direction="outgoing",
                channel_key=(
                    "max_hp_reduction"
                    if event.included_in_effective_damage
                    else "max_hp_reduction_estimated"
                ),
                channel_label=(
                    "HP 상한 정산"
                    if event.included_in_effective_damage
                    else "HP 상한 추정"
                ),
                damage_name=event.mechanic_name,
                source_skill_name=event.source_skill_name,
                ability_id=event.mechanic_kind,
                start_us=event.observed_at_us,
                end_us=event.observed_at_us + 1,
                hits=1,
                damage=event.effective_hp_loss,
                evidence_event_ids=(),
                detail_lines=(
                    f"최대 HP {event.old_max_hp:,.0f} → {event.new_max_hp:,.0f}",
                    f"정산 전 HP 비율 {event.hp_ratio_before * 100:.2f}%",
                    f"귀속 신뢰도 {event.attribution_confidence} / 정산 신뢰도 {event.calculation_confidence}",
                    event.inference_basis,
                ),
            )
            for event in events
        )
