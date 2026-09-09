# 判断角色自选配置适用的分配模式。
"""Mode guards for per-role equipment preference options."""

from __future__ import annotations


def role_preference_mode_error(strategy: str, tape_main_filters=None, crit_priority_modes=None, crit_rate_caps=None) -> str | None:
    if strategy in ("role_priority", "update_mode"):
        return None
    return "알 수 없는 분배 전략입니다."
