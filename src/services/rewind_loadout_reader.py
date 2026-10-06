# 在账号一致读取边界内投影倒带槽位及其来源装备。
from dataclasses import replace
from collections.abc import Mapping

from src.domain.rewind_loadout import positive_id
from src.services.rewind_loadout_projection import inspect_slot
from src.services.virtual_equipment_service import normalized_equipment_assignment, is_virtual_equipment_assignment


def snapshot_complete(summary):
    if not summary:
        return False
    complete = summary.get("complete")
    if complete is not True and not (type(complete) is int and complete == 1):
        return False
    declared, stored = summary.get("declared_item_count"), summary.get("stored_item_count")
    return (type(declared) is int and type(stored) is int
            and declared >= 0 and declared == stored)


class RewindLoadoutReader:
    def __init__(self, dao, shapes):
        self.dao, self.shapes = dao, shapes
        self.items = {}
        self.summaries = {}

    def inspect(self, slot):
        try:
            return self._inspect(slot)
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
            plan = slot.get("current_plan") or {}
            plan_id = positive_id(plan.get("plan_id")) if isinstance(plan, Mapping) else None
            base = inspect_slot(slot, {}, {}, self.shapes)
            return replace(base, reference=replace(base.reference, plan_id=plan_id),
                           state="layout_invalid", reason="장비 세팅 데이터가 부족합니다. 다시 계산하고 저장하세요.")

    def _inspect(self, slot):
        plan = slot.get("current_plan") or {}
        source = positive_id(plan.get("source_snapshot_id"))
        inventory = {}
        if source is not None:
            if source not in self.summaries:
                self.summaries[source] = self.dao.inventory_snapshot_summary(source)
            if snapshot_complete(self.summaries[source]):
                uids = set()
                for raw in plan.get("assignments") or ():
                    row = normalized_equipment_assignment(raw)
                    if row.get("kind") == "module" and not is_virtual_equipment_assignment(row):
                        uids.add((int(row["uid_serial"]), int(row["uid_slot"])))
                key = (source, frozenset(uids))
                if key not in self.items:
                    self.items[key] = {
                        (int(row["uid_slot"]), int(row["uid_serial"])): row
                        for row in self.dao.list_inventory_items(source, kind="module", uids=uids)
                    }
                inventory = self.items[key]
        return inspect_slot(slot, plan, inventory, self.shapes)
