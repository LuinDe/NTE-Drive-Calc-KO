# 将冻结同频证据送入原生推断，并恢复各角色归属与中文依据。
from __future__ import annotations

from src.services.battle_native_axis_compute import serialize_animation_candidate

_BASIS = {
    "core_pair": "아군 QTE 직후 링코 UltraSkillLTE_AOE가 이어지고, 둘 다 같은"
                 "nte-core 전투 리포트에 기록된 시간 정지 구간 안에 있습니다; 이 구간은 중간 신뢰도 컨텍스트 보조로만 쓰이며,"
                 "QTE→LTE 페어링이 발동 추론의 주체입니다.",
    "fallback_pair": "아군 QTE 직후 링코 UltraSkillLTE_AOE가 이어집니다; 둘 다 낮은 신뢰도의"
                     "Q 동작 폴백 구간에 들어갑니다. 폴백 구간만으로는 발동을 증명할 수 없고, 독립 페어링과 결합해야만 낮은 신뢰도 추론을 이룹니다.",
    "pair": "아군 QTE 후 150ms 안에 같은 대상에 링코 UltraSkillLTE_AOE가 이어집니다;"
            "현재 페어링 전체를 덮는 유일한 사용 가능 시간 정지 구간이 없으므로 낮은 신뢰도의"
            "동조 응답 페어링만 기록하며, 이를 근거로 링코의 Q 또는 E가 발동했다고 증명하지는 않습니다.",
    "type6_skill": "완전하고 순서가 맞는 링코 E 4단 히트 뒤, 유일한 정적 애니메이션 응답 창 안에서"
                    "같은 대상에 대한 첫 아군 QTE가 나타났고, 시간 정지나 이미 배정된 다른 QTE를 넘지 않음"
                    "; 유일한 type6 캐릭터 선택 구간이 해당 QTE를 덮거나 바로 인접하지만, 단독으로 동조 발동을 증명할 수는 없습니다.",
    "legacy_skill": "완전하고 순서가 맞는 링코 E 4단 히트 뒤, 유일한 정적 애니메이션 응답 창 안에서"
                     "같은 대상에 대한 첫 아군 QTE가 나타났고, 시간 정지나 이미 배정된 다른 QTE를 넘지 않음"
                     "; 이전 전투 리포트에는 유형 근거가 없어 창 안에서 가장 이른 유일한 유효 QTE 폴백을 사용합니다.",
}


def infer_native_linko_coattack(
    backend, hits, actions, *, time_stop_projection, animation_candidates, type6_evidence,
    allow_legacy_e_fallback, character_elements, checkpoint=None,
):
    from src.services.battle_linko_coattack_inference_service import _QteAction, _inference

    payload = {
        "hits": [{
            "event_id": hit.event_id, "sequence": hit.sequence,
            "relative_time_us": hit.relative_time_us, "character_id": hit.character_id,
            "direction": hit.direction, "is_follow_up": hit.is_follow_up,
            "ability_id": hit.ability_id, "gameplay_effect_id": hit.gameplay_effect_id,
            "target_id": hit.target_id,
        } for hit in hits],
        "actions": [{
            "action_id": action.action_id, "character_id": action.character_id,
            "input_kind": action.input_kind, "start_us": action.start_us,
            "evidence_event_ids": action.evidence_event_ids,
        } for action in actions],
        "time_stop_projection": {
            "intervals": time_stop_projection.intervals,
            "source_kind": time_stop_projection.source_kind,
            "confidence": time_stop_projection.confidence,
            "non_type6_intervals": time_stop_projection.non_type6_intervals,
        },
        "animation_candidates": [serialize_animation_candidate(row) for row in animation_candidates],
        "type6_evidence": [{
            "event_id": row.event_id, "relative_time_us": row.relative_time_us,
            "end_relative_time_us": row.end_relative_time_us, "target_id": row.target_id,
        } for row in type6_evidence],
        "allow_legacy_e_fallback": allow_legacy_e_fallback,
    }
    response = backend.compute_batch("linko_coattack_v1", (payload,), checkpoint=checkpoint)
    if len(response) != 1:
        raise ValueError("Native Linko response count mismatch")
    groups = []
    for ordinal, row in enumerate(response[0]["inferences"]):
        if checkpoint is not None and ordinal % 64 == 0:
            checkpoint()
        action_index = row["qte_action"]
        indices = row["hit_indices"]
        if type(action_index) is not int or not 0 <= action_index < len(actions):
            raise ValueError("Native Linko action index mismatch")
        if not indices or any(type(index) is not int or not 0 <= index < len(hits) for index in indices):
            raise ValueError("Native Linko hit index mismatch")
        qte = _QteAction(actions[action_index], tuple(hits[index] for index in indices))
        groups.append(_inference(
            qte, trigger_kind=row["trigger_kind"], confidence=row["confidence"],
            basis=_BASIS[row["basis_kind"]], evidence_event_ids=row["evidence_event_ids"],
            trigger_action_id=row["trigger_action_id"], raw_gap_us=row["raw_gap_us"],
            active_gap_us=row["active_gap_us"], time_stop_source_kind=row["time_stop_source_kind"],
            time_stop_confidence=row["time_stop_confidence"], character_elements=character_elements or {},
            selection_pause_start_us=row["selection_pause_start_us"],
            selection_pause_end_us=row["selection_pause_end_us"],
        ))
    results = []
    for group, index in response[0]["order"]:
        if type(group) is not int or not 0 <= group < len(groups):
            raise ValueError("Native Linko group index mismatch")
        if type(index) is not int or not 0 <= index < len(groups[group]):
            raise ValueError("Native Linko result index mismatch")
        results.append(groups[group][index])
    return tuple(results)
