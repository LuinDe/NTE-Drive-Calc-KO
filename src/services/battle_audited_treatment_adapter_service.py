# 将已审计且能由当前战报证据唯一定位的角色治疗行为投影为事件。
"""Character treatment producers beyond Oneiroi's direct skill events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import floor
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredAction,
    BattleInferredBuffInterval,
    BattleTreatmentEvent,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


AUDITED_TREATMENT_ADAPTER_MODEL_VERSION = "battle-audited-treatment-v2"

_LACRIMOSA_IDS = frozenset({1004})
_LACRIMOSA_PERIOD_US = 3_000_000
_LACRIMOSA_RECOVER_RATIO = 0.015
_EDGAR_ID = 1021
_EDGAR_Q_BASE_TICKS = 10
_EDGAR_Q_PERIOD_US = 1_000_000
_SHINKU_ID = 1076
_SHINKU_EFFECT5_RECOVER_RATIO = 3.0
_KUHARA_ID = 1055
_KUHARA_Q_SETTLEMENT_TOLERANCE_US = 500_000
_ZANKOU_ID = 1036
_ZANKOU_TREATMENT_PERIOD_US = 1_000_000


def _active_time(
    raw_time_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    return project_timeline_time_us(
        raw_time_us,
        battle_start_us=0,
        intervals=intervals,
        mode=ACTIVE_TIME_MODE,
    )


def _raw_time(
    active_time_us: int,
    *,
    battle_end_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    return unproject_timeline_time_us(
        active_time_us,
        battle_start_us=0,
        battle_end_us=battle_end_us,
        intervals=intervals,
        mode=ACTIVE_TIME_MODE,
        prefer_interval_end=True,
    )


def _characters(
    build: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], ...]:
    return tuple((build or {}).get("characters") or ())


def _effect_enabled(character: Mapping[str, Any], effect_id: str) -> bool:
    profile = character.get("profile") or {}
    profile = profile if isinstance(profile, Mapping) else {}
    selected = {
        str(value).casefold()
        for value in profile.get("selected_awaken_effect_ids") or ()
    }
    if bool(profile.get("awakening_selection_initialized")):
        return effect_id.casefold() in selected
    ordinal = int(effect_id.removeprefix("Effect") or 0)
    return int(
        character.get("awakening_level")
        or profile.get("awakening_level")
        or 0
    ) >= ordinal


def _base_attack(character: Mapping[str, Any]) -> float | None:
    values = tuple(
        float(row.get("value") or 0.0)
        for row in character.get("stats") or ()
        if str(row.get("property_id") or "") == "AtkBase"
        and str(row.get("source_group") or "") in {"character", "fork"}
    )
    return sum(values) if values else None


def _max_health(character: Mapping[str, Any]) -> float | None:
    totals: dict[str, float] = {}
    for row in character.get("stats") or ():
        property_id = str(row.get("property_id") or "")
        totals[property_id] = totals.get(property_id, 0.0) + float(
            row.get("value") or 0.0
        )
    base = totals.get("HPMaxBase", 0.0)
    if base <= 0:
        return None
    return base * (1.0 + totals.get("HPMaxUp", 0.0)) + totals.get(
        "HPMaxAdd",
        0.0,
    )


def _nightmare_hit(hit: BattleAnalysisHit, source_ids: frozenset[int]) -> bool:
    if hit.character_id not in source_ids or hit.damage <= 0:
        return False
    text = "|".join((
        hit.damage_name,
        hit.skill_name,
        hit.gameplay_effect_id,
    )).casefold()
    return any(marker in text for marker in (
        "噩梦",
        "nightmare",
        "lacrimosa_meleetotal",
    ))


def _hit_index(
    hits: Sequence[BattleAnalysisHit],
) -> dict[str, BattleAnalysisHit]:
    return {row.event_id: row for row in hits}


def is_kuhara_q_settlement_hit(
    hit: BattleAnalysisHit,
    actions: Sequence[BattleInferredAction],
) -> bool:
    """Return whether a BudBoom hit is formally tied to one observed Q action."""

    if (
        hit.character_id != _KUHARA_ID
        or "kuhara_budboom_damage" not in hit.gameplay_effect_id.casefold()
    ):
        return False
    return any(
        action.character_id == _KUHARA_ID
        and action.input_kind == "Q"
        and action.start_us <= hit.relative_time_us
        <= action.end_us + _KUHARA_Q_SETTLEMENT_TOLERANCE_US
        for action in actions
    )


class BattleAuditedTreatmentAdapterService:
    """Emit only treatment events supported by frozen build and axis facts."""

    @classmethod
    def infer(
        cls,
        *,
        build: Mapping[str, Any] | None,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
        state_buff_intervals: Sequence[BattleInferredBuffInterval] = (),
        zankou_effect_three_recover_ratio: float | None = None,
    ) -> tuple[BattleTreatmentEvent, ...]:
        results = [
            *cls._lacrimosa_effect_five(
                build=build,
                hits=hits,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
            ),
            *cls._edgar_hold_e(
                actions=actions,
                hits=hits,
            ),
            *cls._edgar_q_field(
                build=build,
                actions=actions,
                hits=hits,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
            ),
            *cls._shinku_effect_five(
                build=build,
                hits=hits,
            ),
            *cls._kuhara_effect_two(
                build=build,
                actions=actions,
                hits=hits,
            ),
            *cls._zankou_effect_three(
                build=build,
                state_buff_intervals=state_buff_intervals,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
                recover_ratio=zankou_effect_three_recover_ratio,
            ),
        ]
        return tuple(sorted(
            results,
            key=lambda row: (row.relative_time_us, row.event_id),
        ))

    @staticmethod
    def _lacrimosa_effect_five(
        *,
        build: Mapping[str, Any] | None,
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleTreatmentEvent, ...]:
        characters = tuple(
            row for row in _characters(build)
            if int(row.get("character_id") or 0) in _LACRIMOSA_IDS
            and _effect_enabled(row, "Effect5")
        )
        if not characters:
            return ()
        source_ids = frozenset(
            int(row.get("character_id") or 0) for row in characters
        )
        source_names = {
            int(row.get("character_id") or 0): str(
                row.get("observed_name") or "安魂曲"
            )
            for row in characters
        }
        nightmare_hits = tuple(
            row for row in hits if _nightmare_hit(row, source_ids)
        )
        maximum_active_us = _active_time(
            battle_end_us,
            time_stop_intervals,
        )
        results: list[BattleTreatmentEvent] = []
        for tick_active_us in range(
            _LACRIMOSA_PERIOD_US,
            maximum_active_us + 1,
            _LACRIMOSA_PERIOD_US,
        ):
            window_start_us = tick_active_us - _LACRIMOSA_PERIOD_US
            window_hits = tuple(
                row for row in nightmare_hits
                if window_start_us
                < _active_time(row.relative_time_us, time_stop_intervals)
                <= tick_active_us
            )
            damage_by_source: dict[int, float] = {}
            for hit in window_hits:
                source_id = int(hit.character_id or 0)
                damage_by_source[source_id] = (
                    damage_by_source.get(source_id, 0.0) + hit.damage
                )
            event_time_us = _raw_time(
                tick_active_us,
                battle_end_us=battle_end_us,
                intervals=time_stop_intervals,
            )
            if event_time_us >= battle_end_us:
                continue
            for source_id, damage in damage_by_source.items():
                if damage <= 0:
                    continue
                raw_heal = float(floor(damage * _LACRIMOSA_RECOVER_RATIO))
                evidence_ids = tuple(
                    row.event_id for row in window_hits
                    if row.character_id == source_id
                )
                results.append(BattleTreatmentEvent(
                    event_id=(
                        f"treatment:lacrimosa:effect5:{source_id}:"
                        f"{tick_active_us}"
                    ),
                    relative_time_us=event_time_us,
                    source_character_id=source_id,
                    source_character_name=source_names[source_id],
                    treatment_kind="lacrimosa_effect5_period",
                    target_scope="self",
                    evidence_kind="formal_period_and_damage_window",
                    confidence="中",
                    evidence_event_ids=evidence_ids,
                    inference_basis=(
                        "라크리모사 5각성은 유효 전투 3초마다 이전 창의 악몽 본체 피해를 합산하고,"
                        "합계의 1.5%를 현재 통합 정산 규칙에 따라 내림해 자가 치유합니다."
                        "양수 악몽 피해가 없는 창에서는 자가 치유를 추론하지 않습니다."
                        "즉 출처 측 치유 브로드캐스트를 생성하지 않습니다."
                    ),
                    target_character_ids=(source_id,),
                    raw_healing_amount=raw_heal,
                    is_periodic=True,
                    application_tick=tick_active_us // _LACRIMOSA_PERIOD_US,
                    amount_basis=(
                        f"floor({damage:g} × {_LACRIMOSA_RECOVER_RATIO:g})"
                    ),
                ))
        return tuple(results)

    @staticmethod
    def _edgar_hold_e(
        *,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
    ) -> tuple[BattleTreatmentEvent, ...]:
        held_event_ids = {
            event_id
            for action in actions
            if action.character_id == _EDGAR_ID
            and action.input_kind == "E"
            and action.input_gesture == "hold"
            for event_id in action.evidence_event_ids
        }
        results: list[BattleTreatmentEvent] = []
        for hit in hits:
            if hit.event_id not in held_event_ids:
                continue
            results.append(BattleTreatmentEvent(
                event_id=f"treatment:edgar:e:{hit.event_id}",
                relative_time_us=hit.relative_time_us,
                source_character_id=_EDGAR_ID,
                source_character_name=hit.character_name or "埃德嘉",
                treatment_kind="edgar_hold_e_segment",
                target_scope="lowest_hp_team_member",
                evidence_kind="formal_damage_segment_binding",
                confidence="中",
                evidence_event_ids=(hit.event_id,),
                inference_basis=(
                    "에드가 E 길게 누르기는 실제 피해 단계마다 치유를 한 번씩 함께 적용합니다."
                    "전투 리포트에 실제로 존재하는 길게 누르기 단계만 생성하며 이론상의 7단계를 채우지 않습니다."
                ),
                amount_basis=(
                    "스킬 레벨 고정값 + 정산 시 치유 대상 최대 HP × 스킬 레벨 비율."
                    "현재 축에는 치유 대상과 캐릭터 HP가 없어 수치는 알 수 없음으로 유지됩니다."
                ),
            ))
        return tuple(results)

    @staticmethod
    def _edgar_q_field(
        *,
        build: Mapping[str, Any] | None,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleTreatmentEvent, ...]:
        hits_by_id = _hit_index(hits)
        results: list[BattleTreatmentEvent] = []
        ordered_actions = tuple(sorted(
            actions,
            key=lambda row: (row.start_us, row.action_id),
        ))
        character = next((
            row for row in _characters(build)
            if int(row.get("character_id") or 0) == _EDGAR_ID
        ), None)
        teammate_qte_enabled = bool(
            character is not None and _effect_enabled(character, "Effect1")
        )
        truth_keys = 0
        for action in ordered_actions:
            grants_own_key = (
                action.character_id == _EDGAR_ID
                and action.input_kind in {"E", "QTE"}
            )
            grants_teammate_key = (
                teammate_qte_enabled
                and action.character_id != _EDGAR_ID
                and action.input_kind == "QTE"
            )
            if grants_own_key or grants_teammate_key:
                truth_keys = min(3, truth_keys + 1)
                continue
            if action.character_id != _EDGAR_ID or action.input_kind != "Q":
                continue
            tick_count = _EDGAR_Q_BASE_TICKS + truth_keys
            truth_keys = 0
            evidence_hits = tuple(
                hits_by_id[event_id]
                for event_id in action.evidence_event_ids
                if event_id in hits_by_id
            )
            field_start_us = min(
                (row.relative_time_us for row in evidence_hits),
                default=action.start_us,
            )
            switch_us = min(
                (
                    row.start_us for row in ordered_actions
                    if row.start_us > field_start_us
                    and row.character_id != _EDGAR_ID
                ),
                default=battle_end_us,
            )
            start_active_us = _active_time(
                field_start_us,
                time_stop_intervals,
            )
            for ordinal in range(1, tick_count + 1):
                event_time_us = _raw_time(
                    start_active_us + ordinal * _EDGAR_Q_PERIOD_US,
                    battle_end_us=battle_end_us,
                    intervals=time_stop_intervals,
                )
                if event_time_us >= min(battle_end_us, switch_us):
                    break
                results.append(BattleTreatmentEvent(
                    event_id=f"treatment:edgar:q:{action.action_id}:{ordinal}",
                    relative_time_us=event_time_us,
                    source_character_id=_EDGAR_ID,
                    source_character_name=action.character_name or "埃德嘉",
                    source_action_id=action.action_id,
                    treatment_kind="edgar_q_field_period",
                    target_scope="active_character",
                    evidence_kind="formal_field_period",
                    confidence="中",
                    evidence_event_ids=action.evidence_event_ids,
                    inference_basis=(
                        "에드가 Q 영역은 생성 후 유효 전투 1초가 지나면 매초 치유를 시작합니다."
                        "진리의 열쇠(真理之匙)는 이번 전투에서 추론된 에드가 E/QTE와 1각성 아군 QTE 동작으로 누적됩니다."
                        "최대 3개까지 쌓이며, Q 시 소모되어 기본 10회 치유를 열쇠 하나당 연장합니다."
                        "다른 캐릭터의 동작이 감지되면 이후 영역 치유를 중단합니다."
                    ),
                    is_periodic=True,
                    application_tick=ordinal,
                    amount_basis=(
                        "스킬 레벨 고정값 + 현재 치유 대상 캐릭터 최대 HP × 스킬 레벨 비율."
                        "현재 축에는 치유 대상과 캐릭터 HP가 없어 수치는 알 수 없음으로 유지됩니다."
                    ),
                ))
        return tuple(results)

    @staticmethod
    def _shinku_effect_five(
        *,
        build: Mapping[str, Any] | None,
        hits: Sequence[BattleAnalysisHit],
    ) -> tuple[BattleTreatmentEvent, ...]:
        character = next((
            row for row in _characters(build)
            if int(row.get("character_id") or 0) == _SHINKU_ID
        ), None)
        if character is None or not _effect_enabled(character, "Effect5"):
            return ()
        rage_hits = tuple(
            row for row in hits
            if row.character_id == _SHINKU_ID
            and "shinku_skill2_rage_damage"
            in row.gameplay_effect_id.casefold()
        )
        grouped: dict[tuple[int, str, str], list[BattleAnalysisHit]] = {}
        for hit in rage_hits:
            key = (
                hit.relative_time_us,
                hit.ability_id.casefold(),
                hit.gameplay_effect_id.casefold(),
            )
            grouped.setdefault(key, []).append(hit)
        base_attack = _base_attack(character)
        raw_heal = (
            None
            if base_attack is None
            else base_attack * _SHINKU_EFFECT5_RECOVER_RATIO
        )
        source_name = str(character.get("observed_name") or "真红")
        return tuple(
            BattleTreatmentEvent(
                event_id=(
                    "treatment:shinku:effect5:"
                    f"{group[0].relative_time_us}"
                ),
                relative_time_us=group[0].relative_time_us,
                source_character_id=_SHINKU_ID,
                source_character_name=source_name,
                treatment_kind="shinku_effect5_rage_e",
                target_scope="self",
                evidence_kind="formal_rage_e_damage_binding",
                confidence="中",
                evidence_event_ids=tuple(row.event_id for row in group),
                inference_basis=(
                    "신쿠 5각성은 승천의 적(升腾之赤)이 E를 대체한 두 번째 단계의 정식 피해 항목이 실제로 나타날 때만 자가 치유를 생성합니다."
                    "한 번만 생성하며, 같은 시점의 다중 대상 hit는 출처 측 치유를 중복 생성하지 않습니다."
                ),
                target_character_ids=(_SHINKU_ID,),
                raw_healing_amount=raw_heal,
                amount_basis=(
                    "GetAtkBase × 300%"
                    if base_attack is None
                    else f"{base_attack:g} × 300%"
                ),
            )
            for group in grouped.values()
        )

    @staticmethod
    def _kuhara_effect_two(
        *,
        build: Mapping[str, Any] | None,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
    ) -> tuple[BattleTreatmentEvent, ...]:
        character = next((
            row for row in _characters(build)
            if int(row.get("character_id") or 0) == _KUHARA_ID
        ), None)
        if character is None or not _effect_enabled(character, "Effect2"):
            return ()
        settlements = tuple(
            row for row in hits
            if is_kuhara_q_settlement_hit(row, actions)
        )
        grouped: dict[tuple[int, str], list[BattleAnalysisHit]] = {}
        for hit in settlements:
            grouped.setdefault(
                (hit.relative_time_us, hit.target_id),
                [],
            ).append(hit)
        source_name = str(character.get("observed_name") or "九原")
        return tuple(
            BattleTreatmentEvent(
                event_id=(
                    "treatment:kuhara:effect2:"
                    f"{group[0].relative_time_us}:{group[0].target_id}"
                ),
                relative_time_us=group[0].relative_time_us,
                source_character_id=_KUHARA_ID,
                source_character_name=source_name,
                treatment_kind="kuhara_effect2_q_settlement",
                target_scope="team",
                evidence_kind="formal_q_settlement_hit",
                confidence="中",
                evidence_event_ids=tuple(row.event_id for row in group),
                inference_basis=(
                    "구원 2각성은 Q로 능동 청산한 실제 장미 서약(玫约) 정산 시에만 파티 전체를 치유합니다."
                    "A 길게 누르기 청산과 자연 만료는 치료를 생성하지 않습니다. 다중 대상은 각자 관측된"
                    "청산 이벤트별로 독립 출처를 유지합니다."
                ),
                amount_basis=(
                    "해당 玫约 대상 누적 총 피해 × 5%. 현재 축에는 치료 실행기가"
                    "읽은 누적값이 저장되지 않아 수치는 미확인으로 유지됩니다."
                ),
            )
            for group in grouped.values()
        )

    @staticmethod
    def _zankou_effect_three(
        *,
        build: Mapping[str, Any] | None,
        state_buff_intervals: Sequence[BattleInferredBuffInterval],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
        recover_ratio: float | None,
    ) -> tuple[BattleTreatmentEvent, ...]:
        character = next((
            row for row in _characters(build)
            if int(row.get("character_id") or 0) == _ZANKOU_ID
        ), None)
        if (
            character is None
            or not _effect_enabled(character, "Effect3")
            or recover_ratio is None
            or recover_ratio <= 0
        ):
            return ()
        huo_intervals = tuple(
            row for row in state_buff_intervals
            if row.source_character_id == _ZANKOU_ID
            and row.source_effect_definition_id.endswith(":huo")
        )
        maximum_health = _max_health(character)
        raw_heal = (
            maximum_health * recover_ratio
            if maximum_health is not None
            else None
        )
        source_name = str(character.get("observed_name") or "残虹")
        results: list[BattleTreatmentEvent] = []
        for interval in huo_intervals:
            start_active_us = _active_time(
                interval.start_us,
                time_stop_intervals,
            )
            end_active_us = _active_time(
                interval.end_us,
                time_stop_intervals,
            )
            ordinal = 1
            while True:
                tick_active_us = (
                    start_active_us + ordinal * _ZANKOU_TREATMENT_PERIOD_US
                )
                if tick_active_us >= end_active_us:
                    break
                event_time_us = _raw_time(
                    tick_active_us,
                    battle_end_us=battle_end_us,
                    intervals=time_stop_intervals,
                )
                if event_time_us >= battle_end_us:
                    break
                results.append(BattleTreatmentEvent(
                    event_id=(
                        f"treatment:zankou:effect3:{interval.interval_id}:"
                        f"{ordinal}"
                    ),
                    relative_time_us=event_time_us,
                    source_character_id=_ZANKOU_ID,
                    source_character_name=source_name,
                    treatment_kind="zankou_effect3_huo_period",
                    target_scope="self",
                    evidence_kind="formal_huo_interval_and_period",
                    confidence="中",
                    evidence_event_ids=(interval.interval_id,),
                    inference_basis=(
                        "잔홍 3각성 치료는 惑 상태의 1초 정식 주기로 실행됩니다. 재구성된"
                        "惑 보유 구간 내에서만 시간 정지를 제외한 활동 시계에 따라 생성됩니다. 현재 축에는 플레이어"
                        "사망 이벤트가 없어 1각성 상시 惑는 전투 종료까지 투영됩니다."
                    ),
                    target_character_ids=(_ZANKOU_ID,),
                    raw_healing_amount=raw_heal,
                    is_periodic=True,
                    application_tick=ordinal,
                    amount_basis=(
                        f"{maximum_health:g} × {recover_ratio:g}"
                        if maximum_health is not None
                        else f"잔홍 정산 시 최대 HP × {recover_ratio:g}"
                    ),
                ))
                ordinal += 1
        return tuple(results)
