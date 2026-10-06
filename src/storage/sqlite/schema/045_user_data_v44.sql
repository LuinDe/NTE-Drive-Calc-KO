-- 当前账号抓包会话中稳定的物品数量观测；不改变完整背包当前指针。
CREATE TABLE packet_item_observation (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    inventory_snapshot_id INTEGER REFERENCES inventory_snapshot(snapshot_id) ON DELETE SET NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    observed_at_unix_ms INTEGER NOT NULL CHECK (observed_at_unix_ms > 0),
    item_count INTEGER NOT NULL CHECK (item_count BETWEEN 1 AND 10000),
    raw_snapshot_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    saved_at_utc TEXT NOT NULL
);
