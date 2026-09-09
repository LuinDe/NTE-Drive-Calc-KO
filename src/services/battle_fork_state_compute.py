# 调度弧盘专属状态批量计算并恢复原有区间证据和展示文本。
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from src.services.battle_state_payload import HIT_FIELDS, ACTION_FIELDS, RULE_FIELDS, EVENT_FIELDS, state_rows
from src.domain.battle_report import BattleInferredBuffInterval
from src.domain.native_analysis import BattleComputeBackend


_BASIS = {
    "damage_stack": (
        "히트 단위 순방향 리플레이: 주속성 직접 피해·DOT·스코치가 피해 정산 후 중첩을 쌓습니다;"
        "0.3초 재사용 대기시간과 15초 지속 시간 모두 시간 정지를 제외한 시계를 사용합니다."
    ),
    "wush_main": (
        "Q 시작 시 곧바로 E/Q 피해 증가가 생성되며, 이를 발동한 이번 Q에도 적용;"
        "지속 시간은 시간 정지를 제외한 시계로 진행하며, 창 안에서 다시 Q를 써도 갱신되지 않습니다."
    ),
    "wush_stack": (
        "E는 스킬이 실제로 끝난 후 1중첩 추가; 이번 E는"
        "시전 전 중첩 수만 읽으며, 최대 2중첩입니다."
    ),
    "gold_record": (
        "장착자의 독립 QTE 시작 이벤트마다 Q 치명 피해 1중첩만 추가;"
        "QTE의 히트 수만큼 중첩이 복제되지는 않습니다."
    ),
    "gold_record_final": (
        "장착자의 독립 QTE 시작 이벤트마다 Q 치명 피해 1중첩만 추가;"
        "QTE의 히트 수만큼 중첩이 복제되지는 않으며, 해당 QTE 시작 시점부터 적용됩니다."
    ),
    "door": "통합 출처 측 치료 이벤트 기준으로 갱신; HP가 가득 찼거나 유효 치료가 0이어도 발동 가능.",
    "bit_base": (
        "직접 A/E/Q/QTE 동작 전환 출처로 출전/대기 상태를 재구성; 장착자가 출전하거나 빠질"
        "때 해당 상태와 중첩 수를 즉시 초기화합니다."
    ),
    "bit_stack": (
        "재사용 대기시간이 허용한 피해 시점마다 1중첩만 추가; 같은 시점의 다중 대상은"
        "중복 계산하지 않고, 새 중첩은 해당 타격 정산 1마이크로초 후 시작하며, 상태 전환 시 초기화됩니다."
    ),
    "bitter": (
        "정적 발동은 피격 계산 전; 구간은 이번 피격 시점부터 시작하므로 이를 발동한"
        "이번 공격은 이미 방어력 증가를 읽으며, 출처 측 20초 재사용 대기시간이 적용됩니다."
    ),
    "blast": (
        "Q 시작 시 곧바로 공격력 증가를 얻으며, 이를 발동한 이번 Q에도 적용;"
        "반복 발동 시 갱신만 되고 중첩되지 않습니다."
    ),
    "butterfly": (
        "Q 시작 시 즉시 부착물 피해 증가를 강화 단계로 교체하며, 발동한 Q 동안의 부착물"
        "피해에 적용; Q를 반복하면 6초 창만 갱신합니다."
    ),
    "after_e": (
        "E 스킬이 실제로 끝난 1마이크로초 후 적용되며, 이를 발동한 이번 E에는 적용되지 않음;"
        "같은 이름의 효과는 중첩되지 않으며, E를 반복하면 지속 시간만 갱신합니다."
    ),
    "gold_wool": (
        "Q 시작 시 즉시 적용되고 이번 Q에도 적용; E 실제 종료 1마이크로초 후 적용되며,"
        "이번 E에는 적용되지 않음; 어느 쪽이 발동하든 같은 20초 창만 갱신합니다."
    ),
    "knight": (
        "히트별 공식 리플레이가 판정한 치명 근거를 소비; 새 중첩은 발동 타격 1마이크로초 후 적용되며,"
        "출처 측 0.3초 재사용 대기시간, 최대 10중첩이며 전체 그룹의 10초 지속 시간을 갱신합니다."
    ),
    "lunar": (
        "Q 시작 시 즉시 빛속성 피해 증가와 방어 무시를 얻으며, 이번 Q에도 적용;"
        "효과는 중첩되지 않으며, Q를 반복하면 20초 지속 시간을 갱신합니다."
    ),
    "motor": (
        "출전 시 0중첩에서 시작하며, 1초가 온전히 지나야 첫 중첩 획득;"
        "전투 리포트 원본 시간 기준으로 주기적 중첩을 계속하고, 시간 정지 중에도 멈추지 않으며, 최대 5중첩;"
        "출전에서 빠지면 즉시 초기화되고, 다시 출전하면 처음부터 다시 계산합니다."
    ),
    "nest": (
        "Q 동작에 바인딩된 실제 히트와 그 target_id만 소비; 표식은"
        "발동 타격 1마이크로초 후 적용되고, 같은 대상은 20초 갱신, 다중 대상은 독립입니다."
    ),
}


