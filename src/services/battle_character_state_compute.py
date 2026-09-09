# 调度角色状态纯计算并恢复法帝娅生命上限区间的不可变证据。
from __future__ import annotations

from collections.abc import Callable
from math import isfinite
from typing import Any

from src.domain.battle_report import BattleBuffModifierEvidence, BattleInferredBuffInterval
from src.domain.native_analysis import BattleComputeBackend


def compute_character_state(
    kind: str, inputs: dict[str, Any], *,
    backend: BattleComputeBackend | None, checkpoint: Callable[[], None] | None,
) -> dict[str, Any] | None:
    if not isinstance(backend, BattleComputeBackend) or not backend.supports_battle_compute:
        return None
    if checkpoint:
        checkpoint()
    rows = backend.compute_batch("character_state_v1", ({**inputs, "kind": kind},),
                                 checkpoint=checkpoint)
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("invalid_character_state_result")
    return rows[0]


def restore_fadia_intervals(result: dict[str, Any]) -> tuple[BattleInferredBuffInterval, ...]:
    rows = result.get("intervals")
    if not isinstance(rows, list):
        raise ValueError("invalid_character_state_result")
    restored = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_character_state_result")
        start, end, ordinal, event, half, value, observed = (
            row.get("start_us"), row.get("end_us"), row.get("ordinal"),
            row.get("event_id"), row.get("half"), row.get("stack_hp"), row.get("observed"),
        )
        if (
            type(start) is not int or type(end) is not int or start >= end
            or type(ordinal) is not int or ordinal <= 0
            or not isinstance(event, str) or not isinstance(half, str)
            or type(value) not in (int, float) or not isfinite(value) or value <= 0
            or type(observed) is not bool
        ):
            raise ValueError("invalid_character_state_result")
        restored.append(BattleInferredBuffInterval(
            interval_id=f"fadia:dark-star-hp:{ordinal}:{event}",
            buff_asset_path="confirmed:character_passive:1039:dark-star-hp",
            buff_name="파디아 패시브: HP 상한 흡수",
            source_effect_definition_id="character_passive:1039:GA_Fadia_Passive_1",
            source_kind="confirmed_character_text_and_formal_hit",
            source_character_id=1039, source_character_name="法帝娅", target_scope="team",
            start_us=start, end_us=end, stacks=1, duration_policy="until_battle_end",
            state_confidence="高", value_confidence="高" if observed else "中",
            inference_basis=(
                "정식 Buff_Reaction_4_new 히트가 노바 정산 완료를 나타냄;"
                "정식 HTExtractAttributeGEComp는 출처의 당시 MAXHP 10%만큼"
                "팀 전체의 HP 상한을 올리며, 최대 5회;"
                + (f"{half} 하프별로 독립 누적하고 하프 전환 시 초기화;" if half else "")
                + ("이번 대상의 최대 HP 정식 감소값을 200%로 역산해 출처의 당시 MAXHP를 구합니다."
                   if observed else "이번 대상 감소값이 없어 고정 PanelHP, 3각성, 이 메커니즘의 이전 중첩 수로 순차 추정합니다.")
            ),
            trigger_event_type="FORMAL_DARK_STAR_SETTLED", evidence_action_ids=(),
            evidence_event_ids=(event,),
            modifiers=(BattleBuffModifierEvidence(
                property_id="HPMaxAdd", modifier_operation="EGameplayModOp::Additive",
                magnitude_kind="derived:fadia_source_current_max_hp*0.10", magnitude_value=value,
                calculation_asset_path="", value_confidence="高" if observed else "中",
            ),),
            stacking_type="AggregateBySource", stack_limit_count=5,
        ))
    return tuple(restored)


def state_ranges(result: dict[str, Any], name: str) -> tuple[tuple[int, int], ...]:
    rows = result.get(name)
    if not isinstance(rows, list) or not all(
        isinstance(row, list) and len(row) == 2 and all(type(value) is int for value in row)
        for row in rows
    ):
        raise ValueError("invalid_character_state_result")
    return tuple((row[0], row[1]) for row in rows)


def state_ids(result: dict[str, Any], name: str) -> tuple[str, ...]:
    values = result.get(name)
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("invalid_character_state_result")
    return tuple(values)
