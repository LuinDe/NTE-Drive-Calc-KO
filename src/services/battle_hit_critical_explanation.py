# 区分公式暴击分支与原生观测标记，避免把飘字标记当作伤害倍率证据。
from src.domain.battle_report import BattleAnalysisHit, BattleHitReplayResult


def critical_explanation(hit: BattleAnalysisHit, replay: BattleHitReplayResult) -> str:
    states = {"critical": "예", "non_critical": "아니요", "not_applicable": "해당 없음", "unknown": "未知"}
    evidence = hit.field_evidence
    if replay.critical_policy == "disabled" and replay.critical_state == "not_applicable":
        label = "공식 치명타: 해당 없음 (현재 공식은 치명타 배율을 사용하지 않음)"
        if evidence is not None and evidence.critical_source == "native_prediction":
            label += f"; 네이티브 예측/플로팅 텍스트 치명타 표시: {states.get(evidence.critical_state, '未知')}"
        return label
    if evidence is not None and (evidence.critical_source.startswith("native_") or evidence.critical_source == "server_settlement"):
        source = {"native_prediction": "네이티브 예측/피해 텍스트", "native_execution": "네이티브 실행", "server_settlement": "네이티브 정산"}.get(evidence.critical_source, "네이티브 관측")
        return f"{source} 치명타: {states.get(evidence.critical_state, '未知')} (필드 신뢰도 {evidence.critical_confidence})"
    return f"추정 치명타: {states.get(replay.critical_state, replay.critical_state)} (신뢰도 {replay.confidence})"
