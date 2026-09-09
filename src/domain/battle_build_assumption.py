# 定义战报毕业配装假定的冻结来源与只读投影。
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


GRADUATION_ASSUMPTION_TITLE = "졸업 템플릿 가정 (원본 가방 없음)"
GRADUATION_ASSUMPTION_WARNING = (
    "완전한 원본 가방을 가져오지 못해 이번 전투는 졸업 템플릿으로 가정한 콘솔/드라이브로 계산합니다. 실측 전투 리포트는 그대로 보존됩니다."
)


def assumed_graduation_equipment(profile: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    assumption = profile.get("equipment_assumption")
    if not isinstance(assumption, Mapping):
        return None
    if assumption.get("kind") != "official_graduation" or assumption.get("version") != 1:
        return None
    items = assumption.get("items")
    if not isinstance(items, list):
        return None
    return deepcopy(items)


def has_graduation_assumption(build: Mapping[str, Any] | None) -> bool:
    return any(
        assumed_graduation_equipment(role.get("profile") or {}) is not None
        for role in (build or {}).get("characters") or ()
    )
