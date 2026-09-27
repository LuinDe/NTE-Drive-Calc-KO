-- requires-foreign-keys-off
-- 用户数据库 v40：删除全局优先，并将历史偏好版本收口为角色优先。

CREATE TABLE optimization_preference_version_v40 (
    profile_version_id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL
        REFERENCES optimization_preference_profile(profile_id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL CHECK (version_number >= 1),
    allocation_strategy TEXT NOT NULL
        CHECK (allocation_strategy = 'role_priority'),
    created_at_utc TEXT NOT NULL,
    UNIQUE (profile_id, version_number)
);

INSERT INTO optimization_preference_version_v40(
    profile_version_id, profile_id, version_number, allocation_strategy, created_at_utc
)
SELECT profile_version_id, profile_id, version_number, 'role_priority', created_at_utc
FROM optimization_preference_version;

DROP TABLE optimization_preference_version;
ALTER TABLE optimization_preference_version_v40 RENAME TO optimization_preference_version;
CREATE INDEX idx_optimization_preference_version_profile
    ON optimization_preference_version(profile_id, version_number DESC);
