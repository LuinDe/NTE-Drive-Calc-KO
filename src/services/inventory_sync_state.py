# 定义背包同步展示状态，明确停止原因以约束自动恢复。
from dataclasses import dataclass
from typing import Literal


SyncPhase = Literal[
    "stopped",
    "starting",
    "waiting",
    "collecting",
    "saving",
    "listening",
    "error",
]



@dataclass(frozen=True)
class InventorySyncState:
    phase: SyncPhase = "stopped"
    message: str = "가방 동기화가 아직 시작되지 않았습니다"
    running: bool = False
    capturing: bool = False
    pending_item_count: int | None = None
    added_count: int = 0
    removed_count: int = 0
    last_snapshot_id: int | None = None
    last_item_count: int | None = None
    last_synced_at_utc: str | None = None
    source_snapshot_ready: bool = False
    capture_source: Literal["packet", "native"] = "packet"
    error: str | None = None
    error_code: str | None = None
    stop_reason: str | None = None
    character_sync_revision: int = 0
    character_sync_error: str | None = None
    updated_at_utc: str = ""
