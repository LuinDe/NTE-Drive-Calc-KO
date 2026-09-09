# 管理活动配装方案的计算保留锁及其装备占用查询。
"""SQLite access methods for allocation-plan reservation locks."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from src.services.loadout_equipment_identity import source_snapshots_share_equipment_uids
from src.services.virtual_equipment_service import is_virtual_equipment_assignment

from .protocols import UserDataDaoMixinHost
from .user_data_support import UserDataError, UserDataValidationError, _integer, _utc_now


class LoadoutPlanLockDaoMixin(UserDataDaoMixinHost):
    """Own the persisted lock state for current role loadout-slot plans.

    This is a reservation in the calculator, not the game's equipment-lock
    flag.  A lock can retain a partially filled plan without a core, but it
    must contain at least one real assignment and no virtual placeholder.
    """

    _LOCKABLE_ROLE_PLAN_SCHEMAS = frozenset({
        "allocation-official-snapshot-v1",
        "game-observed-loadout-v1",
    })

    @staticmethod
    def _is_role_loadout_plan(plan: Mapping[str, Any]) -> bool:
        payload = plan.get("payload")
        role_name = payload.get("source_role_name") if isinstance(payload, Mapping) else None
        return bool(
            isinstance(payload, Mapping)
            and payload.get("schema")
            in LoadoutPlanLockDaoMixin._LOCKABLE_ROLE_PLAN_SCHEMAS
            and isinstance(role_name, str)
            and role_name.strip()
        )

    @staticmethod
    def _validate_lockable_plan(plan: Mapping[str, Any], *, is_current_slot_plan: bool) -> None:
        if not is_current_slot_plan:
            raise UserDataValidationError("장비 세팅 슬롯의 현재 방안만 잠글 수 있습니다")
        if not LoadoutPlanLockDaoMixin._is_role_loadout_plan(plan):
            raise UserDataValidationError("활성 캐릭터 장비 세팅 방안만 잠글 수 있습니다")
        assignments = tuple(plan.get("assignments") or ())
        if not assignments:
            raise UserDataValidationError("빈 방안은 잠글 수 없습니다")
        for assignment in assignments:
            raw_assignment = assignment.get("raw_assignment") or assignment
            if is_virtual_equipment_assignment(raw_assignment):
                raise UserDataValidationError("가상 자리 표시 장비가 있는 방안은 잠글 수 없습니다")
            try:
                slot = int(assignment["uid_slot"])
                serial = int(assignment["uid_serial"])
            except (KeyError, TypeError, ValueError) as exc:
                raise UserDataValidationError("방안에 잘못된 장비 UID가 있어 잠글 수 없습니다") from exc
            if slot <= 0 or serial <= 0:
                raise UserDataValidationError("방안에 실제가 아닌 장비가 있어 잠글 수 없습니다")

    def set_allocation_lock(self, plan_id: int, locked: bool) -> bool:
        """Set a plan reservation lock after validating its visible state."""

        raw_plan_id = _integer(plan_id, "plan_id", minimum=1)
        plan = self.get_loadout_plan(raw_plan_id)
        if plan is None:
            raise UserDataValidationError("잠글 장비 세팅 방안을 찾지 못했습니다")
        target = bool(locked)
        if target:
            self._validate_lockable_plan(
                plan,
                is_current_slot_plan=self.is_current_loadout_slot_plan(raw_plan_id),
            )
        elif not self.is_current_loadout_slot_plan(raw_plan_id):
            raise UserDataValidationError("장비 세팅 슬롯 현재 방안의 잠금만 해제할 수 있습니다")
        if bool(plan.get("allocation_locked")) == target:
            return False
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE loadout_plan
                SET allocation_locked = ?, updated_at_utc = ?
                WHERE plan_id = ? AND updated_at_utc = ?
                """,
                (
                    int(target),
                    _utc_now(),
                    raw_plan_id,
                    str(plan.get("updated_at_utc") or ""),
                ),
            )
            if cursor.rowcount != 1:
                raise UserDataValidationError("장비 세팅 방안이 바뀌었습니다. 새로 고친 뒤 다시 시도하세요")
            connection.commit()
            return True
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("세팅 잠금 상태를 갱신할 수 없습니다") from exc
        except BaseException:
            connection.rollback()
            raise

    def list_allocation_locked_loadout_plans_by_role(self) -> dict[str, dict[str, Any]]:
        """Return one current locked plan per role for legacy callers."""

        locked: dict[str, dict[str, Any]] = {}
        for plan in self.list_allocation_locked_loadout_plans():
            payload = plan.get("payload")
            role_name = payload.get("source_role_name") if isinstance(payload, Mapping) else None
            if (
                plan.get("is_active")
                and plan.get("allocation_locked")
                and self._is_role_loadout_plan(plan)
                and isinstance(role_name, str)
            ):
                locked[role_name] = plan
        return locked

    def list_allocation_locked_loadout_plans(self) -> list[dict[str, Any]]:
        """Return every current slot plan reserving equipment for calculation."""

        plans: list[dict[str, Any]] = []
        for plan in self.list_loadout_plans():
            if (
                plan.get("allocation_locked")
                and self._is_role_loadout_plan(plan)
                and self.is_current_loadout_slot_plan(int(plan["plan_id"]))
            ):
                plans.append(plan)
        return plans

    def list_allocation_locked_equipment_owners(self) -> list[dict[str, Any]]:
        """Return physical equipment occupied by every active locked plan."""

        return self._rows(
            """
            SELECT item.uid_slot, item.uid_serial, item.kind,
                   plan.plan_id, plan.character_id, plan.source_snapshot_id,
                   plan.updated_at_utc
            FROM loadout_plan_item AS item
            JOIN loadout_plan AS plan USING(plan_id)
            JOIN role_loadout_slot AS slot ON slot.current_plan_id = plan.plan_id
            WHERE plan.allocation_locked = 1
              AND slot.is_archived = 0
              AND item.uid_slot > 0
            ORDER BY plan.updated_at_utc DESC, plan.plan_id DESC, item.ordinal
            """
        )

    def assert_allocation_lock_invariants(self) -> None:
        """Reject locked UID ownership shared by different characters."""

        owners: dict[tuple[int, int], int] = {}
        for plan in self.list_allocation_locked_loadout_plans():
            character_id = int(plan["character_id"])
            for item in plan.get("assignments") or ():
                uid = (int(item["uid_slot"]), int(item["uid_serial"]))
                if uid[0] <= 0:
                    continue
                previous_character_id = owners.setdefault(uid, character_id)
                if previous_character_id != character_id:
                    raise UserDataValidationError(
                        "두 잠긴 방안이 같은 장비를 차지해 자동으로 복구할 수 없습니다. 먼저 둘 중 한 방안의 잠금을 해제하세요"
                    )

    def assert_active_allocation_locks_preserved(
        self,
        *,
        target_characters: set[int],
        claimed_uids: set[tuple[int, int]],
    ) -> None:
        """Prevent every active-plan writer from overwriting a reservation."""

        locked_plans = [
            plan
            for plan in self.list_allocation_locked_loadout_plans()
        ]
        if {
            int(plan["character_id"])
            for plan in locked_plans
        }.intersection(target_characters):
            raise UserDataValidationError(
                "잠긴 방안은 다시 저장하거나 교체할 수 없습니다. 먼저 장비 세팅 페이지에서 잠금을 해제하세요"
            )
        locked_claims = {
            (int(item["uid_slot"]), int(item["uid_serial"]))
            for plan in locked_plans
            for item in plan["assignments"]
            if int(item["uid_slot"]) > 0
        }
        conflicting_uids = sorted(claimed_uids.intersection(locked_claims))
        if conflicting_uids:
            labels = ", ".join(
                f"({slot}, {serial})" for slot, serial in conflicting_uids[:3]
            )
            raise UserDataValidationError(f"잠긴 방안의 장비는 대여할 수 없습니다: {labels}")

    def assert_loadout_slot_save_allowed(
        self,
        slot_id: int,
        assignments: Sequence[Mapping[str, Any]],
        *,
        source_snapshot_id: int | None = None,
    ) -> None:
        """Reject overwriting a locked slot or borrowing another locked slot's UID."""

        raw_slot_id = _integer(slot_id, "slot_id", minimum=1)
        slot = self.get_loadout_slot(raw_slot_id)
        if slot is None or slot.get("is_archived"):
            raise UserDataValidationError("장비 세팅 슬롯이 없거나 보관 처리되었습니다")
        current = slot.get("current_plan") or {}
        if current.get("allocation_locked"):
            raise UserDataValidationError("잠긴 방안은 덮어쓸 수 없습니다. 먼저 잠금을 해제하세요")
        claimed_uids: set[tuple[int, int]] = set()
        for assignment in assignments:
            if not isinstance(assignment, Mapping):
                continue
            if is_virtual_equipment_assignment(assignment):
                continue
            try:
                uid = (int(assignment["uid_slot"]), int(assignment["uid_serial"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise UserDataValidationError("방안에 잘못된 장비 UID가 있습니다") from exc
            if uid[0] > 0 and uid[1] > 0:
                claimed_uids.add(uid)
        target_summary = (
            self.inventory_snapshot_summary(int(source_snapshot_id)) or {}
            if source_snapshot_id is not None
            else {}
        )
        locked_uids: set[tuple[int, int]] = set()
        for plan in self.list_allocation_locked_loadout_plans():
            if int(plan.get("slot_id") or 0) == raw_slot_id:
                continue
            if int(plan["character_id"]) == int(slot["character_id"]):
                continue
            owner_snapshot_id = plan.get("source_snapshot_id")
            if source_snapshot_id is not None and owner_snapshot_id is not None:
                owner_summary = self.inventory_snapshot_summary(int(owner_snapshot_id)) or {}
                if not source_snapshots_share_equipment_uids(
                    int(source_snapshot_id),
                    target_summary.get("source"),
                    int(owner_snapshot_id),
                    owner_summary.get("source"),
                ):
                    continue
            locked_uids.update(
                (int(item["uid_slot"]), int(item["uid_serial"]))
                for item in plan.get("assignments") or ()
                if int(item["uid_slot"]) > 0
            )
        collisions = sorted(claimed_uids.intersection(locked_uids))
        if collisions:
            label = ", ".join(f"({slot}, {serial})" for slot, serial in collisions[:3])
            raise UserDataValidationError(f"잠긴 슬롯 방안의 장비는 대여할 수 없습니다: {label}")
