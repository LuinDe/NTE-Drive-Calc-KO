# 为共享原生会话预留空闲维护窗口，不取消其他功能的业务租约。
from contextlib import contextmanager
from time import monotonic, sleep

from src.integrations.nte_core_protocol import NteCoreError


class NativeSessionMaintenance:
    """Own a temporary connection gate without changing saved feature preferences."""

    def __init__(self, owner):
        self.owner = owner
        self.closed = False
        if not owner._lock.acquire(blocking=False):
            raise NteCoreError('네이티브 요청이 마무리 중입니다. 잠시 후 업데이트하세요.')
        try:
            if owner._maintenance.is_set():
                raise NteCoreError('컴포넌트에 이미 유지 관리 작업이 진행 중이라 중복으로 업데이트할 수 없습니다.')
            if owner._lease is not None or owner._battle_requested.is_set():
                raise NteCoreError('전투 리포트를 녹화 중이거나 마무리하는 중입니다. 녹화를 중지하고 저장이 끝날 때까지 기다린 뒤 플러그인을 업데이트하세요.')
            inventory = owner._inventory_lease
            if inventory is not None and inventory.maintenance_blocked:
                raise NteCoreError(inventory.maintenance_description)
            owner._maintenance.set()
        finally:
            owner._lock.release()

    def disconnect(self, *, check, timeout=15):
        """Called on the update worker after business owners have requested stop."""
        owner, deadline = self.owner, monotonic() + timeout
        while monotonic() < deadline:
            check()
            if owner._snapshot_lock.acquire(timeout=.1):
                try:
                    with owner._lock:
                        if owner._lease is not None:
                            raise NteCoreError('전투 리포트가 아직 연결을 사용 중이라 업데이트를 실행하지 않았습니다.')
                        if owner._inventory_lease is None and owner._refresh_active is None:
                            owner._finish_close()
                            owner._hud_applied = None
                            return
                finally:
                    owner._snapshot_lock.release()
            sleep(.05)
        raise NteCoreError('네이티브 작업이 아직 마무리되지 않아 플러그인 업데이트를 실행하지 않았습니다.')

    def close(self):
        if not self.closed:
            self.closed = True
            self.owner._maintenance.clear()


def reserve_plugin_maintenance(owner):
    return NativeSessionMaintenance(owner)


@contextmanager
def plugin_maintenance(owner):
    if not owner._snapshot_lock.acquire(blocking=False):
        raise NteCoreError('네이티브 읽기가 아직 진행 중입니다. 동기화를 끝낸 뒤 컴포넌트를 업데이트하세요.')
    try:
        with owner._lock:
            if (owner._maintenance.is_set() or owner._lease is not None or owner._inventory_lease is not None
                    or owner._refresh_active is not None or owner._battle_requested.is_set()
                    or owner._performance_active or owner._performance_trace_active or (owner._hud_applied and any(owner._hud_applied[1].values()))):
                raise NteCoreError('먼저 동기화, 전투 리포트, HUD, 성능 표시를 중지한 뒤 컴포넌트를 업데이트하세요.')
            owner._maintenance.set()
            try:
                owner._finish_close()
            except Exception:
                owner._maintenance.clear()
                raise
    finally:
        owner._snapshot_lock.release()
    try:
        yield
    finally:
        owner._maintenance.clear()
