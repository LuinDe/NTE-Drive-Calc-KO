# 管理工作台摘要后台读取、版本去重和隐藏页面的刷新合并。
"""Bounded read-only dashboard refresh; sync status is a separate projection."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any

from PySide6.QtCore import QObject, QTimer

from src.app.page_tasks import PageRequest, PageTaskLane
from src.services.dashboard_service import DashboardService
from src.utils.logger import logger
from src.utils.perf import log_perf


@dataclass(frozen=True)
class DashboardDependencies:
    generation: int
    user_database_path: Path
    static_database_path: Path


class DashboardController(QObject):
    """One active read, one pending revision, and no hidden-page database work."""

    def __init__(
        self, *, dependencies: Callable[[], DashboardDependencies],
        apply: Callable[[dict[str, Any]], None], failed: Callable[[str], None],
        loading: Callable[[], None], parent: QObject | None = None,
        merge_ms: int = 500,
    ) -> None:
        super().__init__(parent)
        self._dependencies = dependencies
        self._apply = apply
        self._failed = failed
        self._loading = loading
        self._merge_ms = merge_ms
        self._reads = PageTaskLane(self)
        self._reads.idle.connect(self._schedule)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start)
        self._identity: DashboardDependencies | None = None
        self._revision = 0
        self._dirty = True
        self._visible = False
        self._closed = False
        self._last_started = 0.0
        self._version: Hashable | None = None
        self._retry_blocked = False

    def _check_identity(self) -> None:
        identity = self._dependencies()
        if identity != self._identity:
            self._reads.cancel()
            self._timer.stop()
            self._identity = identity
            self._revision += 1
            self._version = None
            self._dirty = True
            self._retry_blocked = False
            self._last_started = 0.0
            self._loading()

    def set_visible(self, visible: bool) -> None:
        if self._closed:
            return
        self._check_identity()
        if visible and not self._visible:
            self._last_started = 0.0
        self._visible = visible
        if not visible:
            self._timer.stop()
            if self._reads.is_running():
                self._reads.cancel()
                self._dirty = True
                self._revision += 1
        else:
            self._schedule()

    def refresh(self, *, version: Hashable | None = None) -> None:
        """No version means a known mutation/explicit refresh, never a status tick."""
        if self._closed:
            return
        self._check_identity()
        if version is not None and version == self._version:
            return
        self._version = version
        self._revision += 1
        self._dirty = True
        self._retry_blocked = False
        # An already running result must not overwrite the new revision.
        self._reads.cancel()
        self._schedule()

    def _schedule(self) -> None:
        if self._closed or not self._visible or not self._dirty or self._retry_blocked or self._reads.is_running():
            return
        remaining = self._merge_ms - (monotonic() - self._last_started) * 1000
        if remaining <= 0:
            self._timer.start(0)
        elif not self._timer.isActive():
            self._timer.start(max(1, int(remaining)))

    def _start(self) -> None:
        if self._closed or not self._visible or not self._dirty or self._retry_blocked or self._reads.is_running():
            return
        self._check_identity()
        identity, revision = self._identity, self._revision
        assert identity is not None
        self._dirty = False
        self._last_started = monotonic()

        def read():
            started = perf_counter()
            model = DashboardService(
                identity.user_database_path, static_database_path=identity.static_database_path,
            ).load()
            log_perf(logger, "home.summary_read", elapsed_ms=(perf_counter() - started) * 1000)
            return model

        def current() -> bool:
            return (not self._closed and self._visible and revision == self._revision
                    and identity == self._dependencies())

        def apply(model):
            if current():
                started = perf_counter()
                self._apply(model)
                log_perf(logger, "home.summary_apply", elapsed_ms=(perf_counter() - started) * 1000)

        def failed(error):
            if current():
                # Retry is explicit (return to page / next mutation), not a spin loop.
                self._dirty = True
                self._retry_blocked = True
                self._failed(error)

        self._reads.submit(PageRequest((identity, revision), read, apply, failed))

    def retry(self) -> None:
        """Page entry permits retry after a transient failure."""
        self._last_started = 0.0
        self.refresh()

    def close(self) -> None:
        self._closed = True
        self._timer.stop()
        self._reads.close()


def initialize_dashboard(window) -> None:
    """Called by the composition root after navigation and home widgets exist."""
    from src.features.home.page import refresh_home_page

    def loading():
        window.home_account_label.setText("현재 계정의 작업 공간 요약을 읽는 중…")
        for value, _subtitle in window.home_metric_labels.values():
            value.setText("—")
        window.home_last_sync_label.setText("저장된 가방을 읽는 중…")
        window.home_character_sync_detail.setText("저장된 캐릭터 육성 정보를 읽는 중…")

    def failed(error):
        window.home_account_label.setText(f"작업 공간 요약 읽기 실패: {error}; 작업 공간으로 돌아오면 다시 시도할 수 있습니다.")
        logger.warning(f"작업 공간 요약 새로 고침 실패: {error}")

    window.dashboard_controller = DashboardController(
        dependencies=lambda: DashboardDependencies(
            window.app_context.generation, window.app_context.account.user_database_path,
            window.app_context.paths.static_database_path,
        ), apply=lambda model: refresh_home_page(window, model), failed=failed,
        loading=loading, parent=window,
    )
    window.stack.currentChanged.connect(lambda index: window.dashboard_controller.set_visible(
        window._nav_key_for_index(index) == "home",
    ))
    window.dashboard_controller.set_visible(window._nav_key_for_index(window.stack.currentIndex()) == "home")
