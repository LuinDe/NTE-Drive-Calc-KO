# 提供扫描工作流的纯状态与文案契约。
"""Small pure helpers shared by the scanning controller and workflow."""

from __future__ import annotations


def scanning_is_running(owner) -> bool:
    for name in ("_scan_worker", "_gamepad_worker", "_vision_worker"):
        worker = getattr(owner, name, None)
        if worker is not None and callable(getattr(worker, "isRunning", None)):
            if worker.isRunning():
                return True
    return False


def offline_scope_replaces_inventory(scope: str) -> bool:
    return scope in ("full", "all")


def vision_cancel_message(parsed_count: int) -> str:
    return (
        f"분석을 중지했습니다. 이번에 스크린샷 {int(parsed_count or 0)}장을 분석했습니다.\n\n"
        "분석 작업이 취소되어 이번 결과는 SQLite 가방 스냅샷에 기록/갱신되지 않았습니다."
    )
