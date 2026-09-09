# 汇总极速装配完成结果与提示。
"""Compact, actionable summaries for the fast-equipment completion dialog."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def build_fast_apply_completion_summary(
    applied: Sequence[Mapping[str, Any]],
    *,
    mismatch_role_names: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    """Return dispatched equipment counts without claiming fragment verification.

    Equipment residual events are not a complete inventory view.  They remain
    useful for internal retry diagnostics, but presenting their absence or a
    partial match as a player-visible loadout result would be misleading.
    """

    del mismatch_role_names
    last_attempt = max((int(row.get("attempt_count") or 1) for row in applied), default=1)

    summary = f"캐릭터 {len(applied)}명의 장비 세팅을 전송했습니다"
    if last_attempt > 1:
        summary += f"({last_attempt}번째 장착까지 진행됨)"
    lines = []
    for row in applied:
        role_name = str(row.get("role_name") or "알 수 없는 캐릭터")
        module_count = row.get("module_count")
        if module_count is None:
            detail = "전송됨"
        else:
            detail = f"드라이브 {int(module_count)}개"
            if row.get("core_count"):
                detail += " + 코어 1개"
        lines.append(f"• {role_name}: {detail}")
    return summary, "\n".join(lines)
