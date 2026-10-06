# 角色页面兼容入口。
"""Thin composition entry point for official role feature."""

from __future__ import annotations

from .role_shell import (
    _page_my_role,
    _refresh_my_role,
    confirm_pending_my_role_changes,
)


def build_official_role_page(window):
    return _page_my_role(window)


def refresh_official_role_page(
    window, *, restore_scroll_value: int | None = None, discard_pending: bool = False,
):
    """Refresh the role page through its public feature entry point."""

    if discard_pending:
        window._official_role_dirty_ids = set()
        window._official_role_world_bonus_dirty = False
        window._my_role_dirty = False
        window._official_role_editors = {}
    return _refresh_my_role(window, restore_scroll_value=restore_scroll_value, force=discard_pending)


def deactivate_official_role_page(window):
    """Drop UI delivery/batches on leave, preserving drafts and bounded data caches."""
    controller = getattr(window, "_official_role_controller", None)
    if controller is not None:
        controller.cancel_reads()
    tabs = getattr(window, "official_role_tabs", None)
    if tabs is not None:
        for index in range(tabs.count()):
            tabs.widget(index)._role_build_token = None


__all__ = [
    "build_official_role_page",
    "refresh_official_role_page",
    "confirm_pending_my_role_changes",
    "deactivate_official_role_page",
]
