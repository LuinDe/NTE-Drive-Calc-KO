# 在后台计算单角色材料与体力，并丢弃过期的草稿结果。
"""One-worker owner for single-character cultivation calculations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from src.app.workers import WorkerThread
from src.domain.cultivation_history import HistoryCalculationEnvelope
from src.services.cultivation_planner_service import (
    CultivationPlan, CultivationPlannerService, CultivationRequest, CultivationStaminaPlan,
)
from src.services.cultivation_planner_models import CultivationPreparedTarget


_DRAINING_WORKERS: set[WorkerThread] = set()


@dataclass(frozen=True, slots=True)
class SingleCalculationRequest:
    target: CultivationRequest
    owned_quantities: tuple[tuple[str, int], ...]
    hunter_level: int
    identification_level: int | None
    history_envelope: HistoryCalculationEnvelope | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class SingleCalculationResult:
    request: SingleCalculationRequest
    plan: CultivationPlan
    stamina: CultivationStaminaPlan | None
    dataset_metadata: tuple[tuple[str, object], ...] = ()
    history_error: str | None = None
    preparation: CultivationPreparedTarget | None = None


class CultivationSingleController(QObject):
    result_ready = Signal(object)
    error = Signal(str)
    busy_changed = Signal(bool)
    preparation_ready = Signal(object)
    preparation_error = Signal(str)
    preparing_changed = Signal(bool)

    def __init__(self, service: CultivationPlannerService, *,
                 context_identity: Callable[[], object] | None, parent: QObject) -> None:
        super().__init__(parent)
        self._service = service
        self._context_identity = context_identity
        self._worker: WorkerThread | None = None
        self._active: tuple[int, object, str] | None = None
        self._pending: tuple[int, SingleCalculationRequest, object, str] | None = None
        self._preparation: tuple[object, CultivationPreparedTarget] | None = None
        self._revision = 0
        self._closed = False

    def submit(self, request: SingleCalculationRequest, identity: object) -> None:
        self._submit(request, identity, "calculate")

    def prepare(self, request: SingleCalculationRequest, identity: object) -> None:
        self._submit(request, identity, "prepare")

    def _submit(self, request: SingleCalculationRequest, identity: object, kind: str) -> None:
        if self._closed:
            return
        self._revision += 1
        submission = (self._revision, request, identity, kind)
        if self._worker is not None:
            self._pending = submission
            return
        self._start(submission)

    def invalidate(self, *, clear_preparation: bool = False) -> None:
        if not self._closed:
            self._revision += 1
            self._pending = None
            if clear_preparation:
                self._preparation = None

    def close(self) -> None:
        self._closed = True
        self._revision += 1
        self._pending = None
        self._preparation = None
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            if not worker.wait(5_000):
                worker.setParent(None)
                _DRAINING_WORKERS.add(worker)
                worker.finished.connect(lambda running=worker: _DRAINING_WORKERS.discard(running))
                self._worker = None
                self._active = None

    def _calculate(
        self, request: SingleCalculationRequest, prepared: CultivationPreparedTarget | None,
    ) -> SingleCalculationResult:
        metadata: tuple[tuple[str, object], ...] = ()
        history_error = None
        if request.history_envelope is not None:
            try:
                metadata = tuple(self._service.dataset_metadata().items())
            except Exception:
                history_error = "고정된 자료 식별 정보를 읽지 못했습니다. 다시 계산하세요."
        prepared = prepared if prepared is not None else self._service.prepare(request.target)
        if prepared.request != request.target:
            raise ValueError("육성 재료 준비 결과가 고정된 목표와 일치하지 않습니다")
        stamina = self._service.calculate_stamina(
            prepared.plan, owned_quantities=dict(request.owned_quantities),
            hunter_level=request.hunter_level,
            effective_identification_level=request.identification_level,
            farming_stages=prepared.farming_stages,
        )
        return SingleCalculationResult(request, prepared.plan, stamina, metadata, history_error, prepared)

    def _start(self, submission: tuple[int, SingleCalculationRequest, object, str]) -> None:
        revision, request, identity, kind = submission
        if not self._context_matches(identity):
            self.busy_changed.emit(False)
            self.preparing_changed.emit(False)
            return
        cached = self._preparation
        prepared = cached[1] if (cached is not None and cached[0] == identity
                                and cached[1].request == request.target) else None
        if kind == "prepare":
            action = lambda: prepared if prepared is not None else self._service.prepare(request.target)
        else:
            action = lambda: self._calculate(request, prepared)
        worker = WorkerThread(target=action, parent=self)
        self._worker = worker
        self._active = (revision, identity, kind)
        worker.result_ready.connect(self._on_result, Qt.ConnectionType.QueuedConnection)
        worker.error.connect(self._on_error, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        self.busy_changed.emit(kind == "calculate")
        self.preparing_changed.emit(kind == "prepare")
        worker.start()

    @Slot(object)
    def _on_result(self, result: object) -> None:
        if self._is_current() and QThread.currentThread() == self.thread():
            identity, kind = self._active[1], self._active[2]
            prepared = result if kind == "prepare" else result.preparation
            if isinstance(prepared, CultivationPreparedTarget):
                self._preparation = (identity, prepared)
            if kind == "prepare":
                self.preparation_ready.emit(result)
            else:
                self.result_ready.emit(result)

    @Slot(str)
    def _on_error(self, message: str) -> None:
        if self._is_current() and QThread.currentThread() == self.thread():
            signal = self.preparation_error if self._active[2] == "prepare" else self.error
            signal.emit(message)

    @Slot()
    def _on_finished(self) -> None:
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

    def _is_current(self) -> bool:
        if self._closed or self._active is None or self._active[0] != self._revision:
            return False
        return self._context_matches(self._active[1])

    def _context_matches(self, identity: object) -> bool:
        try:
            return self._context_identity is None or self._context_identity() == identity
        except (OSError, RuntimeError):
            return False
