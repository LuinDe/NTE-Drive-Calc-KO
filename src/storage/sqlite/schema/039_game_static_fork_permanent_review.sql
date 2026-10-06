-- 弧盘可有多条无条件面板属性；逐件保留来源审查结论。
CREATE TABLE fork_permanent_property_v39 (
    fork_id TEXT NOT NULL REFERENCES fork_item(fork_id),
    refinement_level INTEGER NOT NULL CHECK (refinement_level BETWEEN 1 AND 5),
    property_id TEXT NOT NULL CHECK (length(trim(property_id)) > 0),
    modifier_operation TEXT NOT NULL,
    property_value REAL NOT NULL,
    source_parameter_name_id TEXT NOT NULL,
    source_effect_definition_id TEXT NOT NULL
        REFERENCES combat_effect_definition(effect_definition_id),
    source_calculation_asset_path TEXT NOT NULL COLLATE NOCASE,
    source_row_id INTEGER NOT NULL REFERENCES source_row(source_row_id),
    PRIMARY KEY (fork_id, refinement_level, property_id)
);
INSERT INTO fork_permanent_property_v39
SELECT * FROM fork_permanent_property;
DROP TABLE fork_permanent_property;
ALTER TABLE fork_permanent_property_v39 RENAME TO fork_permanent_property;
CREATE INDEX idx_fork_permanent_property_property
    ON fork_permanent_property(property_id, fork_id, refinement_level);

CREATE TABLE fork_permanent_review (
    fork_id TEXT PRIMARY KEY REFERENCES fork_item(fork_id),
    status TEXT NOT NULL CHECK (status IN (
        'resolved_permanent', 'confirmed_no_permanent', 'conditional_only',
        'missing_calculation_evidence', 'missing_refinement_levels',
        'incomplete_curve', 'ambiguous'
    )),
    expected_level_count INTEGER NOT NULL,
    resolved_level_count INTEGER NOT NULL,
    candidate_count INTEGER NOT NULL,
    detail TEXT NOT NULL
);

-- 该索引只按施加条件反查，当前查询均按 Buff asset_path 的主键前缀读取。
-- 移除冗余索引以抵消审查记录的静态库体积，不变更任何来源行。
DROP INDEX IF EXISTS idx_buff_modifier_requirement;
