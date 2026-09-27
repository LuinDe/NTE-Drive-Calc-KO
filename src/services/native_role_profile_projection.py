# 将稀疏原生养成叠加到角色模板或账号配置并明确逐字段来源。
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_support import UserDataValidationError, _valid_breakthrough_stage_for_level


def load_template_growth_defaults(
    character_ids: tuple[int, ...], *, static_database_path: str | Path,
) -> dict[int, dict[str, int]]:
    defaults = {}
    if not character_ids:
        return defaults
    with StaticGameDataDao(static_database_path) as dao:
        for character_id in character_ids:
            if dao.get_character(character_id) is None:
                raise UserDataValidationError("캐릭터 상태에 현재 정식 정적 카탈로그에 존재하지 않는 캐릭터가 포함되어 있습니다")
            rows = dao.list_character_panel_growth(character_id)
            if not rows:
                raise UserDataValidationError("정식 캐릭터 템플릿에 성장 데이터가 없어 희소 캐릭터 상태를 확인할 수 없음")
            growth = max(rows, key=lambda row: (int(row["level"]), int(row["breakthrough_stage"])))
            defaults[character_id] = {"character_level": int(growth["level"]), "breakthrough_stage": int(growth["breakthrough_stage"])}
    return defaults


def project_native_role_profile(
    base: Mapping[str, Any], observation: Mapping[str, Any] | None, *, persisted: bool,
) -> dict[str, Any]:
    profile = dict(base)
    sources = {name: "account" if persisted else "template" for name in base
               if name not in {"persisted", "field_sources", "created_at_utc", "updated_at_utc"}}
    for name in ("character_level", "breakthrough_stage"):
        value = (observation or {}).get(name)
        if value is not None:
            profile[name] = value
            sources[name] = "native_observed"
    observation = observation or {}
    if observation.get("awakening_level") is not None:
        profile["awakening_level"] = observation["awakening_level"]
        sources["awakening_level"] = "native_observed"
    if observation.get("awakening_selection_initialized") is True:
        profile["selected_awaken_effect_ids"] = list(observation["selected_awaken_effect_ids"])
        profile["awakening_selection_initialized"] = True
        sources["selected_awaken_effect_ids"] = "native_observed"
        sources["awakening_selection_initialized"] = "native_observed"
    for name in ("likeability_level_10_enabled", "fork_id", "fork_level", "fork_breakthrough_stage", "fork_refinement_level"):
        if name in observation and (not name.startswith("fork_") or observation.get("fork_observed") is True):
            profile[name] = observation[name]
            sources[name] = "native_observed"
    if observation.get("skill_levels"):
        profile["skill_levels"] = {**profile.get("skill_levels", {}), **observation["skill_levels"]}
        sources["skill_levels"] = "native_observed"
    if observation and not _valid_breakthrough_stage_for_level(profile["character_level"], profile["breakthrough_stage"]):
        raise UserDataValidationError("캐릭터 네이티브 관측이 현재 템플릿의 레벨, 돌파 조합과 일치하지 않습니다")
    profile["field_sources"] = sources
    return profile


def validate_native_cultivation(patches, *, static_database_path):
    """Verify formal identities against the frozen shipped catalog before writes."""
    if not any(patch.get("skill_levels") or patch.get("fork_observed") or "likeability_levels" in patch
               or "likeability_level_10_enabled" in patch or patch.get("awakening_selection_initialized") for patch in patches):
        return
    with StaticGameDataDao(static_database_path) as dao:
        fork_ids = {row["fork_id"] for row in dao.list_forks()} if any(patch.get("fork_id") for patch in patches) else set()
        for patch in patches:
            validate_native_cultivation_patch(patch, dao, fork_ids)


def validate_native_cultivation_patch(patch, dao, fork_ids):
    """核对一个独立字段组；批量严格校验与角色稀疏同步共用此规则。"""
    resolve_native_likeability(patch, dao)
    if patch.get("awakening_selection_initialized"):
        effects = {row["effect_id"] for row in dao.list_character_awaken_effects(patch["character_id"])
                   if row["awaken_type"] == "Awaken_Effect"}
        if any(value not in effects for value in patch["selected_awaken_effect_ids"]):
            raise UserDataValidationError("네이티브 각성 효과가 현재 캐릭터의 공식 카탈로그에 없습니다")
    # Each static row is the cost of advancing from that level to the
    # next one, as in the role editor; the last attainable level is +1.
    skills = {row["skill_id"]: max((int(level["level"]) for level in row["levels"]), default=0) + 1
              for row in dao.list_character_skills(patch["character_id"])} if patch.get("skill_levels") else {}
    if any(key not in skills or not 1 <= level <= skills[key] for key, level in patch.get("skill_levels", {}).items()):
        raise UserDataValidationError("네이티브 스킬 식별 정보 또는 기본 레벨이 현재 공식 카탈로그에 없습니다")
    fork_id = patch.get("fork_id")
    if patch.get("fork_observed") and fork_id is not None:
        if fork_id not in fork_ids:
            matches = [identity for identity in fork_ids if identity.casefold() == fork_id.casefold()]
            if not matches:
                raise UserDataValidationError("네이티브 아크 식별 정보가 현재 공식 카탈로그에 없습니다")
            if len(matches) != 1:
                raise UserDataValidationError("네이티브 아크 식별 정보에 대소문자 매칭 충돌이 있어 해당 아크 상태를 적용하지 않았습니다")
            # 仅纠正唯一正式身份的字母大小写，不补前缀或改写原始采集记录。
            patch["fork_id"] = matches[0]
    if "likeability_level_10_enabled" in patch:
        bonus = dao.get_character_likeability_bonus(patch["character_id"])
        if bonus is None:
            patch["likeability_level_10_enabled"] = False
        elif int(bonus["required_level"]) != 10:
            raise UserDataValidationError("네이티브 호감도 임계값이 현재 공식 카탈로그와 일치하지 않습니다")


def resolve_native_likeability(profile, static_dao):
    """Resolve the observed collection through the official source identity."""
    levels = profile.pop("likeability_levels", None)
    if levels is None:
        return
    identity = static_dao.get_character_likeability_identity(profile["character_id"])
    profile["likeability_level_10_enabled"] = bool(
        identity and levels.get(identity["likeability_id"], 0) >= int(identity["required_level"])
    )
