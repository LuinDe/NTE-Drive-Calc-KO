# 将极速装配稳定快照的差异诊断集中在纯函数中，供单角色与批量流程复用。
"""Pure verification helpers for equipment-apply snapshots."""

from __future__ import annotations

from typing import Any


def plan_mismatch(
    *,
    items: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    core_assignment: dict[str, Any] | None,
    character_id: int,
    character_uid: dict[str, int],
    ignore_module_placement: bool = False,
) -> str | None:
    """返回完整方案与稳定快照的首个具体差异。"""

    expected_uids = {
        (assignment["uid_serial"], assignment["uid_slot"])
        for assignment in modules
    }
    if core_assignment is not None:
        expected_uids.add((
            core_assignment["uid_serial"],
            core_assignment["uid_slot"],
        ))

    mismatch = module_plan_mismatch(
        items=items,
        modules=modules,
        character_id=character_id,
        character_uid=character_uid,
        ignore_placement=ignore_module_placement,
    )
    if mismatch is not None:
        return mismatch

    if core_assignment is not None:
        core_pair = (core_assignment["uid_serial"], core_assignment["uid_slot"])
        by_uid = {(item["uid_serial"], item["uid_slot"]): item for item in items}
        verified_core = by_uid.get(core_pair)
        if verified_core is None:
            return f"카트리지 UID {core_pair}이(가) 재검토 스냅샷에 없습니다"
        if not verified_core["equipped"]:
            return f"카트리지 UID {core_pair}이(가) 장착되지 않았습니다"
        if verified_core["equipped_character_id"] != character_id:
            return (
                f"카트리지 UID {core_pair}이(가) 캐릭터"
                f"{verified_core['equipped_character_id']}에 장착되었으나 목표 캐릭터는 {character_id}입니다"
            )
        if verified_core["equipped_character_uid"] != character_uid:
            return (
                f"카트리지 UID {core_pair}의 캐릭터 인스턴스가 일치하지 않습니다:"
                f"실제 {verified_core['equipped_character_uid']}, 목표 {character_uid}"
            )

    actual_uids = {
        (item["uid_serial"], item["uid_slot"])
        for item in items
        if item["equipped"]
        and item["equipped_character_uid"] == character_uid
        and item["equipped_character_id"] == character_id
    }
    if actual_uids != expected_uids:
        missing = sorted(expected_uids - actual_uids)
        unexpected = sorted(actual_uids - expected_uids)
        return (
            "캐릭터 장비 집합이 방안과 일치하지 않습니다:"
            f"누락 {missing or '无'}, 초과 {unexpected or '无'}"
        )
    return None


def scoped_plan_mismatch(
    *,
    items: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    core_assignment: dict[str, Any] | None,
    character_id: int,
    character_uid: dict[str, int],
) -> str | None:
    """Check only whether planned items are equipped in a residual packet.

    Residual packets do not reliably carry target placement or owner fields.
    They are therefore suitable for detecting a missing equipment operation,
    not for asserting an exact role/grid loadout.
    """

    by_uid = {(item["uid_serial"], item["uid_slot"]): item for item in items}
    assignments = [*modules]
    if core_assignment is not None:
        assignments.append(core_assignment)
    for assignment in assignments:
        uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
        item = by_uid.get(uid_pair)
        if item is None:
            continue
        if not item["equipped"]:
            return f"장비 UID {uid_pair}이(가) 장착되지 않았습니다"
    return None


def module_plan_mismatch(
    *,
    items: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    character_id: int,
    character_uid: dict[str, int],
    allow_missing_placement: bool = False,
    ignore_placement: bool = False,
) -> str | None:
    """复核仅含驱动的方案，不要求更改角色当前卡带。"""

    by_uid = {(item["uid_serial"], item["uid_slot"]): item for item in items}
    for assignment in modules:
        uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
        item = by_uid.get(uid_pair)
        expected_placement = {
            "row": assignment["target_row"],
            "column": assignment["target_column"],
        }
        if item is None:
            return f"드라이브 UID {uid_pair}이(가) 재검토 스냅샷에 없습니다"
        if not item["equipped"]:
            return f"드라이브 UID {uid_pair}이(가) 장착되지 않았습니다"
        if item["equipped_character_id"] != character_id:
            return (
                f"드라이브 UID {uid_pair}이(가) 캐릭터 {item['equipped_character_id']}에 장착되었으나"
                f"목표 캐릭터는 {character_id}입니다"
            )
        if item["equipped_character_uid"] != character_uid:
            return (
                f"드라이브 UID {uid_pair}의 캐릭터 인스턴스가 일치하지 않습니다:"
                f"실제 {item['equipped_character_uid']}, 목표 {character_uid}"
            )
        if ignore_placement:
            continue
        actual_placement = item["equipped_placement"]
        if actual_placement is None and allow_missing_placement:
            # Equipment residual packets reliably identify the equipped item
            # and owner, but may omit grid placement.  Treat that as a
            # successful scoped state observation rather than a false repair.
            continue
        if actual_placement != expected_placement:
            return (
                f"드라이브 UID {uid_pair}의 위치가 일치하지 않습니다:"
                f"실제 {actual_placement}, 목표 {expected_placement}"
            )
    return None
