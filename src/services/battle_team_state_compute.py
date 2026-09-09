# 将角色与环境状态计划交给 Rust，Python 只恢复既有区间证据文案。
from __future__ import annotations

from dataclasses import asdict

from src.domain.battle_report import BattleInferredBuffInterval


def _compute(backend, kind, *, hits, battle_end_us, time_stop_intervals, checkpoint, **values):
    needs_channel = kind == "daffodill" and "Effect4" in values.get("effects", ())
    payload = {
        "kind": kind, "hits": [{
            "event_id": hit.event_id, "relative_time_us": hit.relative_time_us,
            "sequence": hit.sequence, "target_id": hit.target_id, "direction": hit.direction,
            "gameplay_effect_id": hit.gameplay_effect_id,
            "channel_input": {key: getattr(hit, key) for key in (
                "direction", "gameplay_effect_id", "classification", "is_follow_up",
                "damage_name", "damage_component", "attack_type", "ability_id", "skill_name",
            )} if needs_channel else {},
        } for hit in hits],
        "battle_end_us": battle_end_us, "time_stop_intervals": time_stop_intervals,
        "topple_duration_us": None, "topple_limit": None, "topple_recovery_speed": None, **values,
    }
    result = backend.compute_batch("team_state_v1", (payload,), checkpoint=checkpoint)
    if len(result) != 1 or not isinstance(result[0].get("intervals"), list):
        raise ValueError("Native team state result shape mismatch")
    return result[0]["intervals"]


def linko_intervals(backend, *, inferences, hits, battle_end_us, time_stop_intervals, character_name, checkpoint=None):
    from src.domain.battle_report import BattleBuffModifierEvidence
    from src.services.battle_linko_coattack_buff_service import _ELEMENT_ASSET_SUFFIXES, _ELEMENT_LABELS, _PASSIVE_ID
    plans = _compute(backend, "linko", hits=hits, battle_end_us=battle_end_us,
                     time_stop_intervals=time_stop_intervals, checkpoint=checkpoint,
                     inferences=[asdict(row) for row in inferences])
    result = []
    for row in plans:
        inference = inferences[row["index"]]
        target, element = row["target_id"], row["element"]
        result.append(BattleInferredBuffInterval(
            interval_id=f"buff:linko:precision-tuning:{target}:{element}:{inference.qte_action_id or inference.event_id}",
            buff_asset_path="/Game/Blueprints/Abilities/Player/Ability_072_Radio/PassiveEffect/Passive3/"
                            f"Buff_Radio072_Passive3_{_ELEMENT_ASSET_SUFFIXES[element]}",
            buff_name=f"精确调频·{_ELEMENT_LABELS[element]}속성 저항 감소",
            source_effect_definition_id=_PASSIVE_ID, source_kind="derived_linko_coattack_inference",
            source_character_id=1072, source_character_name=character_name, target_scope="target",
            start_us=row["start_us"], end_us=row["end_us"], stacks=1,
            duration_policy="RefreshWholeStackActiveClock12Seconds",
            state_confidence=inference.confidence, value_confidence="高",
            inference_basis=(
                "공식 패시브 확인 결과, 동조 합공은 발동 캐릭터의 속성에 대한 대상의 이능력 저항을 8% 낮추며, 12초 동안 지속되고 같은 속성은 갱신만 됩니다; "
                "실제 전투 리포트 검증은 해당 동조 합공을 발동한 첫 히트가 즉시 저항 감소를 소비함을 뒷받침합니다; 발동 시점과 속성은 버전 관리된 동조 합공 "
                f"추론({inference.confidence})에서 가져온 것이며, Core 네이티브 상태 이벤트가 아닙니다."
            ),
            trigger_event_type="INFERRED_LINKO_COATTACK",
            evidence_action_ids=tuple(value for value in (inference.trigger_action_id, inference.qte_action_id) if value),
            evidence_event_ids=inference.evidence_event_ids,
            modifiers=(BattleBuffModifierEvidence(
                property_id=f"DamageResist{element.title()}Base", modifier_operation="EGameplayModOp::Additive",
                magnitude_kind="confirmed_character_passive", magnitude_value=-0.08,
                calculation_asset_path="", value_confidence="高",
            ),), stacking_type="AggregateByTarget+RefreshWholeStack", stack_limit_count=1, target_id=target,
        ))
    return tuple(result)


