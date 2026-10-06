-- 账号私有养成历史：配置和当时轻量结果原子保存，不关联角色或库存级联删除。
CREATE TABLE cultivation_history (
    history_id TEXT PRIMARY KEY NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('single', 'batch')),
    revision INTEGER NOT NULL CHECK (revision > 0),
    first_calculated_at_utc TEXT NOT NULL,
    last_calculated_at_utc TEXT NOT NULL,
    payload_version INTEGER NOT NULL CHECK (payload_version > 0),
    configuration_json TEXT NOT NULL,
    result_snapshot_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    search_text TEXT NOT NULL,
    content_sha256 TEXT NOT NULL
);
CREATE INDEX idx_cultivation_history_time
    ON cultivation_history(last_calculated_at_utc DESC, history_id DESC);
CREATE INDEX idx_cultivation_history_mode_time
    ON cultivation_history(mode, last_calculated_at_utc DESC, history_id DESC);
