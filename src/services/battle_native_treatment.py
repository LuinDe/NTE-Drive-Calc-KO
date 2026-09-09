# 将冻结养成与整场动作逐击送入治疗计算核，并恢复公开领域证据对象。
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from src.domain.battle_report import (
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
    BattleTreatmentEvent,
)
from src.domain.native_analysis import BattleComputeBackend
from src.services.battle_treatment_native_text import TREATMENT_BUFF_TEXT, TREATMENT_TEXT


def _character(row: Mapping) -> dict:
    profile = row.get("profile") or {}
    profile = profile if isinstance(profile, Mapping) else {}
    if bool(profile.get("awakening_selection_initialized")):
        selected = {str(value).casefold() for value in profile.get("selected_awaken_effect_ids") or ()}
        effects = [f"Effect{index}" for index in range(1, 7) if f"effect{index}" in selected]
    else:
        awakening = int(row.get("awakening_level") or profile.get("awakening_level") or 0)
        effects = [f"Effect{index}" for index in range(1, 7) if awakening >= index]
    return {
        "id": int(row.get("character_id") or 0),
        "name": str(row.get("observed_name") or ""),
        "stage": int(row.get("breakthrough_stage") or profile.get("breakthrough_stage") or 0),
        "effects": effects,
        "stats": [
            (str(stat.get("property_id") or ""), str(stat.get("source_group") or ""),
             float(stat.get("value") or 0.0))
            for stat in row.get("stats") or ()
        ],
    }


def _event(payload: dict) -> BattleTreatmentEvent:
    payload = dict(payload)
    arguments = payload.pop("amount_arguments")
    kind = payload["treatment_kind"]
    text = dict(TREATMENT_TEXT[kind])
    if kind == "lacrimosa_effect5_period":
        text["amount_basis"] = f"floor({arguments['damage']:g} × 0.015)"
    elif kind == "shinku_effect5_rage_e":
        base = arguments["base_attack"]
        text["amount_basis"] = "GetAtkBase × 300%" if base is None else f"{base:g} × 300%"
    elif kind == "zankou_effect3_huo_period":
        maximum, ratio = arguments["maximum_health"], arguments["recover_ratio"]
        text["amount_basis"] = (
            f"잔홍 정산 시 최대 HP × {ratio:g}"
            if maximum is None else f"{maximum:g} × {ratio:g}"
        )
    payload["evidence_event_ids"] = tuple(payload["evidence_event_ids"])
    payload["target_character_ids"] = tuple(payload["target_character_ids"])
    return BattleTreatmentEvent(**payload, **text)


def _buff(payload: dict) -> BattleInferredBuffInterval:
    payload = dict(payload)
    kind, ordinal = payload.pop("kind"), payload.pop("ordinal")
    text = dict(TREATMENT_BUFF_TEXT[kind])
    modifier = BattleBuffModifierEvidence(
        property_id=text.pop("property_id"),
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind=text.pop("magnitude_kind"),
        magnitude_value=payload.pop("amount"),
        calculation_asset_path=text.pop("calculation_asset_path"),
        value_confidence="高",
    )
    for key in ("evidence_action_ids", "evidence_event_ids"):
        payload[key] = tuple(payload[key])
    return BattleInferredBuffInterval(
        **payload, **text,
        interval_id=f"buff:treatment:{kind}:{ordinal}",
        source_kind="formal_treatment_consumer", source_character_id=1075,
        stacks=1, duration_policy="HasDuration", state_confidence="中",
        value_confidence="高", modifiers=(modifier,),
        stacking_type="AggregateByTarget", stack_limit_count=1,
    )


def infer_treatment_batch(
    *,
    build: Mapping | None,
    actions: Sequence,
    hits: Sequence,
    battle_end_us: int,
    time_stop_intervals: Sequence,
    state_buff_intervals: Sequence,
    zankou_effect_three_recover_ratio: float | None,
    infer_buffs: bool,
    backend: BattleComputeBackend,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[tuple[BattleTreatmentEvent, ...], tuple[BattleInferredBuffInterval, ...]]:
    def rows(values, fields):
        result = []
        for index, row in enumerate(values):
            if checkpoint is not None and index % 64 == 0:
                checkpoint()
            result.append({key: getattr(row, key) for key in fields})
        return result

    payload = {
        "characters": [_character(row) for row in (build or {}).get("characters") or ()],
        "actions": rows(actions, (
            "action_id", "character_id", "character_name", "input_kind", "input_gesture",
            "start_us", "end_us", "evidence_event_ids",
        )),
        "hits": rows(hits, (
            "event_id", "character_id", "character_name", "relative_time_us", "damage",
            "damage_name", "skill_name", "gameplay_effect_id", "ability_id", "target_id",
        )),
        "state_buff_intervals": rows(state_buff_intervals, (
            "interval_id", "source_character_id", "source_effect_definition_id",
            "start_us", "end_us",
        )),
        "battle_end_us": battle_end_us,
        "time_stop_intervals": time_stop_intervals,
        "recover_ratio": zankou_effect_three_recover_ratio,
        "infer_buffs": infer_buffs,
    }
    responses = backend.compute_batch("treatment_replay_v1", (payload,), checkpoint=checkpoint)
    if len(responses) != 1:
        raise ValueError("treatment response count mismatch")
    response, = responses
    return tuple(map(_event, response["events"])), tuple(map(_buff, response["buffs"]))
