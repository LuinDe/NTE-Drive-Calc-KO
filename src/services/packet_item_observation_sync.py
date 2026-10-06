# 在背包同步会话中合并并稳定保存抓包物品数量观测。
"""A one-slot, account-bound packet observation path beside complete inventory."""

from __future__ import annotations

import time
from typing import Any

from src.observability import log_event
from src.storage.sqlite.packet_item_observation_dao import normalize_packet_item_observation
from src.storage.sqlite.user_data_support import UserDataValidationError

from .inventory_capture_wait import InventorySyncCancelled, require_inventory_operation


class PacketItemObservationSync:
    def __init__(self, service: Any, dao: Any, settle_seconds: float) -> None:
        self._service = service
        self._dao = dao
        self._settle_seconds = settle_seconds
        self._latest_event: dict[str, Any] | None = None
        self._candidate: dict[str, Any] | None = None
        self._changed_at = 0.0
        self._retry_at = 0.0
        self._last_generation = 0
        self._last_sequence = 0
        self._session_inventory_snapshot_id: int | None = None

    def on_event(self, event: dict[str, Any]) -> None:
        try:
            require_inventory_operation(self._service)
        except (InventorySyncCancelled, PermissionError):
            return
        if event.get("method") != "event.inventory.items_observed":
            return
        with self._service._event_lock:
            self._latest_event = dict(event)
        self._service._event_ready.set()

    def receive_latest(self) -> None:
        with self._service._event_lock:
            event = self._latest_event
            self._latest_event = None
        if event is None:
            return
        try:
            snapshot = normalize_packet_item_observation(event.get("params"))
        except UserDataValidationError as exc:
            self._candidate = None
            log_event(
                "WARNING", "inventory_sync.packet_items_invalid",
                "패킷 캡처 아이템 관측이 검증을 통과하지 못해 마지막으로 저장된 버전을 유지합니다",
                self._service._operation_context, error=exc,
            )
            return
        generation, sequence = snapshot["generation"], snapshot["sequence"]
        if generation < self._last_generation or sequence <= self._last_sequence:
            return
        self._last_generation, self._last_sequence = generation, sequence
        self._candidate = snapshot
        self._changed_at = time.monotonic()
        self._retry_at = 0.0

    def on_inventory_snapshot_committed(self, snapshot_id: int) -> None:
        self._session_inventory_snapshot_id = snapshot_id

    def save_if_stable(self, now: float) -> None:
        candidate = self._candidate
        if (candidate is None or now - self._changed_at < self._settle_seconds
                or now < self._retry_at):
            return
        service = self._service
        require_inventory_operation(service)
        try:
            observation_id = self._dao.save_packet_item_observation(
                candidate, account_id=service.account_id,
                inventory_snapshot_id=self._session_inventory_snapshot_id,
                check=lambda: require_inventory_operation(service),
            )
        except (InventorySyncCancelled, PermissionError):
            raise
        except Exception as exc:
            self._retry_at = now + 2.0
            self._session_inventory_snapshot_id = None
            log_event(
                "WARNING", "inventory_sync.packet_items_save_retry",
                "안정 패킷 캡처 아이템 관측 저장 실패, 자동으로 재시도합니다",
                service._operation_context, error=exc,
            )
            return
        self._candidate = None
        log_event(
            "INFO", "inventory_sync.packet_items_saved",
            "현재 계정의 안정 패킷 캡처 아이템 관측을 저장했습니다",
            service._operation_context,
            observation_id=observation_id, item_count=candidate["item_count"],
            inventory_snapshot_id=self._session_inventory_snapshot_id,
        )
