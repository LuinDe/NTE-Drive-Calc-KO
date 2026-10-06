# 处理抓包等待能力、操作代次以及同步写入前的授权复核。
from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any

from src.integrations.operation_guard import require_operation


class InventorySyncCancelled(Exception):
    def __init__(self, message='', *, reason='operation_cancelled'):
        super().__init__(message)
        self.reason = reason if reason in {
            'connection_lost', 'stop_requested', 'context_changed', 'permission_revoked',
            'battle_requested', 'maintenance', 'operation_cancelled',
        } else 'operation_cancelled'


class CaptureStartError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.domain_code = code
        super().__init__(f"수집 초기화 실패({code}). 환경을 다시 검사하세요.")


def require_inventory_operation(service: Any, capability: str | None = None) -> None:
    if service._stop_requested.is_set() or (
        service._context_is_current is not None and not service._context_is_current()
    ):
        raise InventorySyncCancelled()
    if capability is None:
        capability = "native_sync" if service.capture_source == "native" else "packet_capture"
    require_operation(service._operation_guard, capability)


class CaptureWaitMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.status = "starting"
        self.operation_id: str | None = None
        self.error_code: str | None = None

    def update(self, payload: Mapping[str, Any]) -> None:
        operation = payload.get("capture_operation_id", payload.get("operation_id"))
        status = payload.get("capture_status", payload.get("status"))
        with self._lock:
            if operation and self.operation_id and str(operation) != self.operation_id:
                return
            if operation:
                self.operation_id = str(operation)
            if status in {"idle", "waiting_game", "waiting_network", "starting", "running", "failed", "stopped"}:
                self.status = str(status)
            code = payload.get("capture_error_code", payload.get("error_code"))
            if code:
                self.error_code = str(code)

    def read(self) -> tuple[str, str | None]:
        with self._lock:
            return self.status, self.error_code


def receive_capture_status(service: Any, event: Mapping[str, Any]) -> None:
    payload = event.get("params") if event.get("method") == "event.capture.status" else event
    if not isinstance(payload, Mapping) or payload.get("profile") != "inventory":
        return
    if service._stop_requested.is_set():
        return
    service._capture_monitor.update(payload)
    if service._capture_monitor.read()[0] == "running":
        service._capture_ready.set()


def wait_capture_ready(
    service: Any, client: Any, *, supports_wait: bool, diagnostics: bool = False,
) -> bool:
    deadline = time.monotonic() + 15.0
    next_status = 0.0
    last_state = ""
    while not service._stop_requested.is_set():
        require_inventory_operation(service)
        if diagnostics:
            require_inventory_operation(service, "diagnostics")
        now = time.monotonic()
        if supports_wait and now >= next_status:
            service._capture_monitor.update(client.status())
            next_status = now + 0.5
        status, error_code = service._capture_monitor.read()
        if status == "failed":
            raise CaptureStartError(error_code or "CAPTURE_START_FAILED")
        if status == "running" or service._capture_ready.is_set():
            return True
        normal_wait = supports_wait and status in {"waiting_game", "waiting_network"}
        if normal_wait:
            deadline = now + 15.0
        elif now >= deadline:
            raise TimeoutError("nte-core 패킷 캡처 초기화 시간 초과, running 상태로 진입하지 못했습니다")
        if status != last_state:
            message = {
                "waiting_game": "게임 시작 대기 중; 패킷 캡처가 아직 준비되지 않았습니다.",
                "waiting_network": "게임 네트워크 연결 대기 중; 패킷 캡처가 아직 준비되지 않았습니다.",
            }.get(status, "패킷 캡처를 초기화하는 중이며 네트워크 어댑터 준비를 기다립니다")
            service._publish("waiting" if normal_wait else "starting", message, running=True, capturing=False)
            last_state = status
        service._stop_requested.wait(service._poll_seconds)
    return False
