# 将角色页养成及可选当前配装投影为战报修改副本输入。
"""Build pointer-free battle-edit profiles from current role-page data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.services.battle_build_equipment_service import freeze_equipment_context


class BattleRolePageImportService:
    """Keep role-page equipment imports one-way into the battle edit copy."""

    @staticmethod
    def profiles(
        editor_data: Mapping[str, Any],
        *,
        include_equipment: bool,
    ) -> list[dict[str, Any]]:
        profiles = []
        for detail in editor_data.get("details") or ():
            profile = dict(detail["profile"])
            if include_equipment:
                context = (detail.get("equipment_contexts") or {}).get("current")
                if context is None or not bool(context.get("available")):
                    character = detail.get("character") or {}
                    name = str(
                        character.get("name_zh")
                        or character.get("character_id")
                        or "알 수 없는 캐릭터"
                    )
                    raise ValueError(f"{name}의 캐릭터 페이지에서 현재 콘솔/드라이브를 사용할 수 없습니다")
                profile.update({
                    "equipment_context_key": "current",
                    "equipment_context_title": str(
                        context.get("source_title")
                        or context.get("title")
                        or "게임 현재"
                    ),
                    "equipment_source_kind": str(
                        context.get("source_kind") or "role_page_current"
                    ),
                    "equipment_override": freeze_equipment_context(context),
                })
            profiles.append(profile)
        return profiles
