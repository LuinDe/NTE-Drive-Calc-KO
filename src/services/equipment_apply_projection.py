# 将已接受指令投影到原冻结库存，不替换集合、目标或快照指针。
from __future__ import annotations


def project_dispatched_loadouts(
    user_dao,
    applied: list[dict],
    *,
    snapshot_id: int,
    frozen_plans: dict[int, dict] | None = None,
) -> int:
    """Reflect submitted loadouts in the pinned warehouse projection.

    The immutable inventory membership and current snapshot pointer remain
    untouched.  Later nte-core inventory events overwrite these requested
    per-item states; their only purpose is verification/retry.
    """

    projector = getattr(user_dao, "apply_inventory_command_state_projection", None)
    if not applied or not callable(projector):
        return 0
    rows = user_dao.list_inventory_items(snapshot_id)
    by_uid = {(int(row.get("uid_slot") or 0), int(row.get("uid_serial") or 0)): row for row in rows}
    target_character_uids = {
        (int(role["character_uid"]["slot"]), int(role["character_uid"]["serial"]))
        for role in applied
        if isinstance(role.get("character_uid"), dict)
    }
    projected: list[dict] = []
    for row in rows:
        character_uid = row.get("equipped_character_uid")
        if not isinstance(character_uid, dict):
            continue
        pair = (int(character_uid.get("slot") or 0), int(character_uid.get("serial") or 0))
        if pair not in target_character_uids:
            continue
        item = dict(row)
        item["uid"] = {"slot": row["uid_slot"], "serial": row["uid_serial"]}
        item.update(
            {
                "equipped": False,
                "equipped_character_id": None,
                "equipped_character_uid": None,
                "equipped_placement": None,
            }
        )
        projected.append(item)
    for role in applied:
        plan = (
            frozen_plans.get(int(role["plan_id"]))
            if frozen_plans is not None
            else user_dao.get_loadout_plan(int(role["plan_id"]))
        )
        if plan is None:
            continue
        character_uid = dict(role["character_uid"])
        for assignment in plan.get("assignments") or ():
            pair = (
                int(assignment.get("uid_slot") or 0),
                int(assignment.get("uid_serial") or 0),
            )
            row = by_uid.get(pair)
            if row is None:
                continue
            item = dict(row)
            item["uid"] = {"slot": pair[0], "serial": pair[1]}
            placement = None
            if assignment.get("kind") == "module":
                placement = {
                    "row": assignment.get("target_row"),
                    "column": assignment.get("target_column"),
                }
            item.update(
                {
                    "equipped": True,
                    "equipped_character_id": int(role["character_id"]),
                    "equipped_character_uid": character_uid,
                    "equipped_placement": placement,
                }
            )
            projected.append(item)
    return int(projector(snapshot_id, projected))
