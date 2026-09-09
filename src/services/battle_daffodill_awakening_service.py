# 从固定逐击轴重建达芙蒂尔可证明的基础机制与觉醒状态。
"""Conservative Daffodill awakening projection for immutable battle axes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from src.domain.native_analysis import available_battle_compute

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredAction,
    BattleInferredBuffInterval,
)
from src.services.battle_damage_composition_service import (
    classify_battle_hit_channel,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


DAFFODILL_AWAKENING_MODEL_VERSION = "battle-daffodill-awakening-v1"
DAFFODILL_CHARACTER_ID = 1054
DAFFODILL_EFFECT_FIVE_DEFINITION_ID = "character_awaken:1054:Effect5"
_BASE_TOPPLE_EFFECT = "buff_tenacity_damage"
# 正式时长计算资产给出的非三觉上限档为 10 秒；三觉改为死亡/脱战前不移除。
_INSIGHT_MAX_DURATION_US = 10_000_000
_TOPPLE_SETTLEMENT_CLUSTER_US = 100_000


@dataclass(frozen=True, slots=True)
class _InsightWindow:
    target_id: str
    start_active_us: int
    end_active_us: int
    stacks: int
    action_ids: tuple[str, ...]
    event_ids: tuple[str, ...]


def _daffodill(build: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    for character in (build or {}).get("characters") or ():
        if int(character.get("character_id") or 0) == DAFFODILL_CHARACTER_ID:
            return character
    return None


def _selected_effects(character: Mapping[str, Any]) -> frozenset[str]:
    profile = character.get("profile") or {}
    if bool(profile.get("awakening_selection_initialized")):
        return frozenset(
            str(value)
            for value in profile.get("selected_awaken_effect_ids") or ()
        )
    count = max(0, min(6, int(
        character.get("awakening_level")
        or profile.get("awakening_level")
        or 0
    )))
    return frozenset(f"Effect{index}" for index in range(1, count + 1))


def _modifier(
    property_id: str,
    value: float,
    *,
    requirement: str = "",
    source_require_tags: tuple[str, ...] = (),
) -> BattleBuffModifierEvidence:
    return BattleBuffModifierEvidence(
        property_id=property_id,
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind="confirmed_static_curve",
        magnitude_value=value,
        calculation_asset_path="",
        value_confidence="高",
        application_requirement_asset_path=requirement,
        source_require_tags=source_require_tags,
    )


class BattleDaffodillAwakeningService:
    """Project only Daffodill states supported by the frozen build and axis."""

    @staticmethod
    def reliable_topple_duration_us(config: object | None) -> int | None:
        limit = float(getattr(config, "topple_limit", None) or 0.0)
        speed = float(getattr(config, "topple_recovery_speed", None) or 0.0)
        if limit <= 0.0 or speed <= 0.0:
            return None
        return round(limit / speed * 1_000_000)

    @classmethod
    def infer(
        cls,
        *,
        build: Mapping[str, Any] | None,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
        topple_duration_us: int | None = None,
        compute_backend=None,
        checkpoint=None,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        character = _daffodill(build)
        if character is None:
            return ()
        effects = _selected_effects(character)
        character_name = str(character.get("observed_name") or "达芙蒂尔")
        if (backend := available_battle_compute(compute_backend)) is not None:
            from src.services.battle_team_state_compute import daffodill_intervals
            return daffodill_intervals(
                backend, actions=actions, hits=hits, battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals, effects=effects,
                character_name=character_name, topple_duration_us=topple_duration_us,
                checkpoint=checkpoint,
            )
        intervals: list[BattleInferredBuffInterval] = []
        intervals.extend(cls._qte_e_intervals(
            actions,
            character_name=character_name,
            effect_one_enabled="Effect1" in effects,
        ))
        insight_windows = cls._insight_windows(
            actions,
            hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
            effect_three_enabled="Effect3" in effects,
        )
        if "Effect4" in effects:
            intervals.extend(cls._effect_four_intervals(
                insight_windows,
                hits,
                character_name=character_name,
                time_stop_intervals=time_stop_intervals,
            ))
        if "Effect5" in effects:
            intervals.extend(cls._effect_five_intervals(
                insight_windows,
                hits,
                character_name=character_name,
                time_stop_intervals=time_stop_intervals,
            ))
        if len(effects.intersection(
            {f"Effect{index}" for index in range(1, 7)}
        )) >= 6 and topple_duration_us is not None and topple_duration_us > 0:
            intervals.extend(cls._resonance_six_intervals(
                hits,
                character_name=character_name,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
                topple_duration_us=topple_duration_us,
            ))
        return tuple(sorted(intervals, key=lambda row: (row.start_us, row.interval_id)))

    @staticmethod
    def _qte_e_intervals(
        actions: Sequence[BattleInferredAction],
        *,
        character_name: str,
        effect_one_enabled: bool,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        stacks = 0
        stack_actions: list[str] = []
        stack_events: list[str] = []
        result: list[BattleInferredBuffInterval] = []
        for action in sorted(actions, key=lambda row: (row.start_us, row.action_id)):
            if action.input_kind == "QTE":
                if stacks < 2:
                    stacks += 1
                    stack_actions.append(action.action_id)
                    stack_events.extend(action.evidence_event_ids)
                continue
            if (
                action.character_id != DAFFODILL_CHARACTER_ID
                or action.input_kind != "E"
                or stacks <= 0
            ):
                continue
            per_stack = 1.0 if effect_one_enabled else 0.5
            result.append(BattleInferredBuffInterval(
                interval_id=f"buff:daffodill:qte-e:{action.action_id}",
                buff_asset_path="character-kit:1054:qte-e-enhancement",
                buff_name=f"탈바꿈·E 강화 ({stacks}중첩)",
                source_effect_definition_id=(
                    "character_awaken:1054:Effect1"
                    if effect_one_enabled
                    else "character-kit:1054:qte-e-enhancement"
                ),
                source_kind="confirmed_character_action_resource",
                source_character_id=DAFFODILL_CHARACTER_ID,
                source_character_name=character_name,
                target_scope=f"character:{DAFFODILL_CHARACTER_ID}",
                start_us=action.start_us,
                end_us=max(action.start_us + 1, action.end_us + 1),
                stacks=stacks,
                duration_policy="ConsumeAllOnEAction",
                state_confidence="中",
                value_confidence="高",
                inference_basis=(
                    "추정된 QTE마다 1중첩씩 최대 2중첩까지 누적되며, 다음 추정된 E가 전부 소모합니다;"
                    "고정 축은 E 통용 피해만 투영하며 불균형 게이지와 브레이크 시점은 역추정하지 않습니다."
                ),
                trigger_event_type="INFERRED_DAFFODILL_QTE_CONSUMED_BY_E",
                evidence_action_ids=tuple((*stack_actions, action.action_id)),
                evidence_event_ids=tuple(dict.fromkeys((
                    *stack_events, *action.evidence_event_ids,
                ))),
                modifiers=(_modifier(
                    "DamageUpGeneralBase", per_stack * stacks,
                    source_require_tags=("State.Damage.Skill",),
                ),),
                stacking_type="AggregateByTarget",
                stack_limit_count=2,
            ))
            stacks = 0
            stack_actions.clear()
            stack_events.clear()
        return tuple(result)

    @staticmethod
    def _insight_windows(
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
        *,
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
        effect_three_enabled: bool,
    ) -> tuple[_InsightWindow, ...]:
        hit_by_id = {hit.event_id: hit for hit in hits}
        active_end = project_timeline_time_us(
            battle_end_us,
            battle_start_us=0,
            intervals=time_stop_intervals,
            mode=ACTIVE_TIME_MODE,
        )
        latest: dict[str, _InsightWindow] = {}
        latest_index: dict[str, int] = {}
        windows: list[_InsightWindow] = []
        for action in sorted(actions, key=lambda row: (row.end_us, row.action_id)):
            if action.character_id != DAFFODILL_CHARACTER_ID or action.input_kind != "Q":
                continue
            targets = tuple(dict.fromkeys(
                hit_by_id[event_id].target_id
                for event_id in action.evidence_event_ids
                if event_id in hit_by_id and hit_by_id[event_id].target_id
            ))
            start_active = project_timeline_time_us(
                action.end_us,
                battle_start_us=0,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
            )
            for target_id in targets:
                previous = latest.get(target_id)
                previous_stacks = (
                    previous.stacks
                    if effect_three_enabled
                    and previous is not None
                    and start_active < previous.end_active_us
                    else 0
                )
                if (
                    not effect_three_enabled
                    and previous is not None
                    and start_active < previous.end_active_us
                ):
                    # 三觉前重复施加只刷新单层洞察，不允许借 Buff 本体的容量叠到二层。
                    previous_index = latest_index[target_id]
                    windows[previous_index] = _InsightWindow(
                        target_id=previous.target_id,
                        start_active_us=previous.start_active_us,
                        end_active_us=start_active,
                        stacks=previous.stacks,
                        action_ids=previous.action_ids,
                        event_ids=previous.event_ids,
                    )
                window = _InsightWindow(
                    target_id=target_id,
                    start_active_us=start_active,
                    end_active_us=(
                        active_end + 1
                        if effect_three_enabled
                        else start_active + _INSIGHT_MAX_DURATION_US
                    ),
                    stacks=(min(2, previous_stacks + 1) if effect_three_enabled else 1),
                    action_ids=(
                        *((previous.action_ids if previous_stacks else ())),
                        action.action_id,
                    ),
                    event_ids=tuple(dict.fromkeys((
                        *((previous.event_ids if previous_stacks else ())),
                        *action.evidence_event_ids,
                    ))),
                )
                latest[target_id] = window
                latest_index[target_id] = len(windows)
                windows.append(window)
        if not effect_three_enabled:
            topple_active_times: dict[str, list[int]] = {}
            for hit in hits:
                if hit.gameplay_effect_id.casefold() != _BASE_TOPPLE_EFFECT:
                    continue
                topple_active_times.setdefault(hit.target_id, []).append(
                    project_timeline_time_us(
                        hit.relative_time_us,
                        battle_start_us=0,
                        intervals=time_stop_intervals,
                        mode=ACTIVE_TIME_MODE,
                    )
                )
            # 零/非三觉洞察在第一次倾陷结算后移除；结束点加一微秒以让该触发击消费状态。
            for index, window in enumerate(windows):
                trigger_at = next((
                    at_us
                    for at_us in sorted(topple_active_times.get(window.target_id, ()))
                    if window.start_active_us <= at_us < window.end_active_us
                ), None)
                if trigger_at is not None:
                    windows[index] = _InsightWindow(
                        target_id=window.target_id,
                        start_active_us=window.start_active_us,
                        end_active_us=trigger_at + 1,
                        stacks=window.stacks,
                        action_ids=window.action_ids,
                        event_ids=window.event_ids,
                    )
        return tuple(windows)

    @staticmethod
    def _effect_four_intervals(
        insight_windows: Sequence[_InsightWindow],
        hits: Sequence[BattleAnalysisHit],
        *,
        character_name: str,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        result: list[BattleInferredBuffInterval] = []
        ordered_hits = tuple(sorted(hits, key=lambda row: row.relative_time_us))
        for hit in ordered_hits:
            if hit.gameplay_effect_id.casefold() != _BASE_TOPPLE_EFFECT:
                continue
            active_at = project_timeline_time_us(
                hit.relative_time_us,
                battle_start_us=0,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
            )
            window = next((
                row for row in reversed(insight_windows)
                if row.target_id == hit.target_id
                and row.start_active_us <= active_at < row.end_active_us
            ), None)
            if window is None:
                continue
            cluster_end = max(
                (
                    row.relative_time_us + 1
                    for row in ordered_hits
                    if row.target_id == hit.target_id
                    and hit.relative_time_us <= row.relative_time_us
                    <= hit.relative_time_us + _TOPPLE_SETTLEMENT_CLUSTER_US
                    and classify_battle_hit_channel(row)[0] in {
                        "other_topple", "special_daffodill_extra_topple",
                    }
                ),
                default=hit.relative_time_us + 1,
            )
            result.append(BattleInferredBuffInterval(
                interval_id=f"buff:daffodill:effect4:{hit.event_id}",
                buff_asset_path="character_awaken:1054:Effect4",
                buff_name=f"통찰·브레이크 피해 증가 ({window.stacks}중첩)",
                source_effect_definition_id="character_awaken:1054:Effect4",
                source_kind="confirmed_character_awakening_state",
                source_character_id=DAFFODILL_CHARACTER_ID,
                source_character_name=character_name,
                target_scope=f"character:{DAFFODILL_CHARACTER_ID}",
                start_us=hit.relative_time_us,
                end_us=cluster_end,
                stacks=window.stacks,
                duration_policy="ObservedToppleSettlementCluster",
                state_confidence="中",
                value_confidence="高",
                inference_basis=(
                    "Q 동작은 같은 대상에 통찰을 최대 2중첩까지 부여합니다; 4각은 중첩당,"
                    "다포딜 본인의 브레이크 피해만 높입니다. 구간은 축에서 관측된 같은 대상의 브레이크 정산 묶음만 포함합니다."
                ),
                trigger_event_type="INFERRED_DAFFODILL_INSIGHT_TOPPLE",
                evidence_action_ids=window.action_ids,
                evidence_event_ids=tuple(dict.fromkeys((
                    *window.event_ids, hit.event_id,
                ))),
                modifiers=(_modifier(
                    "UnbalDamageUp",
                    0.15 * window.stacks,
                    requirement=f"battle-hit-target|id={hit.target_id}",
                ),),
                stacking_type="AggregateByTarget",
                stack_limit_count=2,
                target_id=hit.target_id,
            ))
        return tuple(result)

    @staticmethod
    def _resonance_six_intervals(
        hits: Sequence[BattleAnalysisHit],
        *,
        character_name: str,
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
        topple_duration_us: int,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        result: list[BattleInferredBuffInterval] = []
        for hit in hits:
            if hit.gameplay_effect_id.casefold() != _BASE_TOPPLE_EFFECT:
                continue
            start_us = hit.relative_time_us + 1
            start_active = project_timeline_time_us(
                start_us,
                battle_start_us=0,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
            )
            end_us = unproject_timeline_time_us(
                start_active + topple_duration_us,
                battle_start_us=0,
                battle_end_us=battle_end_us,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
                prefer_interval_end=True,
            )
            if end_us <= start_us:
                continue
            result.append(BattleInferredBuffInterval(
                interval_id=f"buff:daffodill:resonance6:{hit.event_id}",
                buff_asset_path="character_awaken:1054:resonance_6",
                buff_name="6각 공명·암속성 저항 감소",
                source_effect_definition_id="character_awaken:1054:resonance_6",
                source_kind="confirmed_character_awakening_state",
                source_character_id=DAFFODILL_CHARACTER_ID,
                source_character_name=character_name,
                target_scope="target",
                start_us=start_us,
                end_us=end_us,
                stacks=1,
                duration_policy="ReliableToppleDurationActiveClock",
                state_confidence="中",
                value_confidence="高",
                inference_basis=(
                    "일반 각성 6개가 활성화되었습니다; 관측된 브레이크 정산 1마이크로초 후부터,"
                    "플레이 설정에서 검증 가능한 불균형 상한/회복 속도에 따라 지속되며 시간 정지는 유효 시간을 소모하지 않습니다."
                ),
                trigger_event_type="INFERRED_DAFFODILL_RESONANCE_SIX_TOPPLE",
                evidence_action_ids=(),
                evidence_event_ids=(hit.event_id,),
                modifiers=(_modifier("DamageResistChaosBase", -0.15),),
                stacking_type="AggregateByTarget",
                stack_limit_count=1,
                target_id=hit.target_id,
            ))
        return tuple(result)

    @staticmethod
    def _effect_five_intervals(
        insight_windows: Sequence[_InsightWindow],
        hits: Sequence[BattleAnalysisHit],
        *,
        character_name: str,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        result: list[BattleInferredBuffInterval] = []
        for hit in hits:
            if hit.gameplay_effect_id.casefold() != _BASE_TOPPLE_EFFECT:
                continue
            active_at = project_timeline_time_us(
                hit.relative_time_us,
                battle_start_us=0,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
            )
            window = next((
                row for row in reversed(insight_windows)
                if row.target_id == hit.target_id
                and row.start_active_us <= active_at < row.end_active_us
            ), None)
            if window is None:
                continue
            result.append(BattleInferredBuffInterval(
                interval_id=f"derived:daffodill:effect5:{hit.event_id}",
                buff_asset_path=DAFFODILL_EFFECT_FIVE_DEFINITION_ID,
                buff_name=f"완벽한 진실·후보 추가 정산 ({window.stacks}중첩)",
                source_effect_definition_id=DAFFODILL_EFFECT_FIVE_DEFINITION_ID,
                source_kind="candidate_derived_awakening_settlement",
                source_character_id=DAFFODILL_CHARACTER_ID,
                source_character_name=character_name,
                target_scope=f"character:{DAFFODILL_CHARACTER_ID}",
                start_us=hit.relative_time_us,
                end_us=hit.relative_time_us + 2,
                stacks=window.stacks,
                duration_policy="InstantDerivedSettlementPerInsightStack",
                state_confidence="中",
                value_confidence="高",
                inference_basis=(
                    "후보 설정에서 5각을 활성화했습니다; 0각 기본 통찰은 이미 1회 추가 정산되었고, 5각은 원본 축의 Q"
                    "동작이 같은 대상에 부여한 통찰 중첩마다 1회씩 더 추가합니다. 따라서 1중첩이면 총 2회,"
                    "3각으로 2중첩까지 쌓이면 총 3회입니다; 파생 행은 기본 대비 초과분만 나타내며 원본 히트에 기록되지 않습니다."
                ),
                trigger_event_type="CANDIDATE_DAFFODILL_EFFECT_FIVE_SETTLEMENT",
                evidence_action_ids=window.action_ids,
                evidence_event_ids=tuple(dict.fromkeys((
                    *window.event_ids, hit.event_id,
                ))),
                modifiers=(),
                stacking_type="AggregateByTarget",
                stack_limit_count=2,
                target_id=hit.target_id,
            ))
        return tuple(result)
