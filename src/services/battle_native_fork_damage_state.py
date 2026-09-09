# 传递冻结弧盘状态事实，按原生区间描述恢复中文依据与原始修饰项。
from __future__ import annotations


from src.services.battle_state_payload import HIT_FIELDS, ACTION_FIELDS, state_rows
from src.domain.battle_report import BattleBuffModifierEvidence

_RULE_FIELDS = (
    "event_type", "source_character_id", "target_asset_path", "duration_seconds",
    "cooldown_seconds", "stack_limit_count",
)
_BASIS = {
    "tiger": "E 또는 Q 시작마다 일반 공격 및 극한 반격 피해 증가 1중첩 추가; 각 중첩은 유효 전투 시간 15초 동안 독립 지속하며, 히트 시 최대 2중첩까지 적용합니다.",
    "commander": "E 실제 종료 시 左虎符, Q begin 시 右虎符를 획득하며 유효 전투 시간 15초 안에 둘을 모아야 함;"
                 "두 번째 정식 虎符가 도착하면 즉시 유효 전투 시간 10초의 司令虎符 구간을 만듭니다.",
    "rose": "지속 피해는 0.3초마다 최대 1중첩; E 시작 시 즉시 10중첩까지 채웁니다.",
    "moon": "정식 혼속성 피해 정산마다 1중첩 추가, 0.1초당 최대 1중첩;"
            "각 중첩은 유효 전투 시간 5초 동안 독립 지속하며, 최대 10중첩까지 적용합니다.",
    "time": "E로 荒时迷宫을 만들고 0으로 초기화; 팀원의 E/QTE로 荒时 누적;"
            "이번 Q는 荒时 {stacks}중첩을 소모하며, 치명 피해 강화는 해당 Q에만 적용됩니다.",
    "time_defense": "이번 Q는 荒时 3중첩을 한 번에 소모하며, Q 시작 시점부터"
                    "장착자의 모든 피해에 적용되는 방어 무시를 얻어 유효 전투 시간 70초 동안 지속됩니다.",
    "spider": "일반 공격은 0.5초마다 최대 1중첩의 「蜘识」 획득; Q는 {stacks}중첩을 소모하며, 8중첩일 때 팀 전체 공격력을 추가로 얻습니다.",
}


def infer_native_fork_damage(
    backend, rules, *, actions, hits, battle_end_us, time_stop_intervals, checkpoint=None,
):
    from src.services.battle_fork_damage_state_service import _interval

    payload = {
        "rules": [
            {**{key: getattr(rule, key) for key in _RULE_FIELDS},
             "modifiers": [{"property_id": modifier.property_id,
                            "magnitude_value": modifier.magnitude_value}
                           for modifier in rule.modifiers]}
            for rule in rules
        ],
        "actions": state_rows(actions, ACTION_FIELDS),
        "hits": state_rows(hits, HIT_FIELDS),
        "battle_end_us": battle_end_us,
        "time_stop_intervals": list(time_stop_intervals),
    }
    responses = backend.compute_batch("fork_damage_state_v1", (payload,), checkpoint=checkpoint)
    if len(responses) != 1:
        raise ValueError("Native fork damage response count mismatch")
    results = []
    for ordinal, descriptor in enumerate(responses[0]["intervals"]):
        if checkpoint is not None and ordinal % 64 == 0:
            checkpoint()
        rule_index = descriptor["rule_index"]
        if type(rule_index) is not int or not 0 <= rule_index < len(rules):
            raise ValueError("Native fork damage rule index mismatch")
        rule = rules[rule_index]
        kind = descriptor["modifier_kind"]
        modifiers = None
        if kind:
            if kind == "time":
                crit = tuple(row for row in rule.modifiers if row.property_id == "CritDamageBase")
                template = crit[1]
            elif kind == "spider":
                template = rule.modifiers[0]
            else:
                raise ValueError("Unsupported native fork modifier descriptor")
            modifier = BattleBuffModifierEvidence(
                property_id="CritDamageBase" if kind == "time" else "AtkUp",
                modifier_operation=template.modifier_operation,
                magnitude_kind=template.magnitude_kind,
                magnitude_value=descriptor["modifier_value"],
                calculation_asset_path=template.calculation_asset_path,
                value_confidence=template.value_confidence,
                source_require_tags=template.source_require_tags if kind == "time" else (),
            )
            modifiers = (crit[0], modifier) if kind == "time" else (modifier,)
        row = _interval(
            rule, suffix=descriptor["suffix"], start_us=descriptor["start_us"],
            end_us=descriptor["end_us"], stacks=descriptor["stacks"],
            basis=_BASIS[descriptor["basis_kind"]].format(stacks=descriptor["basis_stacks"]),
            action_ids=descriptor["action_ids"], event_ids=descriptor["event_ids"],
            modifiers=modifiers,
        )
        if row is None:
            raise ValueError("Native fork damage emitted an empty interval")
        results.append(row)
    return tuple(results)
