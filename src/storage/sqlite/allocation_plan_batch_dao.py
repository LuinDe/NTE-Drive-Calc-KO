# 原子保存计算方案并解除其他角色当前槽位对借出装备的占用。
from __future__ import annotations

from src.services.loadout_equipment_identity import source_snapshots_share_equipment_uids
from src.services.virtual_equipment_service import is_virtual_equipment_assignment

from .protocols import UserDataDaoMixinHost
from .loadout_comparison_guard import assert_comparison_baseline
from .user_data_support import UserDataError, UserDataValidationError, _integer, _utc_now


class AllocationPlanBatchDaoMixin(UserDataDaoMixinHost):
    def _create_calculated_slot_in_transaction(self, character_id, name):
        """Create the first visible slot only inside the calculated-plan transaction."""
        connection = self._db()
        rows = connection.execute(
            "SELECT slot_key, is_archived, sort_order FROM role_loadout_slot "
            "WHERE character_id = ?", (character_id,),
        ).fetchall()
        if any(not row[1] for row in rows):
            raise UserDataValidationError("대상 장비 세팅 슬롯이 변경되었습니다. 다시 선택하세요")
        keys = {str(row[0]) for row in rows}
        if "primary" not in keys:
            key = "primary"
        else:
            number = 1
            while f"slot-{number}" in keys:
                number += 1
            key = f"slot-{number}"
        now = _utc_now()
        cursor = connection.execute(
            "INSERT INTO role_loadout_slot("
            "character_id, slot_key, slot_name, sort_order, current_plan_id, "
            "is_archived, created_at_utc, updated_at_utc) "
            "VALUES (?, ?, ?, ?, NULL, 0, ?, ?)",
            (
                character_id, key, self._normalize_slot_name(name),
                max((int(row[2]) for row in rows), default=-1) + 1, now, now,
            ),
        )
        if cursor.lastrowid is None:
            raise UserDataError("대상 장비 세팅 슬롯 생성 후 slot_id가 반환되지 않았습니다")
        return int(cursor.lastrowid)

    def save_calculated_loadout_plans(self, plans, *, checkpoint, validate=None):
        """Persist targets and release other current owners in one transaction."""
        if not plans:
            raise UserDataValidationError("계산 방안을 최소 한 개는 저장해야 합니다")
        connection = self._db()
        if connection.in_transaction:
            raise UserDataError("계산 방안 일괄 저장에서는 트랜잭션을 중첩할 수 없습니다")
        try:
            connection.execute("BEGIN IMMEDIATE")
            checkpoint()
            if validate is not None:
                validate()
            slots, claims, summaries = {}, {}, {}
            resolved_plans = []
            created_slots = set()
            inventory_kinds = {}
            for source in plans:
                row = dict(source)
                if row.get("slot_id") is None:
                    name = row.get("create_slot_name")
                    if name is None:
                        raise UserDataValidationError("새 장비 세팅 슬롯에 캐릭터 이름이 없습니다")
                    row["slot_id"] = self._create_calculated_slot_in_transaction(
                        _integer(row["character_id"], "character_id", minimum=1), name,
                    )
                    created_slots.add(int(row["slot_id"]))
                slot_id = _integer(row.get("slot_id"), "slot_id", minimum=1)
                slot = self.get_loadout_slot(slot_id)
                if (slot is None or slot["is_archived"] or slot_id in slots
                        or int(slot["character_id"]) != int(row["character_id"])):
                    raise UserDataValidationError("계산 방안의 대상 슬롯이 유효하지 않거나 중복됩니다")
                slots[slot_id] = slot
                assert_comparison_baseline(self, slot_id, row.get("comparison_baseline"))
                resolved_plans.append(row)
                self.assert_loadout_slot_save_allowed(
                    slot_id, row["assignments"], source_snapshot_id=row["source_snapshot_id"],
                )
                snapshot_id = int(row["source_snapshot_id"])
                if snapshot_id not in summaries:
                    summaries[snapshot_id] = self.inventory_snapshot_summary(snapshot_id) or {}
                if not summaries[snapshot_id].get("complete"):
                    raise UserDataValidationError("계산 방안의 원본 가방 스냅샷이 불완전합니다")
                if snapshot_id not in inventory_kinds:
                    inventory_kinds[snapshot_id] = {
                        (int(item["uid_slot"]), int(item["uid_serial"])): item["kind"]
                        for item in connection.execute(
                            "SELECT uid_slot, uid_serial, kind FROM inventory_item WHERE snapshot_id = ?",
                            (snapshot_id,),
                        )
                    }
                for item in row["assignments"]:
                    if is_virtual_equipment_assignment(item):
                        continue
                    uid = (int(item["uid_slot"]), int(item["uid_serial"]))
                    if inventory_kinds[snapshot_id].get(uid) != item["kind"]:
                        raise UserDataValidationError("장비가 방안에서 동결한 가방 스냅샷에 없습니다")
                    previous = claims.get(uid)
                    if previous is not None and int(previous["character_id"]) != int(row["character_id"]):
                        raise UserDataValidationError("계산 방안 간에 동일한 실제 장비를 중복 사용할 수 없습니다")
                    claims[uid] = row

            owners = []
            for current in self.list_current_loadout_slot_plans():
                slot, plan = current["slot"], current["plan"]
                if int(slot["slot_id"]) in slots or plan.get("source_snapshot_id") is None:
                    continue
                snapshot_id = int(plan["source_snapshot_id"])
                if snapshot_id not in summaries:
                    summaries[snapshot_id] = self.inventory_snapshot_summary(snapshot_id) or {}
                removed, receivers = set(), {}
                for item in plan["assignments"]:
                    uid = (int(item["uid_slot"]), int(item["uid_serial"]))
                    target = claims.get(uid)
                    if target is None or int(target["character_id"]) == int(slot["character_id"]):
                        continue
                    target_snapshot = int(target["source_snapshot_id"])
                    if source_snapshots_share_equipment_uids(
                        snapshot_id, summaries[snapshot_id].get("source"),
                        target_snapshot, summaries[target_snapshot].get("source"),
                    ):
                        removed.add(uid)
                        receivers[int(target["slot_id"])] = int(target["character_id"])
                if not removed:
                    continue
                if plan.get("allocation_locked"):
                    raise UserDataValidationError("잠긴 슬롯 방안의 장비는 빌려 쓸 수 없습니다")
                owners.append((slot, plan, removed, receivers))

            # Share one frozen inventory lookup among all released owners too.
            inventories = {}
            for slot, plan, removed, receivers in owners:
                checkpoint()
                snapshot_id = int(plan["source_snapshot_id"])
                if snapshot_id not in inventories:
                    inventories[snapshot_id] = {
                        (int(item["uid_slot"]), int(item["uid_serial"])): item
                        for item in self.list_inventory_items(snapshot_id)
                    }
                target_slot, target_character = next(iter(receivers.items()))
                self._save_released_owner_slot(
                    slot, plan, removed, received_by_slot_id=target_slot,
                    received_by_character_id=target_character,
                    frozen_inventory=inventories[snapshot_id], received_by_slots=receivers,
                )
                current_id = self.get_loadout_slot(int(slot["slot_id"]))["current_plan_id"]
                connection.execute("UPDATE loadout_plan SET is_active = 0 WHERE slot_id = ?", (slot["slot_id"],))
                if plan["is_active"]:
                    connection.execute("UPDATE loadout_plan SET is_active = 1 WHERE plan_id = ?", (current_id,))

            result = []
            for row in resolved_plans:
                checkpoint()
                slot_id = int(row["slot_id"])
                arguments = {key: row[key] for key in (
                    "name", "character_id", "assignments", "source_snapshot_id", "status", "score", "payload",
                )}
                plan_id = self.save_loadout_plan(**arguments, slot_id=slot_id, is_active=False)
                if slots[slot_id]["slot_key"] == "primary" or slot_id in created_slots:
                    connection.execute("UPDATE loadout_plan SET is_active = 0 WHERE character_id = ?", (row["character_id"],))
                    connection.execute("UPDATE loadout_plan SET is_active = 1 WHERE plan_id = ?", (plan_id,))
                result.append(plan_id)
            checkpoint()
            connection.commit()
            return tuple(result)
        except BaseException:
            connection.rollback()
            raise
