# 合并同一 Core 的在途状态读取，让观察者共享本次回包而非互相报忙。
from dataclasses import dataclass, field
from threading import Event, Lock

from src.integrations.nte_core_protocol import NteCoreRpcError, NteCoreTimeoutError
from src.observability import OperationContext, log_event
from src.integrations.native_inventory_snapshot import NativeSnapshotPending
from src.services.inventory_capture_wait import InventorySyncCancelled


@dataclass
class _StatusRead:
    client: object
    done: Event = field(default_factory=Event)
    result: object = None
    error: BaseException | None = None


class NativeStatusQueries:
    def __init__(self):
        self._lock = Lock()
        self._pending = None

    def read(self, client, *, timeout=2.0):
        with self._lock:
            pending = self._pending
            owner = pending is None or pending.client is not client
            if owner:
                pending = self._pending = _StatusRead(client)
        if owner:
            try:
                pending.result = client.call('core.status', timeout=timeout)
            except BaseException as error:
                pending.error = error
            finally:
                with self._lock:
                    pending.done.set()
                    if self._pending is pending:
                        self._pending = None
        elif not pending.done.wait(timeout):
            raise NteCoreTimeoutError('core.status', timeout)
        if pending.error is not None:
            raise pending.error
        return pending.result


def transient_inspection_error(error):
    if isinstance(error, (NativeSnapshotPending, InventorySyncCancelled)):
        return True
    return isinstance(error, NteCoreRpcError) and (
        (error.code == -32000 and error.domain_code == 'REQUEST_IN_PROGRESS')
        or (error.code == -32001 and error.message in {
            'not_ready', 'source_changed', 'control_busy', 'control_timeout', 'snapshot_not_found', 'disabled',
        })
    )


def log_inspection_failure(error, *, retained):
    # Do not log arbitrary server messages, request payloads or diagnostic data.
    domain = getattr(error, 'domain_code', None)
    log_event('WARNING', 'native_session.inspection_failed',
              '네이티브 검사 미완료, 공유 연결 유지' if retained else '네이티브 검사 실패, 유효하지 않은 연결 닫음',
              OperationContext.create('native_session'),
              error_type=type(error).__name__, rpc_code=getattr(error, 'code', None),
              reason=domain if domain in {'REQUEST_IN_PROGRESS', 'NATIVE_SNAPSHOT_INCOMPLETE',
                                         'NATIVE_MAPPING_UNSUPPORTED', 'NATIVE_CAPABILITY_MISSING'} else 'other',
              connection_retained=retained)
