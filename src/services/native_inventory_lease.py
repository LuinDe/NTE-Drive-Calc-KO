# 复用应用原生会话读取正式背包投影，不创建抓包进程或额外轮询线程。
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from threading import Event
from time import monotonic

from src.domain.all_item_snapshot import ALL_ITEMS_CAPABILITY
from src.integrations.native_inventory_snapshot import NativeSnapshotPending
from src.services.inventory_capture_wait import InventorySyncCancelled
from src.integrations.nte_core_protocol import NteCoreError, NteCoreRpcError, NteCoreProtocolError
from src.services.native_snapshot_changes import (
    CHANGES_CAPABILITY, NativeSnapshotChanges, domain_status, snapshot_change_key,
)
from src.observability import OperationContext, log_event


class NativeInventoryLease:
    native_capture = True

    def __init__(self, owner, client):
        self._owner, self._client = owner, client
        self._stopped = Event()
        self._released = False
        self._started = False
        self._handlers = {}
        self._sequence = 0
        self._snapshot_ready = False
        self._equipment_context = None
        self._write_outcome_unknown = False
        self._write_operation = '게임 장비 세팅'
        self._changes = NativeSnapshotChanges()
        self._all_items_pending = None
        self._all_items_saved_revision = None
        self._all_items_retry_at = 0.0

    @property
    def maintenance_blocked(self):
        return self._equipment_context is not None or self._write_outcome_unknown

    @property
    def maintenance_description(self):
        if self._write_outcome_unknown:
            return f'{self._write_operation} 실행 결과가 확인되지 않았습니다. 먼저 게임 안의 실제 상태를 확인하세요. 이번에는 플러그인을 업데이트하지 않으며 작업도 다시 보내지 않습니다.'
        return f'{self._write_operation} 작업이 실행 중이거나 결과 확인을 기다리는 중입니다. 해당 기능이 끝난 뒤 플러그인을 업데이트하세요.'

    @property
    def snapshot_ready(self):
        return self._snapshot_ready and not self._released and not self._stopped.is_set()

    @property
    def hello_result(self):
        return self._client.hello_result

    @property
    def executable_sha256(self):
        return self._client.executable_sha256

    def _check(self):
        if self._released or self._stopped.is_set() or self._owner._inventory_lease is not self:
            raise InventorySyncCancelled("네이티브 가방 동기화가 중지되었습니다.")
        self._owner._check_projection(self._client)

    def start(self):
        self._check()
        return self

    def add_event_handler(self, method, handler):
        self._check()
        self._handlers.setdefault(method, []).append(handler)

    def remove_event_handler(self, method, handler):
        handlers = self._handlers.get(method, [])
        if handler in handlers:
            handlers.remove(handler)

    def start_capture(self, *, profile, raw_capture="disabled", **kwargs):
        self._check()
        if profile != "inventory" or raw_capture != "disabled" or kwargs.get("wait_for_game"):
            raise ValueError("네이티브 가방 동기화는 정식 인벤토리 읽기만 허용합니다.")
        self._started = True
        return {"capture_status": "running", "native_snapshot_ready": False}

    def status(self):
        with self._owner._snapshot_scope(self._check):
            return self._status()

    def confirm_inventory_snapshot(self, metadata):
        """保存前只复核候选身份，不采集新快照或刷新角色。"""
        with self._owner._snapshot_scope(self._check):
            try:
                current = self._change_status()
            except NteCoreRpcError as error:
                if not self._pending_error(error):
                    raise
                self._snapshot_ready = False
                return False
            if current is None:
                return self.snapshot_ready
            if not isinstance(metadata, dict):
                raise NteCoreProtocolError("저장 대기 중인 네이티브 가방에 리비전 식별 정보가 없습니다.")
            expected = tuple(metadata.get(key) for key in ("providerId", "domainKey", "revision"))
            self._snapshot_ready = (self._changes.is_current(current, "inventory")
                                    and snapshot_change_key(current, "inventory") == expected)
            self._check()
            return self.snapshot_ready

    def _status(self):
        self._check()
        self._snapshot_ready = False
        if not self._started:
            return {"capture_status": "idle", "native_snapshot_ready": False}
        try:
            status = self._change_status()
            snapshot = self._refresh_domain("inventory", status)
        except NativeSnapshotPending as error:
            return {"capture_status": "running", "native_snapshot_ready": False, "message": str(error)}
        except NteCoreRpcError as error:
            if (error.domain_code == "NATIVE_SNAPSHOT_INCOMPLETE"
                    or error.message in {"not_ready", "source_changed", "snapshot_not_found", "disabled"}
                    or (error.code == -32001 and error.message == "control_timeout")):
                return {"capture_status": "running", "native_snapshot_ready": False,
                        "message": ("이번 가방 읽기가 시간 초과되어 게임이 준비되면 자동으로 재시도합니다; 저장된 가방은 그대로 유지됩니다."
                                    if error.message == "control_timeout" else
                                    "읽는 동안 가방에 변화가 있어 자동으로 다시 읽는 중입니다; 저장된 가방은 그대로 유지됩니다."
                                    if error.message == "source_changed" else
                                    "게임이 이번 전체 가방을 제공할 때까지 기다리는 중입니다; 저장된 가방은 변경되지 않습니다.")}
            raise
        self._check()
        if snapshot is not None:
            self._emit_inventory(snapshot)
        character = None
        character_error = None
        # Return a newly read inventory to the stabilizer before starting another
        # potentially slow game-thread read. Tracked domains resume next poll.
        defer_character = snapshot is not None and status is not None
        if not defer_character and "native_character_profile_v1" in (self.hello_result or {}).get("capabilities", ()):
            try:
                character = self._refresh_domain("character", self._change_status())
            except (NativeSnapshotPending, NteCoreRpcError) as error:
                if isinstance(error, NteCoreRpcError) and not self._pending_error(error):
                    raise
                character_error = "캐릭터 상태가 아직 준비되지 않아 저장된 육성을 유지합니다."
        self._check()
        current = self._change_status()
        self._snapshot_ready = current is None or self._changes.is_current(current, "inventory")
        if character is not None and current is not None and not self._changes.is_current(current, "character"):
            character = None
        all_items = None
        all_items_error = None
        if (self.snapshot_ready and snapshot is None and character is None and character_error is None
                and current is not None and self._equipment_context is None
                and ALL_ITEMS_CAPABILITY in (self.hello_result or {}).get("capabilities", ())):
            try:
                all_items = self._all_items_for_storage(current)
            except (NativeSnapshotPending, NteCoreError) as error:
                self._all_items_retry_at = monotonic() + 30.0
                all_items_error = type(error).__name__
            self._check()
            current = self._change_status()
            self._snapshot_ready = self._changes.is_current(current, "inventory")
        return {"capture_status": "running", "native_snapshot_ready": self.snapshot_ready,
                "native_change_pending": (not self.snapshot_ready and current is not None
                                          and bool(domain_status(current, "inventory").get("domainKey"))),
                "message": "가방이 동기화되었으며, 백그라운드에서 변화를 모니터링하고 있습니다." if self.snapshot_ready else "장비 변경이 안정될 때까지 기다리는 중입니다.",
                **({"native_character_snapshot": character} if character is not None else {}),
                **({"native_all_item_snapshot": all_items} if all_items is not None else {}),
                **({"native_all_item_error": all_items_error} if all_items_error else {}),
                **({"native_character_error": character_error} if character_error else {})}

    def _all_items_for_storage(self, status):
        revision = snapshot_change_key(status, "inventory")
        if self._all_items_pending is not None:
            pending_revision = tuple(self._all_items_pending[key] for key in ("providerId", "domainKey", "revision"))
            if pending_revision == revision:
                return self._all_items_pending
            self._all_items_pending = None
        if revision == self._all_items_saved_revision or monotonic() < self._all_items_retry_at:
            return None
        self._all_items_pending = self._owner.read_all_items(self._client, self._check)
        return self._all_items_pending

    def confirm_all_item_snapshot_saved(self, snapshot):
        self._check()
        if self._all_items_pending is snapshot:
            self._all_items_saved_revision = tuple(snapshot[key] for key in ("providerId", "domainKey", "revision"))
            self._all_items_pending = None

    @staticmethod
    def _pending_error(error):
        return (error.code == -32001 and error.message == "control_timeout") or error.domain_code in {"NATIVE_SNAPSHOT_INCOMPLETE", "NATIVE_MAPPING_UNSUPPORTED"} or error.message in {
            "not_ready", "source_changed", "snapshot_not_found", "disabled",
        }

    def _change_status(self):
        self._check()
        if CHANGES_CAPABILITY not in (self.hello_result or {}).get("capabilities", ()):
            return None
        result = self._client.call("native.snapshot.status", {}, check_cancelled=self._check)
        self._check()
        return result

    def _refresh_domain(self, domain, status):
        if status is not None and not self._changes.needs_refresh(status, domain):
            return None
        try:
            snapshot = self._owner._read_projection(self._client, domain, self._check)
        except (NativeSnapshotPending, NteCoreRpcError) as error:
            if status is not None and (isinstance(error, NativeSnapshotPending) or self._pending_error(error)):
                delay = self._changes.defer(status, domain)
                log_event("INFO", "native_sync.retry_deferred", "같은 리비전 읽기가 완료되지 않아 재시도 빈도를 낮춥니다",
                          OperationContext.create("native_sync"), domain=domain, retry_after_seconds=delay,
                          error_type=type(error).__name__)
            raise
        if status is not None:
            latest = self._change_status()
            expected = snapshot.get("providerId"), snapshot.get("domainKey"), snapshot.get("revision")
            if (snapshot_change_key(latest, domain) != expected
                    or domain_status(latest, domain).get("ready") is not True
                    or domain_status(latest, domain)["dirty"] or snapshot.get("dirty") is not False):
                raise NativeSnapshotPending("읽는 동안 게임 상태가 변경되어 다시 동기화하는 중입니다.")
            self._changes.accept(latest, domain)
        return snapshot

    def _emit_inventory(self, snapshot):
        timestamp = snapshot["observedUnixUs"]
        generation = snapshot["generation"]
        if not timestamp.isdecimal() or not generation.isdecimal():
            raise NteCoreProtocolError("네이티브 동기화 시간 또는 스냅샷 세대 형식이 유효하지 않습니다.")
        self._sequence += 1
        metadata = {key: value for key, value in snapshot.items() if key not in {"items", "characters"}}
        payload = {"generation": int(generation), "sequence": self._sequence,
                   "observed_at_unix_ms": int(timestamp) // 1000, "complete": True,
                   "item_count": len(snapshot["items"]), "items": snapshot["items"],
                   "character_count": len(snapshot["characters"]), "characters": snapshot["characters"],
                   "native_snapshot": metadata}
        event = {"jsonrpc": "2.0", "method": "event.inventory.snapshot", "params": payload}
        for handler in tuple(self._handlers.get("event.inventory.snapshot", ())):
            self._check()
            handler(deepcopy(event))
        self._check()

    def request_stop(self):
        self._stopped.set()
        self._snapshot_ready = False

    def stop_capture(self):
        self.request_stop()
        self._started = False
        return {"capture_status": "stopped", "native_snapshot_ready": False}

    def close(self):
        if not self._released:
            self.stop_capture()
            self._released = True
            self._handlers.clear()
            self._owner._release_inventory(self)

    @contextmanager
    def equipment_batch(self, *, check_cancelled=None, timeout: float | None = None):
        """暂缓快照读取直到整批派发完成；通知仍由 DLL 累积。"""
        deadline = None if timeout is None else monotonic() + timeout
        def check():
            self._check()
            self._owner._guard("native_equipment")
            if check_cancelled is not None:
                check_cancelled()
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutError("장착 읽기 잠금 대기 시간이 초과되어 이번 요청은 전달되지 않았습니다")

        with self._owner._snapshot_scope(check):
            if self._equipment_context is not None:
                yield self
                return
            # The application pins a saved complete inventory before dispatch.
            # Refresh readiness must not block commands against those known UIDs.
            status = self._owner.equipment_status(self._client,
                timeout=2.0 if deadline is None else min(2.0, max(0.001, deadline - monotonic())))
            if status.get("ready") is not True:
                raise NteCoreRpcError({"code": -32001, "message": "장비 인터페이스가 준비된 후 다시 시도하세요.",
                                       "data": {"domain_code": "NATIVE_SNAPSHOT_INCOMPLETE"}})
            self._equipment_context = tuple(status.get(key) for key in ("providerId", "domainKey"))
            try:
                check()
                yield self
            finally:
                self._equipment_context = None
                self._snapshot_ready = False

    def _equipment(self, method, **kwargs):
        if self._equipment_context is not None:
            return self._equipment_direct(method, **kwargs)
        with self.equipment_batch():
            return self._equipment_direct(method, **kwargs)

    def equipment_identity(self, *, timeout: float = 2.0):
        """Read the live command source identity without changing a frozen batch."""
        self._check()
        status = self._owner.equipment_status(self._client, timeout=timeout)
        if status.get("ready") is not True:
            raise NteCoreRpcError({"code": -32001, "message": "source_changed",
                                   "data": {"domain_code": "EQUIPMENT_REQUEST_REJECTED"}})
        provider, domain = status.get("providerId"), status.get("domainKey")
        if provider is None or domain is None:
            raise NteCoreProtocolError("장비 소스 식별 정보에 정식 제공자 또는 객체 도메인이 없습니다")
        if self._equipment_context is not None and (provider, domain) != self._equipment_context:
            raise NteCoreRpcError({"code": -32001, "message": "source_changed",
                                   "data": {"domain_code": "EQUIPMENT_REQUEST_REJECTED"}})
        self._check()
        return (provider, domain, self._client.executable_sha256, id(self._client))

    def _equipment_direct(self, method, **kwargs):
        self._check()
        self._owner._guard("native_equipment")
        self._write_operation = {
            'equip_one_key': '고속 장착', 'set_item_locked': '창고 잠금/잠금 해제',
            'set_item_discarded': '창고 폐기 표시', 'set_item_states': '창고 잠금/폐기 일괄 표시',
        }.get(method, '게임 장비 조정')
        try:
            return getattr(self._client, method)(**kwargs)
        except NteCoreRpcError as error:
            if error.code != -32001 or error.message != "source_changed" or error.domain_code is not None:
                raise
            self._check()
            current = self._owner.equipment_status(self._client)
            current_context = tuple(current.get(key) for key in ("providerId", "domainKey"))
            self._check()
            if current.get("ready") is True and current_context == self._equipment_context:
                raise
            raise NteCoreRpcError({
                "code": -32001,
                "message": "source_changed",
                "data": {"domain_code": "EQUIPMENT_REQUEST_REJECTED"},
            }) from error
        except Exception:
            # Transport/protocol failure after dispatch must not be treated as a
            # completed write or replayed after reconnect. Retain the session.
            self._write_outcome_unknown = True
            raise

    def equip_one_key(self, **kwargs):
        return self._equipment("equip_one_key", **kwargs)

    def equip_module(self, **kwargs):
        return self._equipment("equip_module", **kwargs)

    def equip_core(self, **kwargs):
        return self._equipment("equip_core", **kwargs)

    def unequip_module(self, **kwargs):
        return self._equipment("unequip_module", **kwargs)

    def unequip_core(self, **kwargs):
        return self._equipment("unequip_core", **kwargs)

    def unequip_all(self, **kwargs):
        return self._equipment("unequip_all", **kwargs)

    def move_module_to_character(self, **kwargs):
        return self._equipment("move_module_to_character", **kwargs)

    def move_core_to_character(self, **kwargs):
        return self._equipment("move_core_to_character", **kwargs)

    def set_item_discarded(self, **kwargs):
        return self._equipment("set_item_discarded", **kwargs)

    def set_item_states(self, **kwargs):
        return self._equipment("set_item_states", **kwargs)

    def set_item_locked(self, **kwargs):
        return self._equipment("set_item_locked", **kwargs)
