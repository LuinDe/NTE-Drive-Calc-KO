# 冻结采集组件来源，并将原生采集终止原因转换为安全的玩家提示。
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal


def with_comparison_metadata(
    record: Mapping[str, Any] | None,
    *,
    comparison_id: str | None,
    source: Literal["native", "packet"] | None,
) -> dict[str, Any] | None:
    """Attach Calc ownership outside Core summaries and native hit evidence."""
    if record is None:
        return None
    result = dict(record)
    if comparison_id is not None:
        if not comparison_id.strip() or source not in {"native", "packet"}:
            raise ValueError("이중 경로 페어링 메타데이터에는 유효한 식별자와 명확한 수집 출처가 필요합니다")
        result["calc_capture"] = {"comparison_id": comparison_id, "source": source}
    return result


def freeze_nte_core_provenance(client: object) -> dict[str, Any]:
    hello = getattr(client, "hello_result", None)
    hello_payload = dict(hello) if isinstance(hello, Mapping) else {}
    executable_sha256 = str(getattr(client, "executable_sha256", None) or "").strip()
    return {
        "core_version": str(hello_payload.get("core_version") or "").strip() or None,
        "protocol_version": hello_payload.get("protocol_version"),
        "data_version": str(hello_payload.get("data_version") or "").strip() or None,
        "executable_sha256": executable_sha256 or None,
    }


def native_capture_end_warning(
    terminal: Mapping[str, Any] | None,
    record: Mapping[str, Any] | None = None,
) -> str:
    reason = native_capture_end_reason(terminal, record)
    if reason is None or reason in {"user_stop", "scene_transition"}:
        return ""
    detail = {
        "source_changed": "게임 수집 컨텍스트가 무효화되었습니다",
        "queue_overflow": "수집 버퍼 부족",
        "peer_disconnected": "수집 연결 끊김",
        "provider_stopping": "수집 컴포넌트 중지됨",
        "start_failed": "수집 컴포넌트 시작 실패",
    }.get(reason, "수집 컴포넌트가 조기 종료됨")
    evidence = record.get("native_capture") if record else None
    diagnostics = evidence if isinstance(evidence, Mapping) and "reason" in evidence else terminal
    trigger = diagnostics.get("sourceChangeTrigger") if isinstance(diagnostics, Mapping) else None
    trigger_label = {
        "pawn_replication": "출전 캐릭터 복제 알림",
        "pawn_possess": "출전 캐릭터 인계",
        "pawn_unpossess": "출전 캐릭터 해제",
        "player_state_replication": "플레이어 상태 복제 알림",
        "local_end_play": "로컬 캐릭터 또는 씬 종료",
        "local_context_change": "로컬 수집 컨텍스트 변경",
        "game_thread_pulse_exception": "게임 스레드 수집 콜백 예외",
    }.get(trigger) if isinstance(trigger, str) else None
    if reason == "source_changed" and trigger_label:
        detail += f"(트리거: {trigger_label})"
    return f"강화 수집이 조기 종료되었습니다: {detail}; 이번 전투에는 히트별 데이터가 누락될 수 있습니다."


def native_capture_end_reason(
    terminal: Mapping[str, Any] | None,
    record: Mapping[str, Any] | None = None,
) -> str | None:
    evidence = record.get("native_capture") if record else None
    if isinstance(evidence, Mapping) and "reason" in evidence:
        terminal = evidence
    if terminal is None:
        return None
    reason = terminal.get("reason")
    return str(reason) if reason else None
