# 校验机器可检索的稳定日志事件名称。
"""Stable event-name validation shared by operation logging."""

from __future__ import annotations

import re


_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


def validate_event_name(event: str) -> str:
    normalized = str(event).strip()
    if not _EVENT_NAME.fullmatch(normalized):
        raise ValueError(
            "로그 event는 두 단계 이상의 소문자 점 구분 이름이어야 하며, 각 단계는 문자·숫자·밑줄만 포함할 수 있습니다"
        )
    return normalized
