# 串行执行账号养成历史读写并隔离过期回调与敏感错误日志。
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter

from PySide6.QtCore import QObject, Qt, Signal, Slot

from src.app.workers import WorkerThread
from src.domain.cultivation_history import HistoryConflict, HistoryContextExpired
from src.services.cultivation_history_service import CultivationHistoryService
from src.storage.sqlite.user_data_support import UserDataError
from src.utils.cultivation_trace import trace_cultivation

_DRAINING_WORKERS: set[WorkerThread] = set()
_OPERATIONS = frozenset({"save", "list", "get", "select_all", "delete", "restore"})


@dataclass(frozen=True, slots=True)
class HistoryOperationResult:
    operation: str
    request_id: int
    value: object = None
    error_code: str | None = None
    message: str = ""


class CultivationHistoryController(QObject):
    """独立于求解 owner；单工作器按提交顺序处理保存与确切集合删除。"""

    completed = Signal(object)

    def __init__(
        self, service: CultivationHistoryService, *, context_identity: Callable[[], object], parent: QObject,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._identity = context_identity
        self._initial_identity = context_identity()
        self._worker: WorkerThread | None = None
        self._pending: deque[tuple[str, int, Callable[[], object]]] = deque()
        self._sequence = 0
        self._closed = False

    def submit(self, operation: str, action: Callable[[], object]) -> int:
        if operation not in _OPERATIONS:
            raise ValueError("잘못된 육성 기록 작업입니다")
        if self._closed:
            return 0
        self._sequence += 1
        request_id = self._sequence
        if operation in {"list", "get", "select_all"}:
            # Obsolete read projections are not business writes and need not remain queued.
            self._pending = deque(item for item in self._pending if item[0] != operation)
        self._pending.append((operation, request_id, action))
        if self._worker is None:
            self._start_next()
        return request_id

    def _start_next(self) -> None:
        if self._closed or not self._pending:
            return
        operation, request_id, action = self._pending.popleft()
        worker = WorkerThread(target=lambda: self._execute(operation, request_id, action), parent=self)
        self._worker = worker
        worker.result_ready.connect(self._on_result, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _execute(self, operation: str, request_id: int, action: Callable[[], object]) -> HistoryOperationResult:
        started = perf_counter()
        trace_cultivation(request_id, f"history.{operation}.begin")
        try:
            if self._closed or self._identity() != self._initial_identity:
                raise HistoryContextExpired("육성 기록 컨텍스트가 무효화되었습니다")
            result = HistoryOperationResult(operation, request_id, value=action())
        except HistoryContextExpired:
            result = HistoryOperationResult(operation, request_id, error_code="expired")
        except HistoryConflict as error:
            result = HistoryOperationResult(operation, request_id, error_code="conflict", message=str(error))
        except ValueError as error:
            result = HistoryOperationResult(operation, request_id, error_code="invalid_data", message=str(error))
        except UserDataError:
            result = HistoryOperationResult(
                operation, request_id, error_code="storage_failed",
                message="기록 데이터베이스 작업에 실패했습니다. 파일 사용 여부와 여유 공간을 확인한 후 다시 시도하세요.",
            )
        except Exception:
            # Do not let WorkerThread print a traceback containing a restoration payload.
            result = HistoryOperationResult(
                operation, request_id, error_code="operation_failed", message="기록 작업이 완료되지 않았습니다. 다시 시도하세요.",
            )
        trace_cultivation(
            request_id, f"history.{operation}.end", failed=result.error_code is not None,
            elapsed_ms=int((perf_counter() - started) * 1000),
        )
        return result

    @Slot(object)
    def _on_result(self, result: object) -> None:
        if self._closed:
            return
        try:
            current = self._identity() == self._initial_identity
        except (OSError, RuntimeError):
            current = False
        if current:
            self.completed.emit(result)

    @Slot()
    def _on_finished(self) -> None:
        self._worker = None
        self._start_next()

    def close(self) -> None:
        self._closed = True
        self._pending.clear()
        self._service.close()
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            if not worker.wait(5_000):
                worker.setParent(None)
                _DRAINING_WORKERS.add(worker)
                worker.finished.connect(lambda running=worker: _DRAINING_WORKERS.discard(running))
                self._worker = None
