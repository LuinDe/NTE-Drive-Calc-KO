# 将不可变导入装备证据投影到战报配置副本。
"""Project immutable imported equipment evidence onto a battle build copy."""

from __future__ import annotations

from typing import Any

from src.storage.sqlite.user_data_dao import UserDataError


def apply_import_equipment_locks(
    build: dict[str, Any] | None,
    locks: dict[int, dict[str, Any]],
) -> None:
    if build is None or not locks:
        return
    for character in build.get("characters") or ():
        lock = locks.get(int(character["character_id"]))
        if lock is None:
            raise UserDataError("가져온 전투 리포트에 현재 캐릭터의 고정 장비 세팅이 없습니다")
        character["equipment"] = [
            dict(item) for item in lock.get("equipment") or ()
        ]
        character["equipment_source_kind"] = "imported_locked"
        character["equipment_sha256"] = str(lock["equipment_sha256"])


__all__ = ["apply_import_equipment_locks"]
