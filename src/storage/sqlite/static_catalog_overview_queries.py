# 提供游戏资料库 125 张发行静态表的固定只读登记。
"""Fixed-table coverage queries for the release static catalog overview."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.storage.sqlite.static_game_data_dao import StaticGameDataError, resolve_static_database
from src.storage.sqlite.static_game_data_metadata import (
    MINIMUM_SUPPORTED_SCHEMA_VERSION,
    SCHEMA_VERSION,
)


# 表名只来自本固定登记，绝不接受 UI 输入。状态是独立覆盖审计结论：
# A=完整正式目录，B=已公开但仍有高级字段，C=可展示且存在结构化缺口，
# D=只有 ID/有限证据，E=正式发行中为空或 payload 明确省略。
STATIC_TABLE_CATALOG: tuple[tuple[str, str, str], ...] = (
    ("dataset", "메타데이터와 출처", "B"),
    ("schema_migration", "메타데이터와 출처", "B"),
    ("source_file", "메타데이터와 출처", "C"),
    ("source_row", "메타데이터와 출처", "E"),
    ("application_setting_default", "메타데이터와 출처", "B"),
    ("localized_term", "메타데이터와 출처", "B"),
    ("localized_term_name", "메타데이터와 출처", "A"),
    ("character", "캐릭터와 육성", "A"),
    ("character_release_evidence", "캐릭터와 육성", "B"),
    ("character_release_annotation", "캐릭터와 육성", "B"),
    ("character_release_evidence_link", "캐릭터와 육성", "B"),
    ("character_acquisition_membership", "캐릭터와 육성", "A"),
    ("character_annotation", "캐릭터와 육성", "B"),
    ("character_awaken_effect", "캐릭터와 육성", "A"),
    ("character_awaken_skill_level_bonus", "캐릭터와 육성", "A"),
    ("character_likeability_bonus", "캐릭터와 육성", "B"),
    ("character_likeability_bonus_property", "캐릭터와 육성", "B"),
    ("character_panel_growth", "캐릭터와 육성", "A"),
    ("character_skill", "캐릭터와 육성", "A"),
    ("character_skill_level", "캐릭터와 육성", "B"),
    ("character_cultivation_guide", "캐릭터와 육성", "C"),
    ("character_cultivation_fork_recommendation", "캐릭터와 육성", "B"),
    ("character_cultivation_attribute_recommendation", "캐릭터와 육성", "C"),
    ("character_cultivation_stage", "캐릭터와 육성", "C"),
    ("character_cultivation_stage_skill", "캐릭터와 육성", "B"),
    ("character_weight_recommendation", "캐릭터와 육성", "B"),
    ("character_weight_recommendation_property", "캐릭터와 육성", "B"),
    ("character_graduation_template", "캐릭터와 육성", "B"),
    ("character_shape_bonus", "캐릭터와 육성", "E"),
    ("character_shape_bonus_property", "캐릭터와 육성", "E"),
    ("logical_character_shape_bonus", "캐릭터와 육성", "A"),
    ("logical_character_shape_bonus_property", "캐릭터와 육성", "B"),
    ("fork_type", "아크", "B"),
    ("fork_item", "아크", "A"),
    ("fork_modify_pack", "아크", "B"),
    ("fork_modify_value", "아크", "A"),
    ("fork_upgrade_level", "아크", "B"),
    ("fork_breakthrough", "아크", "A"),
    ("fork_refinement_parameter_value", "아크", "A"),
    ("fork_permanent_property", "아크", "A"),
    ("fork_star_level", "아크", "A"),
    ("fork_star_parameter", "아크", "A"),
    ("fork_lottery_campaign", "아크", "A"),
    ("progression_item", "캐릭터와 육성", "A"),
    ("progression_item_alias", "캐릭터와 육성", "B"),
    ("item_quality_term", "캐릭터와 육성", "A"),
    ("equipment_attribute", "장비와 세트", "A"),
    ("equipment_base_attribute_curve", "장비와 세트", "B"),
    ("equipment_base_attribute_point", "장비와 세트", "B"),
    ("equipment_buff_curve", "장비와 세트", "B"),
    ("equipment_buff_curve_point", "장비와 세트", "B"),
    ("equipment_core_random_attribute", "장비와 세트", "D"),
    ("equipment_item", "장비와 세트", "A"),
    ("equipment_strength_level", "장비와 세트", "D"),
    ("equipment_modify_pack", "장비와 세트", "B"),
    ("equipment_modify_value", "장비와 세트", "B"),
    ("equipment_plan", "장비와 세트", "A"),
    ("equipment_plan_cell", "장비와 세트", "A"),
    ("equipment_plan_core_attribute", "장비와 세트", "B"),
    ("equipment_plan_module", "장비와 세트", "A"),
    ("equipment_plan_recommended_attribute", "장비와 세트", "B"),
    ("equipment_shape", "장비와 세트", "A"),
    ("equipment_shape_cell", "장비와 세트", "A"),
    ("equipment_suit", "장비와 세트", "A"),
    ("equipment_suit_effect", "장비와 세트", "A"),
    ("equipment_suit_required_shape", "장비와 세트", "B"),
    ("gameplay_ability_catalog", "스킬과 정식 식별자", "B"),
    ("gameplay_ability_description", "스킬과 정식 식별자", "C"),
    ("gameplay_ability_level_hint", "스킬과 정식 식별자", "B"),
    ("gameplay_effect_catalog", "스킬과 정식 식별자", "B"),
    ("skill_damage", "스킬과 정식 식별자", "A"),
    ("skill_damage_modifier", "스킬과 정식 식별자", "B"),
    ("character_combat_ability_binding", "스킬과 정식 식별자", "B"),
    ("combat_ability_effect_binding", "스킬과 정식 식별자", "B"),
    ("combat_ability_montage_binding", "스킬과 정식 식별자", "B"),
    ("combat_level_curve", "공식과 효과 근거", "B"),
    ("combat_level_curve_point", "공식과 효과 근거", "B"),
    ("reaction_definition", "공식과 효과 근거", "C"),
    ("combat_effect_constant", "공식과 효과 근거", "C"),
    ("damage_resistance_term", "공식과 효과 근거", "A"),
    ("combat_effect_definition", "공식과 효과 근거", "B"),
    ("combat_effect_buff_link", "공식과 효과 근거", "B"),
    ("combat_curve", "공식과 효과 근거", "B"),
    ("combat_curve_point", "공식과 효과 근거", "B"),
    ("buff_definition", "공식과 효과 근거", "B"),
    ("buff_modifier", "공식과 효과 근거", "B"),
    ("buff_trigger_effect", "공식과 효과 근거", "B"),
    ("roguelike_modifier_profile", "공식과 효과 근거", "C"),
    ("roguelike_modifier_property", "공식과 효과 근거", "C"),
    ("combat_blueprint_asset", "청사진과 애니메이션 근거", "B"),
    ("combat_blueprint_reference", "청사진과 애니메이션 근거", "B"),
    ("combat_blueprint_semantic_property", "청사진과 애니메이션 근거", "B"),
    ("combat_blueprint_tag", "청사진과 애니메이션 근거", "B"),
    ("combat_montage", "청사진과 애니메이션 근거", "B"),
    ("combat_montage_section", "청사진과 애니메이션 근거", "B"),
    ("combat_montage_notify", "청사진과 애니메이션 근거", "B"),
    ("monster_catalog", "몬스터와 프로필", "A"),
    ("monster_identifier_alias", "몬스터와 프로필", "B"),
    ("monster_template_binding", "몬스터와 프로필", "B"),
    ("monster_boss_support", "몬스터와 프로필", "B"),
    ("monster_instance_profile", "몬스터와 프로필", "B"),
    ("monster_instance_profile_variant", "몬스터와 프로필", "B"),
    ("enemy_combat_profile", "몬스터와 프로필", "B"),
    ("enemy_element_resistance", "몬스터와 프로필", "B"),
    ("abyss_level", "플레이 방식과 조우", "A"),
    ("abyss_level_monster_spawn", "플레이 방식과 조우", "B"),
    ("abyss_monster_pool_entry", "플레이 방식과 조우", "B"),
    ("clone_activity_category", "플레이 방식과 조우", "A"),
    ("clone_activity", "플레이 방식과 조우", "B"),
    ("clone_activity_difficulty", "플레이 방식과 조우", "B"),
    ("clone_drop_projection", "플레이 방식과 조우", "B"),
    ("clone_drop_projection_item", "플레이 방식과 조우", "A"),
    ("clone_drop_projection_gap", "플레이 방식과 조우", "C"),
    ("clone_spawn_member", "플레이 방식과 조우", "B"),
    ("feast_stage", "플레이 방식과 조우", "A"),
    ("feast_stage_difficulty", "플레이 방식과 조우", "B"),
    ("feast_option", "플레이 방식과 조우", "A"),
    ("feast_stage_option", "플레이 방식과 조우", "A"),
    ("divination_buff", "플레이 방식과 조우", "A"),
    ("outer_realm_rotation", "플레이 방식과 조우", "A"),
    ("outer_realm_season_buff", "플레이 방식과 조우", "A"),
    ("outer_realm_season_buff_component", "플레이 방식과 조우", "B"),
    ("high_risk_commission", "플레이 방식과 조우", "B"),
    ("high_risk_commission_difficulty", "플레이 방식과 조우", "B"),
    ("high_risk_monster_pool_member", "플레이 방식과 조우", "B"),
)


@dataclass(frozen=True, slots=True)
class StaticTableOverview:
    name: str
    domain: str
    coverage_state: str
    rows: int


class StaticCatalogOverviewQueries:
    """Count every registered static table through a schema-checked RO handle."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = resolve_static_database(database_path)
        try:
            self._connection = sqlite3.connect(
                f"{self.database_path.as_uri()}?mode=ro", uri=True
            )
        except sqlite3.Error as exc:
            raise StaticGameDataError("자료실 커버리지 개요를 읽기 전용으로 열 수 없습니다") from exc
        version = self._connection.execute(
            "SELECT MAX(version) FROM schema_migration"
        ).fetchone()[0]
        self._schema_version = int(version or 0)
        if not MINIMUM_SUPPORTED_SCHEMA_VERSION <= self._schema_version <= SCHEMA_VERSION:
            self.close()
            raise StaticGameDataError(
                f"지원하지 않는 정적 데이터베이스 구조 버전: {version!r}; 지원 버전 "
                f"{MINIMUM_SUPPORTED_SCHEMA_VERSION}~{SCHEMA_VERSION}"
            )

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None

    def list_tables(self) -> tuple[StaticTableOverview, ...]:
        if self._connection is None:
            raise StaticGameDataError("자료실 커버리지 개요 연결이 닫혔습니다")
        available_tables = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        rows: list[StaticTableOverview] = []
        for name, domain, state in STATIC_TABLE_CATALOG:
            if name not in available_tables:
                continue
            count = self._connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            rows.append(StaticTableOverview(name, domain, state, int(count)))
        return tuple(rows)