def outer_intervals(backend, config, *, hits, battle_end_us, time_stop_intervals, checkpoint=None):
    from src.services.battle_outer_realm_buff_service import BattleOuterRealmBuffService, _modifier
    plans = _compute(backend, "outer", hits=hits, battle_end_us=battle_end_us,
                     time_stop_intervals=time_stop_intervals, checkpoint=checkpoint,
                     components=[asdict(row) for row in config.components],
                     topple_limit=config.topple_limit, topple_recovery_speed=config.topple_recovery_speed)
    result = []
    for row in plans:
        component = config.components[row["component"]]
        requirement = ""
        if row["kind"] == "whole":
            suffix, trigger = str(component.component_ordinal), "OUTER_REALM_WHOLE_BATTLE"
            basis = "정식 궤외 시즌 설정은 전투 전체에 적용되도록 선언되어 있으며, 수치는 공식 단일값 곡선에서 가져옵니다."
        elif row["kind"] == "stack":
            suffix, trigger = f"stack:{row['ordinal']}", "CORRUPTION_DAMAGE_AFTER_HIT"
            basis = ("정식 스코치 히트마다 피해 정산 후 중첩을 쌓습니다; 1초 발동 간격·6초 전체 갱신"
                     "·최대 8중첩은 모두 시즌 설명에서 가져오며, 지속 시간은 시간 정지 차감 시계를 사용합니다.")
        elif row["kind"] == "topple":
            hit = hits[row["index"]]
            suffix, trigger = f"topple:{hit.event_id}", "TARGET_TOPPLED"
            requirement = f"battle-target|id={hit.target_id}"
            basis = (f"{hit.gameplay_effect_id}이(가) 대상의 브레이크 진입을 증명합니다; 해당 대상의 정식 "
                     f"UnbalMax={float(config.topple_limit):g} ÷ UnbalReduceReset={float(config.topple_recovery_speed):g}에 따라, "
                     "시간 정지 차감 시계 위에 브레이크 회복 구간을 재구성합니다.")
        else:
            raise ValueError("Unknown native outer state kind")
        result.append(BattleOuterRealmBuffService._base_interval(
            config, component, interval_id=f"outer:{config.level_config_id}:{suffix}",
            start_us=row["start_us"], end_us=row["end_us"], stacks=row["stacks"],
            trigger_event_type=trigger, evidence_event_ids=tuple(row["event_ids"]),
            modifier=_modifier(component, requirement=requirement), inference_basis=basis,
        ))
    return tuple(sorted(result, key=lambda row: (row.start_us, row.end_us, row.interval_id)))


