# 从正式技能与动画时点派生治疗事件，不把治疗猜测写回原始逐击。
"""Formal treatment-event adapters for fixed-axis battle replay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredAction,
    BattleInferredBuffInterval,
    BattleTreatmentEvent,
)
from src.services.battle_audited_treatment_adapter_service import (
    BattleAuditedTreatmentAdapterService,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


TREATMENT_EVENT_MODEL_VERSION = "battle-treatment-event-v2"

_ONEIROI_ID = 1075
_ONEIROI_E_NOTIFY_OFFSET_US = 498_799
_ONEIROI_QTE_NOTIFY_OFFSET_US = 784_675
_ONEIROI_Q_NOTIFY_OFFSET_US = 1_925_956
_ONEIROI_Q_PERIOD_US = 1_000_000
_ONEIROI_Q_PERIOD_COUNT = 14
_ONEIROI_E_FRAGMENT_WINDOW_US = 2_000_000
_ONEIROI_AWAKEN2_COOLDOWN_US = 5_000_000


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


def _character(
    build: Mapping[str, Any] | None,
    character_id: int,
) -> Mapping[str, Any] | None:
    for row in (build or {}).get("characters") or ():
        if int(row.get("character_id") or 0) == character_id:
            return row
    return None


def _dedupe_oneiroi_e_actions(
    actions: Sequence[BattleInferredAction],
    time_stop_intervals: Sequence[tuple[int | None, int | None]],
) -> tuple[BattleInferredAction, ...]:
    result: list[BattleInferredAction] = []
    previous_active_us: int | None = None
    for action in sorted(actions, key=lambda row: (row.start_us, row.action_id)):
        if action.input_gesture == "hold":
            continue
        active_us = _active_time(action.start_us, time_stop_intervals)
        if (
            previous_active_us is not None
            and active_us - previous_active_us < _ONEIROI_E_FRAGMENT_WINDOW_US
        ):
            continue
        result.append(action)
        previous_active_us = active_us
    return tuple(result)


def _event(
    action: BattleInferredAction,
    *,
    suffix: str,
    relative_time_us: int,
    treatment_kind: str,
    basis: str,
    is_periodic: bool = False,
    application_tick: int | None = None,
) -> BattleTreatmentEvent:
    return BattleTreatmentEvent(
        event_id=f"treatment:{action.action_id}:{suffix}",
        relative_time_us=relative_time_us,
        source_character_id=action.character_id,
        source_character_name=action.character_name,
        source_action_id=action.action_id,
        treatment_kind=treatment_kind,
        target_scope="team",
        evidence_kind="formal_skill_animation",
        confidence="中",
        evidence_event_ids=action.evidence_event_ids,
        inference_basis=basis,
        is_periodic=is_periodic,
        application_tick=application_tick,
    )


class BattleTreatmentEventService:
    """Derive treatment occurrences before any Buff consumer is evaluated."""

    @classmethod
    def infer(
        cls,
        *,
        build: Mapping[str, Any] | None,
        actions: Sequence[BattleInferredAction],
        battle_end_us: int,
        hits: Sequence[BattleAnalysisHit] = (),
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
        state_buff_intervals: Sequence[BattleInferredBuffInterval] = (),
        zankou_effect_three_recover_ratio: float | None = None,
    ) -> tuple[BattleTreatmentEvent, ...]:
        results: list[BattleTreatmentEvent] = []
        if _character(build, _ONEIROI_ID) is not None:
            oneiroi_actions = tuple(
                row for row in actions if row.character_id == _ONEIROI_ID
            )
            results.extend(cls._oneiroi_instant_events(
                oneiroi_actions,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
            ))
            results.extend(cls._oneiroi_q_period_events(
                oneiroi_actions,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
            ))
            results.extend(cls._oneiroi_awaken_two_events(
                build=build,
                actions=actions,
                time_stop_intervals=time_stop_intervals,
            ))
        results.extend(BattleAuditedTreatmentAdapterService.infer(
            build=build,
            actions=actions,
            hits=hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
            state_buff_intervals=state_buff_intervals,
            zankou_effect_three_recover_ratio=(
                zankou_effect_three_recover_ratio
            ),
        ))
        return tuple(sorted(
            results,
            key=lambda row: (row.relative_time_us, row.event_id),
        ))

    @staticmethod
    def _oneiroi_instant_events(
        actions: Sequence[BattleInferredAction],
        *,
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleTreatmentEvent, ...]:
        e_actions = _dedupe_oneiroi_e_actions(
            tuple(row for row in actions if row.input_kind == "E"),
            time_stop_intervals,
        )
        candidates = (
            *(
                (row, _ONEIROI_E_NOTIFY_OFFSET_US, "oneiroi_e_tap", "E.1")
                for row in e_actions
            ),
            *(
                (row, _ONEIROI_QTE_NOTIFY_OFFSET_US, "oneiroi_qte", "QTE.2")
                for row in actions
                if row.input_kind == "QTE"
            ),
            *(
                (row, _ONEIROI_Q_NOTIFY_OFFSET_US, "oneiroi_q", "UltraSkill.1")
                for row in actions
                if row.input_kind == "Q"
            ),
        )
        results: list[BattleTreatmentEvent] = []
        for action, offset_us, kind, notify_name in candidates:
            event_time_us = action.start_us + offset_us
            if event_time_us >= battle_end_us:
                continue
            results.append(_event(
                action,
                suffix="instant",
                relative_time_us=event_time_us,
                treatment_kind=kind,
                basis=(
                    f"일로이 정식 스킬은 애니메이션 이벤트 {notify_name}에서 치료를 부여하며, 이벤트 시점"
                    "은 동작 시작점과 정식 애니메이션 Notify 오프셋에서 파생됩니다."
                ),
            ))
        return tuple(results)

    @staticmethod
    def _oneiroi_q_period_events(
        actions: Sequence[BattleInferredAction],
        *,
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleTreatmentEvent, ...]:
        results: list[BattleTreatmentEvent] = []
        for action in actions:
            if action.input_kind != "Q":
                continue
            application_us = action.start_us + _ONEIROI_Q_NOTIFY_OFFSET_US
            if application_us >= battle_end_us:
                continue
            application_active_us = _active_time(
                application_us,
                time_stop_intervals,
            )
            for ordinal in range(1, _ONEIROI_Q_PERIOD_COUNT + 1):
                event_time_us = _raw_time(
                    application_active_us + ordinal * _ONEIROI_Q_PERIOD_US,
                    battle_end_us=battle_end_us,
                    intervals=time_stop_intervals,
                )
                if event_time_us >= battle_end_us:
                    break
                results.append(_event(
                    action,
                    suffix=f"period:{ordinal}",
                    relative_time_us=event_time_us,
                    treatment_kind="oneiroi_q_period",
                    basis=(
                        "일로이 Q 지속 치료는 정식으로 14초 지속, 주기 1초이며 부여 시 즉시 실행되지 않습니다. 주기와 지속 시간"
                        "은 시간 정지를 제외한 활성 시계를 사용합니다."
                    ),
                    is_periodic=True,
                    application_tick=ordinal,
                ))
        return tuple(results)

    @staticmethod
    def _oneiroi_awaken_two_events(
        *,
        build: Mapping[str, Any] | None,
        actions: Sequence[BattleInferredAction],
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleTreatmentEvent, ...]:
        character = _character(build, _ONEIROI_ID)
        if character is None:
            return ()
        profile = character.get("profile") or {}
        profile = profile if isinstance(profile, Mapping) else {}
        selected = {
            str(value).casefold()
            for value in profile.get("selected_awaken_effect_ids") or ()
        }
        if bool(profile.get("awakening_selection_initialized")):
            enabled = "effect2" in selected
        else:
            enabled = int(
                character.get("awakening_level")
                or profile.get("awakening_level")
                or 0
            ) >= 2
        if not enabled:
            return ()
        source_name = str(character.get("observed_name") or "伊洛伊")
        last_active_us: int | None = None
        results: list[BattleTreatmentEvent] = []
        for action in sorted(actions, key=lambda row: (row.start_us, row.action_id)):
            if action.character_id == _ONEIROI_ID or action.input_kind != "E":
                continue
            active_us = _active_time(action.start_us, time_stop_intervals)
            if (
                last_active_us is not None
                and active_us - last_active_us < _ONEIROI_AWAKEN2_COOLDOWN_US
            ):
                continue
            last_active_us = active_us
            results.append(BattleTreatmentEvent(
                event_id=f"treatment:oneiroi:awaken2:{action.action_id}",
                relative_time_us=action.start_us,
                source_character_id=_ONEIROI_ID,
                source_character_name=source_name,
                source_action_id=action.action_id,
                treatment_kind="oneiroi_awaken2_team",
                target_scope="team",
                evidence_kind="confirmed_other_character_e",
                confidence="低",
                evidence_event_ids=action.evidence_event_ids,
                inference_basis=(
                    "일로이 2각성은 현재 프로젝트에서 확인된 기준에 따라 다른 캐릭터의 실제 E 동작으로 발동되며, 재사용 대기시간"
                    "은 유효 전투 시간 5초입니다. 세 가지 무작위 소환 모두 팀 전체 치료를 한 번만 생성하고 소환 공격 hit 수"
                    "만큼 복제하지 않습니다. 정식 내보내기가 완전한 스케줄 시점을 노출하지 않아 임시로 발동 E 동작 시작점"
                    "에 앵커링합니다."
                ),
                amount_basis="일로이 정산 시 공격력 × 120%. 현재 이벤트 수치는 알 수 없음으로 유지합니다.",
            ))
        return tuple(results)
