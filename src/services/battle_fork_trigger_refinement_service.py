# 将第三批人工确认弧盘的技能触发与暴击叠层投影为固定轴规则。
"""Trigger-timed fork refinements that generic exported events cannot express."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredAction,
    BattleInferredBuffInterval,
)
from src.services.battle_fork_periodic_refinement_service import (
    BattleForkPeriodicRefinementService,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


FORK_TRIGGER_REFINEMENT_MODEL_VERSION = "battle-fork-trigger-refinement-v3"
BUTTERFLY_Q_EVENT = "FORK_BUTTERFLY_TRIGGER"
CASTLE_E_EVENT = "FORK_CASTLE_TRIGGER"
CROWBAR_E_EVENT = "FORK_CROWBAR_TRIGGER"
GOLD_WOOL_ACTION_EVENT = "FORK_GOLD_WOOL_TRIGGER"
KITE_E_EVENT = "FORK_KITE_TRIGGER"
KNIGHT_CANDY_CRIT_EVENT = "FORK_KNIGHT_CANDY_TRIGGER"
CASTLE_RUNTIME_DURATION_SECONDS = 50.0

_BUTTERFLY_MARKER = "upgradestar_pack_fork_butterfly"
_CASTLE_MARKER = "upgradestar_pack_fork_castle"
_CROWBAR_MARKER = "upgradestar_pack_fork_crowbar"
_GOLD_WOOL_MARKER = "upgradestar_pack_fork_goldwool"
_KITE_MARKER = "upgradestar_pack_fork_kite"
_KNIGHT_CANDY_MARKER = "upgradestar_pack_fork_knightcandy"
_AUDITED_MARKERS = frozenset({
    _BUTTERFLY_MARKER,
    _CASTLE_MARKER,
    _CROWBAR_MARKER,
    _GOLD_WOOL_MARKER,
    _KITE_MARKER,
    _KNIGHT_CANDY_MARKER,
})


@dataclass(frozen=True, slots=True)
class ForkCriticalEvent:
    """Critical-hit evidence supplied explicitly or by first-pass hit replay."""

    event_id: str
    relative_time_us: int
    source_character_id: int
    evidence_kind: str = "explicit"


@dataclass(slots=True)
class _RefreshChain:
    start_us: int
    end_active_us: int
    action_ids: list[str]
    event_ids: list[str]


def _parameter(definition: Mapping[str, Any] | None, name_id: str) -> float | None:
    parameters = (definition or {}).get("parameters") or ()
    if isinstance(parameters, Mapping):
        value = parameters.get(name_id)
        return float(value) if isinstance(value, (int, float)) else None
    if not isinstance(parameters, Sequence) or isinstance(parameters, str):
        return None
    for row in parameters:
        if not isinstance(row, Mapping) or row.get("name_id") != name_id:
            continue
        value = row.get("value")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _modifier(
    property_id: str,
    value: float,
    *,
    source_require_tags: Sequence[str] = (),
    target_require_tags: Sequence[str] = (),
) -> BattleBuffModifierEvidence:
    return BattleBuffModifierEvidence(
        property_id=property_id,
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind="confirmed_fork_parameter",
        magnitude_value=float(value),
        calculation_asset_path="",
        value_confidence="高",
        source_require_tags=tuple(source_require_tags),
        target_require_tags=tuple(target_require_tags),
    )


def _rule(
    selected: Any,
    rule_factory: type[Any],
    *,
    suffix: str,
    name: str,
    scope: str,
    event_type: str,
    modifiers: tuple[BattleBuffModifierEvidence, ...],
    duration_seconds: float | None = None,
    stack_limit_count: int = 1,
    cooldown_seconds: float | None = None,
    refresh_whole_stack: bool = False,
) -> Any:
    effect_id = str(selected.effect_definition_id)
    stacking = "AggregateBySource"
    if refresh_whole_stack:
        stacking += "|RefreshWholeStack"
    return rule_factory(
        rule_id=f"{effect_id}:confirmed-fork:{suffix}",
        source_effect_definition_id=effect_id,
        source_kind="confirmed_fork_refinement",
        source_character_id=int(selected.character_id),
        source_character_name=str(selected.character_name),
        source_asset_path=f"combat-effect:{effect_id}",
        target_asset_path=f"confirmed-fork:{suffix}",
        target_name=name,
        target_scope=scope,
        event_type=event_type,
        effect_type="ADD",
        duration_policy=("HasDuration" if duration_seconds else "StateBound"),
        duration_seconds=duration_seconds,
        stack_count=1,
        modifiers=modifiers,
        stacking_type=stacking,
        stack_limit_count=stack_limit_count,
        cooldown_seconds=cooldown_seconds,
    )


def _rules_butterfly(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    nature = _parameter(selected.definition, "buff_Butterfly_DamageUpNatureBase")
    attachment = _parameter(selected.definition, "buff_Butterfly_attachedup")
    enhanced = _parameter(selected.definition, "buff_Butterfly_attachedup2")
    duration = _parameter(selected.definition, "buff_Butterfly_CD")
    if None in {nature, attachment, enhanced, duration}:
        return ()
    attachment_tag = ("State.Damage.Attachment",)
    return (
        _rule(
            selected,
            factory,
            suffix="butterfly-nature",
            name="现实避难所: 령속성 피해",
            scope="self",
            event_type="STATIC_EQUIPPED_SOURCE",
            modifiers=(_modifier("DamageUpNatureBase", nature),),
        ),
        _rule(
            selected,
            factory,
            suffix="butterfly-attachment-base",
            name="现实避难所: 부착물 기본 피해 증가",
            scope="self",
            event_type="STATIC_EQUIPPED_SOURCE",
            modifiers=(_modifier(
                "DamageUpGeneralBase",
                attachment,
                source_require_tags=attachment_tag,
            ),),
        ),
        _rule(
            selected,
            factory,
            suffix="butterfly-attachment-q-delta",
            name="现实避难所: Q 중 부착물 교체 단계",
            scope="self",
            event_type=BUTTERFLY_Q_EVENT,
            duration_seconds=duration,
            refresh_whole_stack=True,
            modifiers=(_modifier(
                "DamageUpGeneralBase",
                enhanced - attachment,
                source_require_tags=attachment_tag,
            ),),
        ),
    )


def _rules_castle(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    heal_up = _parameter(selected.definition, "buff_Castle_HealUp")
    declared_duration = _parameter(selected.definition, "buff_Castle_CD")
    if None in {heal_up, declared_duration}:
        return ()
    return (_rule(
        selected,
        factory,
        suffix="castle-heal",
        name=(
            "扭曲之城的呼唤: E 후 치료 효율"
            f"(설명 기재 값 {declared_duration:g}초; 현재 런타임 50초)"
        ),
        scope="self",
        event_type=CASTLE_E_EVENT,
        duration_seconds=CASTLE_RUNTIME_DURATION_SECONDS,
        refresh_whole_stack=True,
        modifiers=(_modifier("HealUp", heal_up),),
    ),)


def _rules_crowbar(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    unbalance = _parameter(selected.definition, "buff_Crowbar_Unbal")
    duration = _parameter(selected.definition, "buff_Crowbar_CD")
    if None in {unbalance, duration}:
        return ()
    return (_rule(
        selected,
        factory,
        suffix="crowbar-unbalance",
        name="时间大盗: E 후 스태거 강도",
        scope="self",
        event_type=CROWBAR_E_EVENT,
        duration_seconds=duration,
        refresh_whole_stack=True,
        modifiers=(_modifier("UnbalIntensityAdd", unbalance),),
    ),)


def _rules_gold_wool(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    lakshana = _parameter(selected.definition, "buff_GoldWool_Up")
    crit_damage = _parameter(selected.definition, "buff_GoldWool_CritDamageUp")
    duration = _parameter(selected.definition, "buff_GoldWool_CD")
    if None in {lakshana, crit_damage, duration}:
        return ()
    return (
        _rule(
            selected,
            factory,
            suffix="gold-wool-lakshana",
            name="众人追寻之物: 상속성 피해",
            scope="self",
            event_type="STATIC_EQUIPPED_SOURCE",
            modifiers=(_modifier("DamageUpLakshanaBase", lakshana),),
        ),
        _rule(
            selected,
            factory,
            suffix="gold-wool-crit-damage",
            name="众人追寻之物: E/Q 치명 피해",
            scope="self",
            event_type=GOLD_WOOL_ACTION_EVENT,
            duration_seconds=duration,
            refresh_whole_stack=True,
            modifiers=(_modifier("CritDamageBase", crit_damage),),
        ),
    )


def _rules_kite(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    attack = _parameter(selected.definition, "buff_Kite_AtkUp")
    lakshana = _parameter(selected.definition, "buff_Kite_Up")
    duration = _parameter(selected.definition, "buff_Kite_CD")
    if None in {attack, lakshana, duration}:
        return ()
    return (
        _rule(
            selected,
            factory,
            suffix="kite-attack",
            name="当心头顶: E 후 공격력",
            scope="self",
            event_type=KITE_E_EVENT,
            duration_seconds=duration,
            refresh_whole_stack=True,
            modifiers=(_modifier("AtkUp", attack),),
        ),
        _rule(
            selected,
            factory,
            suffix="kite-delay-and-stain",
            name="当心头顶: 레모라 및 스테인 상태 대상 상속성 피해",
            scope="unknown",
            event_type=KITE_E_EVENT,
            duration_seconds=duration,
            refresh_whole_stack=True,
            modifiers=(_modifier(
                "DamageUpLakshanaBase",
                lakshana,
                target_require_tags=(
                    "confirmed-target-state:delay",
                    "confirmed-target-state:stain",
                ),
            ),),
        ),
    )


def _rules_knight_candy(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    crit_damage = _parameter(
        selected.definition,
        "buff_KnightCandy_CritDamageUp",
    )
    duration = _parameter(selected.definition, "buff_KnightCandy_CD")
    if None in {crit_damage, duration}:
        return ()
    return (_rule(
        selected,
        factory,
        suffix="knight-candy-crit-stack",
        name="凶猛之绵: 치명 발생 후 치명 피해 중첩",
        scope="self",
        event_type=KNIGHT_CANDY_CRIT_EVENT,
        duration_seconds=duration,
        stack_limit_count=10,
        cooldown_seconds=0.3,
        refresh_whole_stack=True,
        modifiers=(_modifier("CritDamageBase", crit_damage),),
    ),)


def _active_time(
    raw_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    return project_timeline_time_us(
        raw_us,
        battle_start_us=0,
        intervals=intervals,
        mode=ACTIVE_TIME_MODE,
    )


def _raw_expiry(
    active_us: int,
    *,
    battle_end_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    return unproject_timeline_time_us(
        active_us,
        battle_start_us=0,
        battle_end_us=battle_end_us,
        intervals=intervals,
        mode=ACTIVE_TIME_MODE,
    )


def _interval(
    rule: Any,
    *,
    suffix: str,
    start_us: int,
    end_us: int,
    stacks: int,
    basis: str,
    action_ids: Sequence[str] = (),
    event_ids: Sequence[str] = (),
) -> BattleInferredBuffInterval | None:
    if end_us <= start_us or stacks <= 0:
        return None
    return BattleInferredBuffInterval(
        interval_id=f"buff:fork-trigger:{suffix}:{rule.rule_id}",
        buff_asset_path=rule.target_asset_path,
        buff_name=rule.target_name,
        source_effect_definition_id=rule.source_effect_definition_id,
        source_kind=rule.source_kind,
        source_character_id=rule.source_character_id,
        source_character_name=rule.source_character_name,
        target_scope=rule.target_scope,
        start_us=start_us,
        end_us=end_us,
        stacks=stacks,
        duration_policy=rule.duration_policy,
        state_confidence="中",
        value_confidence="高",
        inference_basis=basis,
        trigger_event_type=rule.event_type,
        evidence_action_ids=tuple(action_ids),
        evidence_event_ids=tuple(event_ids),
        modifiers=rule.modifiers,
        stacking_type=rule.stacking_type,
        stack_limit_count=rule.stack_limit_count,
    )


def _refresh_intervals(
    rule: Any,
    occurrences: Sequence[tuple[int, BattleInferredAction]],
    *,
    battle_end_us: int,
    time_stop_intervals: Sequence[tuple[int | None, int | None]],
    basis: str,
) -> tuple[BattleInferredBuffInterval, ...]:
    if rule.duration_seconds is None:
        return ()
    duration_us = round(rule.duration_seconds * 1_000_000)
    chains: list[_RefreshChain] = []
    for start_us, action in sorted(
        occurrences,
        key=lambda row: (row[0], row[1].action_id),
    ):
        now_active = _active_time(start_us, time_stop_intervals)
        proposed_end = now_active + duration_us
        if chains and now_active < chains[-1].end_active_us:
            chains[-1].end_active_us = proposed_end
            chains[-1].action_ids.append(action.action_id)
            chains[-1].event_ids.extend(action.evidence_event_ids)
        else:
            chains.append(_RefreshChain(
                start_us=start_us,
                end_active_us=proposed_end,
                action_ids=[action.action_id],
                event_ids=list(action.evidence_event_ids),
            ))
    result = []
    for ordinal, chain in enumerate(chains):
        expiry = _raw_expiry(
            chain.end_active_us,
            battle_end_us=battle_end_us,
            intervals=time_stop_intervals,
        )
        interval = _interval(
            rule,
            suffix=f"refresh:{ordinal}",
            start_us=chain.start_us,
            end_us=min(battle_end_us, expiry),
            stacks=1,
            basis=basis,
            action_ids=chain.action_ids,
            event_ids=chain.event_ids,
        )
        if interval is not None:
            result.append(interval)
    return tuple(result)


class BattleForkTriggerRefinementService:
    """Build and replay the manually confirmed trigger-based fork batch."""

    @staticmethod
    def owns_effect(effect_definition_id: str) -> bool:
        normalized = str(effect_definition_id or "").casefold()
        return (
            any(marker in normalized for marker in _AUDITED_MARKERS)
            or BattleForkPeriodicRefinementService.owns_effect(normalized)
        )

    @classmethod
    def rules_for_selected_effect(
        cls,
        selected: Any,
        rule_factory: type[Any],
    ) -> tuple[Any, ...]:
        effect_id = str(selected.effect_definition_id).casefold()
        builders = (
            (_BUTTERFLY_MARKER, _rules_butterfly),
            (_CASTLE_MARKER, _rules_castle),
            (_CROWBAR_MARKER, _rules_crowbar),
            (_GOLD_WOOL_MARKER, _rules_gold_wool),
            (_KITE_MARKER, _rules_kite),
            (_KNIGHT_CANDY_MARKER, _rules_knight_candy),
        )
        for marker, builder in builders:
            if marker in effect_id:
                return builder(selected, rule_factory)
        return BattleForkPeriodicRefinementService.rules_for_selected_effect(
            selected,
            rule_factory,
        )

    @classmethod
    def infer_specialized(
        cls,
        rules: Sequence[Any],
        *,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit] = (),
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
        critical_events: Sequence[ForkCriticalEvent] = (),
    ) -> tuple[BattleInferredBuffInterval, ...]:
        results: list[BattleInferredBuffInterval] = []
        for rule in rules:
            role_actions = tuple(
                action for action in actions
                if action.character_id == rule.source_character_id
            )
            occurrences: tuple[tuple[int, BattleInferredAction], ...] = ()
            basis = ""
            if rule.event_type == BUTTERFLY_Q_EVENT:
                occurrences = tuple(
                    (action.start_us, action)
                    for action in role_actions if action.input_kind == "Q"
                )
                basis = (
                    "Q 시작 시 즉시 부착물 피해 증가를 강화 단계로 교체하며, 발동한 Q 동안의 부착물"
                    "피해에 적용; Q를 반복하면 6초 창만 갱신합니다."
                )
            elif rule.event_type in {CASTLE_E_EVENT, CROWBAR_E_EVENT, KITE_E_EVENT}:
                occurrences = tuple(
                    (action.end_us + 1, action)
                    for action in role_actions if action.input_kind == "E"
                )
                basis = (
                    "E 스킬이 실제로 끝난 1마이크로초 후 적용되며, 이를 발동한 이번 E에는 적용되지 않음;"
                    "같은 이름의 효과는 중첩되지 않으며, E를 반복하면 지속 시간만 갱신합니다."
                )
            elif rule.event_type == GOLD_WOOL_ACTION_EVENT:
                occurrences = tuple(
                    (
                        action.start_us
                        if action.input_kind == "Q"
                        else action.end_us + 1,
                        action,
                    )
                    for action in role_actions
                    if action.input_kind in {"E", "Q"}
                )
                basis = (
                    "Q 시작 시 즉시 적용되고 이번 Q에도 적용; E 실제 종료 1마이크로초 후 적용되며,"
                    "이번 E에는 적용되지 않음; 어느 쪽이 발동하든 같은 20초 창만 갱신합니다."
                )
            if occurrences:
                results.extend(_refresh_intervals(
                    rule,
                    occurrences,
                    battle_end_us=battle_end_us,
                    time_stop_intervals=time_stop_intervals,
                    basis=basis,
                ))
        results.extend(cls._infer_knight_candy(
            rules,
            critical_events=critical_events,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        ))
        results.extend(BattleForkPeriodicRefinementService.infer_specialized(
            rules,
            actions=actions,
            hits=hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        ))
        return tuple(sorted(results, key=lambda row: (
            row.start_us,
            row.end_us,
            row.source_character_id,
            row.buff_asset_path,
        )))

    @classmethod
    def _infer_knight_candy(
        cls,
        rules: Sequence[Any],
        *,
        critical_events: Sequence[ForkCriticalEvent],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        results = []
        for rule in (
            row for row in rules
            if row.event_type == KNIGHT_CANDY_CRIT_EVENT
            and row.duration_seconds is not None
        ):
            cooldown_us = round(float(rule.cooldown_seconds or 0.0) * 1_000_000)
            duration_us = round(rule.duration_seconds * 1_000_000)
            events = []
            last_active_us: int | None = None
            for event in sorted(
                (
                    row for row in critical_events
                    if row.source_character_id == rule.source_character_id
                ),
                key=lambda row: (row.relative_time_us, row.event_id),
            ):
                now_active = _active_time(event.relative_time_us, time_stop_intervals)
                if last_active_us is not None and now_active - last_active_us < cooldown_us:
                    continue
                events.append((event, now_active))
                last_active_us = now_active
            stack = 0
            segment_start: int | None = None
            expiry_active_us: int | None = None
            evidence_ids: list[str] = []
            segment = 0
            for event, now_active in events:
                if expiry_active_us is not None and now_active >= expiry_active_us:
                    expiry = _raw_expiry(
                        expiry_active_us,
                        battle_end_us=battle_end_us,
                        intervals=time_stop_intervals,
                    )
                    previous = _interval(
                        rule,
                        suffix=f"knight:{segment}",
                        start_us=segment_start if segment_start is not None else expiry,
                        end_us=min(battle_end_us, expiry),
                        stacks=stack,
                        basis=(
                            "히트별 공식 리플레이가 판정한 치명 근거를 소비; 새 중첩은 발동 타격 1마이크로초 후 적용되며,"
                            "출처 측 0.3초 재사용 대기시간, 최대 10중첩이며 전체 그룹의 10초 지속 시간을 갱신합니다."
                        ),
                        event_ids=evidence_ids,
                    )
                    if previous is not None:
                        results.append(previous)
                    stack = 0
                    segment += 1
                    evidence_ids = []
                next_start = min(battle_end_us, event.relative_time_us + 1)
                if segment_start is not None and stack > 0:
                    previous = _interval(
                        rule,
                        suffix=f"knight:{segment}",
                        start_us=segment_start,
                        end_us=next_start,
                        stacks=stack,
                        basis=(
                            "히트별 공식 리플레이가 판정한 치명 근거를 소비; 새 중첩은 발동 타격 1마이크로초 후 적용되며,"
                            "출처 측 0.3초 재사용 대기시간, 최대 10중첩이며 전체 그룹의 10초 지속 시간을 갱신합니다."
                        ),
                        event_ids=evidence_ids,
                    )
                    if previous is not None:
                        results.append(previous)
                    segment += 1
                stack = min(rule.stack_limit_count, stack + 1)
                segment_start = next_start
                expiry_active_us = now_active + duration_us
                evidence_ids.append(event.event_id)
            if segment_start is not None and expiry_active_us is not None:
                expiry = _raw_expiry(
                    expiry_active_us,
                    battle_end_us=battle_end_us,
                    intervals=time_stop_intervals,
                )
                final = _interval(
                    rule,
                    suffix=f"knight:{segment}:final",
                    start_us=segment_start,
                    end_us=min(battle_end_us, expiry),
                    stacks=stack,
                    basis=(
                        "히트별 공식 리플레이가 판정한 치명 근거를 소비; 새 중첩은 발동 타격 1마이크로초 후 적용되며,"
                        "출처 측 0.3초 재사용 대기시간, 최대 10중첩이며 전체 그룹의 10초 지속 시간을 갱신합니다."
                    ),
                    event_ids=evidence_ids,
                )
                if final is not None:
                    results.append(final)
        return tuple(results)
