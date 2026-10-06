# 将只读原生检测与业务租约隔离，业务拒绝不等于传输连接失效。
from typing import Any

from src.integrations.nte_core_protocol import NteCoreRpcError
from src.integrations.native_inventory_snapshot import NativeSnapshotPending
from src.integrations.native_raw_snapshot import read_native_raw_domain
from src.services.native_status_queries import log_inspection_failure, transient_inspection_error

def _inspect_snapshot(self, *, refresh: bool = False, check_equipment: bool = True) -> dict[str, Any]:
    from src.services.native_game_session import SNAPSHOT_DOMAINS, NATIVE_EQUIPMENT_CAPABILITIES
    with self._lock:
        self._check_inspection()
        client = self._connect()
    try:
        capabilities = (client.hello_result or {}).get("capabilities", [])
        # Status calls must not hold the lock needed by a battle stop timeout abort.
        status = self._status_queries.read(client)
        self._runtime_cost_log.observe(status)
        domains = {}
        domain_errors: dict[str, dict[str, Any]] = {}
        if any(f"{domain}.snapshot.v1" in capabilities for domain in SNAPSHOT_DOMAINS):
            if refresh:
                for domain in SNAPSHOT_DOMAINS:
                    if f"{domain}.snapshot.v1" in capabilities:
                        with self._lock:
                            if self._lease is not None or self._inventory_lease is not None:
                                break
                            self._guard("native_sync")
                            if self._close_requested.is_set():
                                raise RuntimeError("네이티브 세션이 마무리 중입니다.")
                            if self._client is not client or not self._context_matches():
                                raise RuntimeError("네이티브 새로고침 중에 계정 컨텍스트가 변경되었습니다.")
                        try:
                            check = lambda: self._check_inspection(client)
                            if "snapshot.changes.v1" not in capabilities:
                                self._refresh_snapshot(client, {"domain": domain}, check)
                            elif ((domain == "character" and "native_character_profile_v1" in capabilities)
                                  or (domain == "inventory" and "native_inventory_dto_v1" in capabilities)):
                                self._read_projection(client, domain, check)
                            else:
                                from src.integrations.native_battle_snapshot import validate_native_snapshot_current
                                baseline = self._baseline
                                current = client.call("native.snapshot.status", {}, check_cancelled=check)
                                if baseline.get(domain, current) is None:
                                    def call(method, params):
                                        if method == "native.snapshot.refresh":
                                            return self._refresh_snapshot(client, params, check)
                                        return client.call(method, params, check_cancelled=check)
                                    raw = read_native_raw_domain(call, check, domain)
                                    validate_native_snapshot_current({"domains": {domain: raw}}, call("native.snapshot.status", {}))
                                    check()
                                    baseline.put(domain, raw)
                        except NativeSnapshotPending:
                            break
                        except NteCoreRpcError as error:
                            # These exact provider rejections invalidate only this domain.
                            if error.code != -32001 or error.message not in {"not_ready", "source_changed"}:
                                raise
                            reason = error.data.get("reason")
                            domain_errors[domain] = {
                                "code": error.code,
                                "message": error.message,
                                "reason": reason if isinstance(reason, str) else "",
                            }
            self._guard("native_sync")
            domains = client.call("native.snapshot.status", {})
        equipment = None
        # 装备接口检测在游戏线程执行，后台同步观察不应反复发起；实际装配仍逐次复核。
        if check_equipment and NATIVE_EQUIPMENT_CAPABILITIES.issubset(capabilities):
            try:
                equipment = self.equipment_status(client)
            except NteCoreRpcError as error:
                if error.code != -32001 or error.message not in {"not_ready", "source_changed"}:
                    raise
                equipment = {"ready": False, "reason": error.message}
        with self._lock:
            self._guard("native_sync")
            if self._client is not client or not self._context_matches():
                raise RuntimeError("네이티브 검사 결과가 만료되었습니다.")
            return {
                "hello": client.hello_result or {}, "status": status,
                "domains": domains, "domain_errors": domain_errors,
                "equipment": equipment,
                "inventory_snapshot_ready": bool(self._inventory_lease is not None and self._inventory_lease.snapshot_ready),
            }
    except Exception as error:
        retained = transient_inspection_error(error)
        log_inspection_failure(error, retained=retained)
        if retained:
            raise
        with self._lock:
            if self._client is client:
                self._failed = True
                if self._lease is None:
                    self._finish_close()
        raise
