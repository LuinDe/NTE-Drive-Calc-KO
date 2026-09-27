# 在单一事务中稀疏更新原生角色养成，保留未观测字段与已有子表。
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import sqlite3
import json
from typing import Any

from src.domain.native_role_sync import NativeRoleSyncResult
from .protocols import UserDataDaoMixinHost
from .user_data_support import UserDataError, UserDataValidationError, _utc_now, _valid_breakthrough_stage_for_level


GROWTH_FIELDS = ("character_level", "breakthrough_stage")


def normalize_native_profile_patches(profiles: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    if isinstance(profiles, (str, bytes)) or not isinstance(profiles, Sequence):
        raise UserDataValidationError("정식 캐릭터 상태는 캐릭터 목록이어야 함")
    patches = []
    seen = set()
    for row in profiles:
        if not isinstance(row, Mapping):
            raise UserDataValidationError("정식 캐릭터 상태 항목이 유효하지 않음")
        character_id = row.get("character_id")
        if type(character_id) is not int or character_id <= 0 or character_id in seen:
            raise UserDataValidationError("캐릭터 상태는 고유한 정식 character_id를 사용해야 합니다")
        seen.add(character_id)
        patch = {"character_id": character_id}
        for name, low, high in (("character_level", 1, 80), ("breakthrough_stage", 0, 6)):
            value = row.get(name)
            if value is None:
                continue
            if type(value) is not int or not low <= value <= high:
                raise UserDataValidationError(f"캐릭터 상태 {name}이(가) 유효 범위를 벗어났습니다")
            patch[name] = value
        awakening = row.get("awakening_level")
        if awakening is not None:
            if type(awakening) is not int or not 0 <= awakening <= 6:
                raise UserDataValidationError("네이티브 각성 레벨이 유효 범위를 벗어났습니다")
            patch["awakening_level"] = awakening
        selection_known = row.get("awakening_selection_initialized")
        if selection_known is not None and type(selection_known) is not bool:
            raise UserDataValidationError("네이티브 각성 선택 완전성은 불리언이어야 합니다")
        if selection_known is True:
            effects = row.get("selected_awaken_effect_ids")
            if (not isinstance(effects, (list, tuple)) or len(effects) > 6
                or any(not isinstance(value, str) or not value or len(value) > 128 for value in effects)
                or len(set(effects)) != len(effects)
                or awakening is None or len(effects) > awakening):
                raise UserDataValidationError("네이티브 각성 선택이 불완전하거나 해금된 슬롯 수를 초과했습니다")
            patch.update(awakening_selection_initialized=True, selected_awaken_effect_ids=list(effects))
        if "likeability_levels" in row:
            levels = row["likeability_levels"]
            if not isinstance(levels, Mapping) or len(levels) > 512 or any(
                not isinstance(key, str) or not key or len(key) > 128
                or type(value) is not int or not 0 <= value <= 100 for key, value in levels.items()
            ):
                raise UserDataValidationError("네이티브 호감도 등급 집합이 유효하지 않습니다")
            patch["likeability_levels"] = dict(levels)
        skills = row.get("skill_levels")
        if skills is not None:
            if not isinstance(skills, Mapping) or len(skills) > 64 or any(
                not isinstance(key, str) or not key or len(key) > 128
                or type(value) is not int or not 1 <= value <= 15 for key, value in skills.items()
            ):
                raise UserDataValidationError("네이티브 스킬 레벨이 유효하지 않습니다")
            if skills:
                patch["skill_levels"] = dict(skills)
        likeability = row.get("likeability_level_10_enabled")
        if likeability is not None:
            if type(likeability) is not bool:
                raise UserDataValidationError("네이티브 호감도 상태는 불리언이어야 합니다")
            patch["likeability_level_10_enabled"] = likeability
        if row.get("fork_observed") is True:
            if "fork_id" not in row:
                raise UserDataValidationError("네이티브 아크에 식별 정보가 없습니다")
            fork_id = row["fork_id"]
            if fork_id is not None and (not isinstance(fork_id, str) or not fork_id or len(fork_id) > 128):
                raise UserDataValidationError("네이티브 아크 식별 정보가 유효하지 않습니다")
            patch.update(fork_observed=True, fork_id=fork_id)
            if fork_id is None:
                patch.update(fork_level=None, fork_breakthrough_stage=None, fork_refinement_level=None)
            else:
                for name, high in (("fork_level", 80), ("fork_breakthrough_stage", 6), ("fork_refinement_level", 5)):
                    value = row.get(name)
                    if type(value) is not int or not (0 if name == "fork_breakthrough_stage" else 1) <= value <= high:
                        raise UserDataValidationError("네이티브 아크 육성이 불완전하거나 유효하지 않습니다")
                    patch[name] = value
                if not _valid_breakthrough_stage_for_level(patch["fork_level"], patch["fork_breakthrough_stage"]):
                    raise UserDataValidationError("네이티브 아크 레벨과 돌파가 일치하지 않습니다")
        if len(patch) > 1:
            patches.append(patch)
    return tuple(patches)


class NativeCharacterProfileDaoMixin(UserDataDaoMixinHost):
    @staticmethod
    def _decode_native_observation(row):
        if row is None:
            return None
        row = dict(row)
        cultivation = json.loads(row.pop("cultivation_json", "{}"))
        return {**row, **cultivation}

    def list_native_character_profile_observations(self) -> list[dict[str, Any]]:
        return [self._decode_native_observation(row) for row in self._rows(
            "SELECT character_id, character_level, breakthrough_stage, cultivation_json, updated_at_utc "
            "FROM character_profile_observation ORDER BY character_id"
        )]

    def get_native_character_profile_observation(self, character_id: int) -> dict[str, Any] | None:
        return self._decode_native_observation(self._one(
            "SELECT character_id, character_level, breakthrough_stage, cultivation_json, updated_at_utc "
            "FROM character_profile_observation WHERE character_id = ?", (character_id,),
        ))

    def patch_native_character_profiles(
        self, profiles: Sequence[Mapping[str, Any]], *,
        growth_defaults: Mapping[int, Mapping[str, int]], check: Callable[[], None],
    ) -> NativeRoleSyncResult:
        patches = normalize_native_profile_patches(profiles)
        if any("likeability_levels" in patch for patch in patches):
            raise UserDataValidationError("호감도가 아직 정식 캐릭터 카탈로그를 통해 연결되지 않았습니다")
        check()
        connection = self._db()
        warnings = []
        saved_count = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            for patch in patches:
                check()
                character_id = patch["character_id"]
                existing = self._one(
                    "SELECT character_level, breakthrough_stage FROM character_profile WHERE character_id = ?",
                    (character_id,),
                )
                observation = self.get_native_character_profile_observation(character_id) or {}
                baseline = dict(growth_defaults[character_id])
                if existing:
                    baseline.update(existing)
                baseline.update({name: observation[name] for name in GROWTH_FIELDS if observation.get(name) is not None})
                baseline.update({name: patch[name] for name in GROWTH_FIELDS if name in patch})
                level, stage = baseline.get("character_level"), baseline.get("breakthrough_stage")
                if type(level) is not int or type(stage) is not int or not _valid_breakthrough_stage_for_level(level, stage):
                    warnings.append(f"캐릭터 {character_id} · 레벨 및 돌파: 최종 조합이 일치하지 않아 기존 성장을 유지합니다; 알 수 없는 단계를 레벨로 추측하지 않습니다.")
                    for name in GROWTH_FIELDS:
                        patch.pop(name, None)
                if (observation.get("awakening_selection_initialized") is True
                        and "awakening_level" in patch
                        and "selected_awaken_effect_ids" not in patch
                        and len(observation["selected_awaken_effect_ids"]) > patch["awakening_level"]):
                    warnings.append(f"캐릭터 {character_id} · 각성 레벨: 저장된 선택과 충돌하여 기존 각성 구성을 유지합니다.")
                    patch.pop("awakening_level")
                if len(patch) == 1:
                    continue
                now = _utc_now()
                cultivation = {key: observation[key] for key in (
                    "skill_levels", "likeability_level_10_enabled", "fork_observed", "fork_id",
                    "fork_level", "fork_breakthrough_stage", "fork_refinement_level",
                    "awakening_level", "awakening_selection_initialized", "selected_awaken_effect_ids",
                ) if key in observation}
                for key, value in patch.items():
                    if key in GROWTH_FIELDS or key == "character_id":
                        continue
                    cultivation[key] = ({**cultivation.get(key, {}), **value} if key == "skill_levels" else value)
                if cultivation.get("fork_observed") and cultivation.get("fork_id") is None:
                    for key in ("fork_level", "fork_breakthrough_stage", "fork_refinement_level"):
                        cultivation[key] = None
                connection.execute(
                    """INSERT INTO character_profile_observation(
                           character_id, character_level, breakthrough_stage, updated_at_utc, cultivation_json
                       ) VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(character_id) DO UPDATE SET
                           character_level = COALESCE(excluded.character_level, character_profile_observation.character_level),
                           breakthrough_stage = COALESCE(excluded.breakthrough_stage, character_profile_observation.breakthrough_stage),
                           updated_at_utc = excluded.updated_at_utc, cultivation_json = excluded.cultivation_json""",
                    (character_id, patch.get("character_level"), patch.get("breakthrough_stage"), now,
                     json.dumps(cultivation, ensure_ascii=False)),
                )
                if existing:
                    connection.execute(
                        """UPDATE character_profile SET character_level = COALESCE(?, character_level),
                               breakthrough_stage = COALESCE(?, breakthrough_stage), updated_at_utc = ?
                           WHERE character_id = ?""",
                        (patch.get("character_level"), patch.get("breakthrough_stage"), now, character_id),
                    )
                    keys = [key for key in ("likeability_level_10_enabled", "fork_id", "fork_level", "fork_breakthrough_stage", "fork_refinement_level") if key in patch]
                    if keys:
                        connection.execute("UPDATE character_profile SET " + ", ".join(key + " = ?" for key in keys)
                                           + " WHERE character_id = ?", (*[patch[key] for key in keys], character_id))
                    for skill_id, level in patch.get("skill_levels", {}).items():
                        connection.execute(
                            "INSERT INTO character_profile_skill(character_id, skill_id, skill_level) VALUES (?, ?, ?) "
                            "ON CONFLICT(character_id, skill_id) DO UPDATE SET skill_level = excluded.skill_level",
                            (character_id, skill_id, level),
                        )
                saved_count += 1
            check()
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("네이티브 캐릭터 상태를 저장할 수 없음") from exc
        except BaseException:
            connection.rollback()
            raise
        return NativeRoleSyncResult(saved_count, tuple(warnings))
