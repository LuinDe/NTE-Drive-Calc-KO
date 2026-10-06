# 统一计算失败结果和日志的一行说明，不参与配装判断。
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def allocation_failure_text(plan: Mapping[str, Any] | None) -> str:
    """Render existing solver evidence without recomputing any constraint."""
    plan = plan or {}
    reason = " ".join(str(
        plan.get("reason") or "이번에는 저장할 수 있는 방안이 생성되지 않았습니다. 원인은 진단 대기 중입니다"
    ).split())
    progress = plan.get("group_search") or {}
    if plan.get("search_status") in {
        "bounded_unproven", "budget_exhausted", "candidate_truncated",
    } and progress.get("members", 0) > 1 and progress.get("completed", 0) > 0:
        reason += f"(같은 등급 그룹 {progress['completed']}/{progress['members']}명 완료)"
    return reason
