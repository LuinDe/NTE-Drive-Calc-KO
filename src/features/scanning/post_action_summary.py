# 汇总扫描后管理动作结果。
"""Text projection for completed scan state-management results."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def append_state_mismatch_summary(summary: str, stats: Mapping[str, Any]) -> str:
    """Append the non-destructive state mismatch notice when rows were skipped."""

    mismatch_count = int(stats.get("post_action_state_mismatch_count", 0) or 0)
    if not mismatch_count:
        return summary
    indexes = tuple(stats.get("post_action_state_mismatch_indexes", ()) or ())
    index_text = "、".join(f"{int(index)}번째" for index in indexes)
    return (
        f"{summary}\n상태가 스캔 계획과 달라 {mismatch_count}개를 건너뛰고 작업을 실행하지 않았습니다:"
        f"{index_text}."
    )