def daffodill_intervals(
    backend, *, actions, hits, battle_end_us, time_stop_intervals, effects,
    character_name, topple_duration_us, checkpoint=None,
):
    from src.services.battle_daffodill_awakening_service import _modifier
    plans = _compute(backend, "daffodill", hits=hits, battle_end_us=battle_end_us,
                     time_stop_intervals=time_stop_intervals, checkpoint=checkpoint,
                     actions=[asdict(row) for row in actions], effects=sorted(effects),
                     topple_duration_us=topple_duration_us)
    result = []
    for row in plans:
        kind, stacks = row["kind"], row["stacks"]
        options = {
            "source_kind": "confirmed_character_awakening_state", "target_scope": "character:1054",
            "target_id": row.get("target_id", ""), "stack_limit_count": 2,
        }
        if kind == "qte":
            action = actions[row["index"]]
            identity = "character-kit:1054:qte-e-enhancement"
            definition = "character_awaken:1054:Effect1" if "Effect1" in effects else identity
            name, interval_id = f"탈바꿈·E 강화 ({stacks}중첩)", f"buff:daffodill:qte-e:{action.action_id}"
            policy, trigger = "ConsumeAllOnEAction", "INFERRED_DAFFODILL_QTE_CONSUMED_BY_E"
            basis = ("추정된 QTE마다 1중첩씩 최대 2중첩까지 누적되며, 다음 추정된 E가 전부 소모합니다;"
                     "고정 축은 E 통용 피해만 투영하며 불균형 게이지와 브레이크 시점은 역추정하지 않습니다.")
            modifiers = (_modifier("DamageUpGeneralBase", row["value"], source_require_tags=("State.Damage.Skill",)),)
            options["source_kind"] = "confirmed_character_action_resource"
        elif kind == "effect4":
            hit = hits[row["index"]]
            identity = definition = "character_awaken:1054:Effect4"
            name, interval_id = f"통찰·브레이크 피해 증가 ({stacks}중첩)", f"buff:daffodill:effect4:{hit.event_id}"
            policy, trigger = "ObservedToppleSettlementCluster", "INFERRED_DAFFODILL_INSIGHT_TOPPLE"
            basis = ("Q 동작은 같은 대상에 통찰을 최대 2중첩까지 부여합니다; 4각은 중첩당,"
                     "다포딜 본인의 브레이크 피해만 높입니다. 구간은 축에서 관측된 같은 대상의 브레이크 정산 묶음만 포함합니다.")
            modifiers = (_modifier("UnbalDamageUp", row["value"], requirement=f"battle-hit-target|id={hit.target_id}"),)
        elif kind == "effect5":
            hit = hits[row["index"]]
            identity = definition = "character_awaken:1054:Effect5"
            name, interval_id = f"완벽한 진실·후보 추가 정산 ({stacks}중첩)", f"derived:daffodill:effect5:{hit.event_id}"
            policy, trigger = "InstantDerivedSettlementPerInsightStack", "CANDIDATE_DAFFODILL_EFFECT_FIVE_SETTLEMENT"
            basis = ("후보 설정에서 5각을 활성화했습니다; 0각 기본 통찰은 이미 1회 추가 정산되었고, 5각은 원본 축의 Q"
                     "동작이 같은 대상에 부여한 통찰 중첩마다 1회씩 더 추가합니다. 따라서 1중첩이면 총 2회,"
                     "3각으로 2중첩까지 쌓이면 총 3회입니다; 파생 행은 기본 대비 초과분만 나타내며 원본 히트에 기록되지 않습니다.")
            modifiers = ()
            options["source_kind"] = "candidate_derived_awakening_settlement"
        elif kind == "resonance6":
            hit = hits[row["index"]]
            identity = definition = "character_awaken:1054:resonance_6"
            name, interval_id = "6각 공명·암속성 저항 감소", f"buff:daffodill:resonance6:{hit.event_id}"
            policy, trigger = "ReliableToppleDurationActiveClock", "INFERRED_DAFFODILL_RESONANCE_SIX_TOPPLE"
            basis = ("일반 각성 6개가 활성화되었습니다; 관측된 브레이크 정산 1마이크로초 후부터,"
                     "플레이 설정에서 검증 가능한 불균형 상한/회복 속도에 따라 지속되며 시간 정지는 유효 시간을 소모하지 않습니다.")
            modifiers = (_modifier("DamageResistChaosBase", -0.15),)
            options.update(target_scope="target", stack_limit_count=1)
        else:
            raise ValueError("Unknown native Daffodill state kind")
        result.append(BattleInferredBuffInterval(
            interval_id=interval_id, buff_asset_path=identity, buff_name=name,
            source_effect_definition_id=definition, source_character_id=1054, source_character_name=character_name,
            start_us=row["start_us"], end_us=row["end_us"], stacks=stacks, duration_policy=policy,
            state_confidence="中", value_confidence="高", inference_basis=basis, trigger_event_type=trigger,
            evidence_action_ids=tuple(row["action_ids"]), evidence_event_ids=tuple(row["event_ids"]),
            modifiers=modifiers, stacking_type="AggregateByTarget", **options,
        ))
    return tuple(sorted(result, key=lambda row: (row.start_us, row.interval_id)))