def compute_fork_intervals(
    rules: Sequence[Any],
    *,
    actions: Sequence[Any],
    hits: Sequence[Any],
    battle_end_us: int,
    time_stop_intervals: Sequence[tuple[int | None, int | None]],
    treatment_events: Sequence[Any],
    critical_events: Sequence[Any],
    backend: BattleComputeBackend | None,
    checkpoint: Callable[[], None] | None,
) -> tuple[BattleInferredBuffInterval, ...] | None:
    if not isinstance(backend, BattleComputeBackend) or not backend.supports_battle_compute:
        return None
    if checkpoint:
        checkpoint()
    inputs = {
        "rules": state_rows(rules, RULE_FIELDS),
        "actions": state_rows(actions, ACTION_FIELDS),
        "hits": state_rows(hits, HIT_FIELDS),
        "battle_end_us": battle_end_us,
        "time_stop_intervals": list(time_stop_intervals),
        "treatment_events": state_rows(treatment_events, EVENT_FIELDS),
        "critical_events": state_rows(critical_events, EVENT_FIELDS),
    }
    responses = backend.compute_batch("fork_state_v1", (inputs,), checkpoint=checkpoint)
    if len(responses) != 1 or not isinstance(responses[0].get("intervals"), list):
        raise ValueError("invalid_fork_state_result")
    results = []
    for row in responses[0]["intervals"]:
        if checkpoint:
            checkpoint()
        results.append(_restore(rules, row))
    return tuple(results)


def _restore(rules: Sequence[Any], row: Any) -> BattleInferredBuffInterval:
    if not isinstance(row, dict):
        raise ValueError("invalid_fork_state_result")
    index, start, end, stacks = (
        row.get("rule_index"), row.get("start_us"), row.get("end_us"), row.get("stacks"),
    )
    kind, suffix, basis, scope = (
        row.get("kind"), row.get("suffix"), row.get("basis_key"), row.get("target_scope"),
    )
    actions, events = row.get("action_ids"), row.get("event_ids")
    if (
        type(index) is not int or not 0 <= index < len(rules)
        or type(start) is not int or type(end) is not int or end <= start
        or type(stacks) is not int or stacks <= 0
        or kind not in ("fork", "fork-state", "fork-trigger", "fork-periodic", "damage-stack")
        or not isinstance(suffix, str) or not isinstance(basis, str) or basis not in _BASIS
        or (scope is not None and not isinstance(scope, str))
        or not isinstance(actions, list) or not isinstance(events, list)
        or not all(isinstance(item, str) for item in (*actions, *events))
    ):
        raise ValueError("invalid_fork_state_result")
    rule = rules[index]
    return BattleInferredBuffInterval(
        interval_id=f"buff:{kind}:{suffix}:{rule.rule_id}",
        buff_asset_path=rule.target_asset_path,
        buff_name=rule.target_name,
        source_effect_definition_id=rule.source_effect_definition_id,
        source_kind=rule.source_kind,
        source_character_id=rule.source_character_id,
        source_character_name=rule.source_character_name,
        target_scope=scope or rule.target_scope,
        start_us=start, end_us=end, stacks=stacks,
        duration_policy=rule.duration_policy,
        state_confidence="中", value_confidence="高",
        inference_basis=_BASIS[basis],
        trigger_event_type=rule.event_type,
        evidence_action_ids=tuple(actions), evidence_event_ids=tuple(events),
        modifiers=rule.modifiers, stacking_type=rule.stacking_type,
        stack_limit_count=rule.stack_limit_count,
    )
