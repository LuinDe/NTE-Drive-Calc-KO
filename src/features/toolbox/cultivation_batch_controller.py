# 管理多角色养成计算的单工作器、末次请求与上下文过期丢弃。
"""Asynchronous owner for batch cultivation calculations."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from src.app.workers import WorkerThread
from src.services.cultivation_batch_planner_service import (
    CultivationBatchPlannerService,
    CultivationBatchRequest,
    CultivationBatchPlan,
    CultivationBatchPreparation,
)
from src.utils.cultivation_trace import trace_cultivation


_DRAINING_WORKERS: set[WorkerThread] = set()


class CultivationBatchController(QObject):
    """Serialize batch solvers and only publish the newest current-context result."""

    result_ready = Signal(object)
    error = Signal(str)
    busy_changed = Signal(bool)
    preparation_ready = Signal(object)
    preparation_error = Signal(str)
    preparing_changed = Signal(bool)

    def __init__(
        self,
        service: CultivationBatchPlannerService,
        *,
        context_identity: Callable[[], object] | None,
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._context_identity = context_identity
        self._worker: WorkerThread | None = None
        self._active: tuple[int, object, int, str] | None = None
        self._pending: tuple[int, CultivationBatchRequest, object, str] | None = None
        self._preparation: tuple[object, CultivationBatchPreparation] | None = None
        self._revision = 0
        self._closed = False

    def submit(self, request: CultivationBatchRequest, identity: object) -> None:
        self._submit(request, identity, "calculate")

    def prepare(self, request: CultivationBatchRequest, identity: object) -> None:
        self._submit(request, identity, "prepare")

    def _submit(self, request: CultivationBatchRequest, identity: object, kind: str) -> None:
        if self._closed:
            return
        self._revision += 1
        submission = (self._revision, request, identity, kind)
        # Keep the old worker owned until its queued finished slot has run.
        if self._worker is not None:
            trace_cultivation(request.trace_id, "controller.queued")
            self._pending = submission
            return
        self._start(submission)

    def invalidate(self, *, clear_preparation: bool = False) -> None:
        """Revoke a running or queued result as soon as the draft changes."""

        if self._closed:
            return
        self._revision += 1
        self._pending = None
        if clear_preparation:
            self._preparation = None

    def close(self) -> None:
        trace_cultivation(self._active[2] if self._active else 0, "controller.close")
        self._closed = True
        self._revision += 1
        self._pending = None
        self._preparation = None
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            if not worker.wait(5_000):
                # A still-running QThread must outlive the page that owned it.
                worker.setParent(None)
                _DRAINING_WORKERS.add(worker)
                worker.finished.connect(
                    lambda running=worker: _DRAINING_WORKERS.discard(running)
                )
                self._worker = None
                self._active = None

    def _start(
        self,
        submission: tuple[int, CultivationBatchRequest, object, str],
    ) -> None:
        revision, request, identity, kind = submission
        if not self._context_matches(identity):
            self.busy_changed.emit(False)
            self.preparing_changed.emit(False)
            return
        cached = self._preparation
        prepared = cached[1] if (cached is not None and cached[0] == identity
                                and cached[1].account_id == request.account_id
                                and cached[1].generation == request.generation
                                and cached[1].dataset_identity == request.dataset_identity
                                and cached[1].ordered_targets == request.ordered_targets) else None
        if kind == "prepare":
            action = lambda: prepared if prepared is not None else self._service.prepare(request)
        elif prepared is not None:
            action = lambda: self._service.calculate(request, preparation=prepared)
        else:
            action = lambda: self._service.calculate(request)
        worker = WorkerThread(
            target=action,
            parent=self,
        )
        self._worker = worker
        self._active = (revision, identity, request.trace_id, kind)
        trace_cultivation(request.trace_id, "controller.worker_started")
        worker.result_ready.connect(self._on_result, Qt.ConnectionType.QueuedConnection)
        worker.error.connect(self._on_error, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        self.busy_changed.emit(kind == "calculate")
        self.preparing_changed.emit(kind == "prepare")
        worker.start()

    @Slot(object)
    def _on_result(self, result: object) -> None:
        active = self._active
        if active is None:
            return
        revision, identity, trace_id, kind = active
        gui_thread = QThread.currentThread() == self.thread()
        trace_cultivation(trace_id, "controller.result_slot", gui_thread=gui_thread)
        if not gui_thread:
            return
        if self._is_current(revision, identity):
            trace_cultivation(trace_id, "controller.result_accepted")
            prepared = result if kind == "prepare" else (
                result.preparation if isinstance(result, CultivationBatchPlan) else None
            )
            if isinstance(prepared, CultivationBatchPreparation):
                self._preparation = (identity, prepared)
            if kind == "prepare":
                self.preparation_ready.emit(result)
            else:
                self.result_ready.emit(result)
        else:
            trace_cultivation(trace_id, "controller.result_discarded")

    @Slot(str)
    def _on_error(self, message: str) -> None:
        active = self._active
        if active is None:
            return
        revision, identity, trace_id, kind = active
        gui_thread = QThread.currentThread() == self.thread()
        trace_cultivation(trace_id, "controller.error_slot", gui_thread=gui_thread)
        if not gui_thread:
            return
        if self._is_current(revision, identity):
            trace_cultivation(trace_id, "controller.error_accepted")
            signal = self.preparation_error if kind == "prepare" else self.error
            signal.emit(message)

    @Slot()
    def _on_finished(self) -> None:
        active = self._active
        trace_id = active[2] if active else 0
        gui_thread = QThread.currentThread() == self.thread()
        trace_cultivation(trace_id, "controller.worker_finished", gui_thread=gui_thread)
        if not gui_thread:
            return
        self._worker = None
        self._active = None
        if self._closed:
            return
        pending = self._pending
        self._pending = None
        if pending is not None:
            self._start(pending)
        else:
            self.busy_changed.emit(False)
            self.preparing_changed.emit(False)

    def _is_current(self, revision: int, identity: object) -> bool:
        if self._closed or revision != self._revision:
            return False
        return self._context_matches(identity)

    def _context_matches(self, identity: object) -> bool:
        try:
            return self._context_identity is None or self._context_identity() == identity
        except (OSError, RuntimeError):
            return False


__all__ = ["CultivationBatchController"]
