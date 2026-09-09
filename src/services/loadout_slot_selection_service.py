# 解析并校验具名配装槽位选择。
"""Resolve explicit multi-loadout selections for apply and presentation flows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.storage.sqlite.user_data_support import UserDataValidationError


@dataclass(frozen=True)
class ResolvedLoadoutSlotSelection:
    """A current, non-archived named slot pinned to its saved plan."""

    slot_id: int
    character_id: int
    slot_key: str
    slot_name: str
    role_name: str
    plan_id: int
    source_snapshot_id: int | None
    plan: Mapping[str, Any]


class LoadoutSlotSelectionService:
    """Validate the explicit one-slot-per-role selection used by game writers."""

    def __init__(self, user_dao) -> None:
        self.user_dao = user_dao

    def list_current(self) -> tuple[ResolvedLoadoutSlotSelection, ...]:
        """List all visible current slot plans in deterministic role/slot order."""

        return tuple(
            self._resolve_slot_projection(row["slot"], row["plan"])
            for row in self.user_dao.list_current_loadout_slot_plans()
        )

    def resolve_default_roles(
        self,
        role_names: Iterable[str] | None = None,
        *,
        require_native_snapshot: bool = False,
    ) -> tuple[ResolvedLoadoutSlotSelection, ...]:
        """Pick one current slot per role for a role-based legacy action.

        Bulk actions historically accepted only role names.  With named slots,
        that must resolve through the slot's current-plan pointer rather than
        the old ``is_active`` history flag.  Prefer the primary slot and fall
        back to the earliest current slot when the primary slot is empty.
        """

        requested = tuple(
            dict.fromkeys(
                name.strip()
                for name in (role_names or ())
                if isinstance(name, str) and name.strip()
            )
        )
        current = self.list_current()
        by_role: dict[str, list[ResolvedLoadoutSlotSelection]] = {}
        for selection in current:
            by_role.setdefault(selection.role_name, []).append(selection)
        targets = requested or tuple(by_role)
        selected_ids: list[int] = []
        for role_name in targets:
            candidates = by_role.get(role_name, [])
            if not candidates:
                raise UserDataValidationError(
                    f"캐릭터 [{role_name}]의 현재 장비 세팅 슬롯 방안이 아직 저장되지 않았습니다"
                )
            selected = next(
                (row for row in candidates if row.slot_key == "primary"),
                candidates[0],
            )
            selected_ids.append(selected.slot_id)
        return self.resolve(
            selected_ids,
            require_native_snapshot=require_native_snapshot,
        )

    def resolve(
        self,
        slot_ids: Iterable[int],
        *,
        require_native_snapshot: bool = False,
    ) -> tuple[ResolvedLoadoutSlotSelection, ...]:
        """Resolve and validate user-selected slots before any game-side input.

        A target set may contain at most one current slot per character.  Physical
        equipment cannot be assigned to two selected roles, even if neither plan
        is allocation-locked.  When ``require_native_snapshot`` is set, every
        selected plan must originate from an nte-core snapshot.
        """

        raw_slot_ids = tuple(int(slot_id) for slot_id in slot_ids)
        if not raw_slot_ids:
            raise UserDataValidationError("장비 세팅 슬롯을 최소 하나 선택하세요")
        if len(set(raw_slot_ids)) != len(raw_slot_ids):
            raise UserDataValidationError("장착 대상에 중복 장비 세팅 슬롯이 있습니다")

        selections: list[ResolvedLoadoutSlotSelection] = []
        character_slots: dict[int, ResolvedLoadoutSlotSelection] = {}
        role_slots: dict[str, ResolvedLoadoutSlotSelection] = {}
        equipment_owner: dict[tuple[int, int], ResolvedLoadoutSlotSelection] = {}
        for slot_id in raw_slot_ids:
            slot = self.user_dao.get_loadout_slot(slot_id)
            if slot is None or slot.get("is_archived"):
                raise UserDataValidationError(f"장비 세팅 슬롯 {slot_id}이(가) 없거나 보관 처리되었습니다")
            plan = slot.get("current_plan")
            if not isinstance(plan, Mapping):
                raise UserDataValidationError(
                    f"장비 세팅 슬롯 [{slot.get('slot_name') or slot_id}]에 저장된 방안이 없습니다"
                )
            selection = self._resolve_slot_projection(slot, plan)
            previous = character_slots.setdefault(selection.character_id, selection)
            if previous is not selection:
                raise UserDataValidationError(
                    f"캐릭터 [{selection.role_name}]이(가) [{previous.slot_name}]과(와)"
                    f"[{selection.slot_name}]을(를) 동시에 선택했습니다. 한 번의 장착에는 슬롯 하나만 선택할 수 있습니다"
                )
            previous_role = role_slots.setdefault(selection.role_name, selection)
            if previous_role is not selection:
                raise UserDataValidationError(
                    f"캐릭터 표시 이름 [{selection.role_name}]이(가) 여러 캐릭터 인스턴스에 대응되어 동시에 장착할 수 없습니다"
                )
            if require_native_snapshot:
                self._require_native_snapshot(selection)
            self._assert_no_duplicate_equipment(selection, equipment_owner)
            selections.append(selection)
        return tuple(selections)

    def _resolve_slot_projection(
        self,
        slot: Mapping[str, Any],
        plan: Mapping[str, Any],
    ) -> ResolvedLoadoutSlotSelection:
        slot_id = int(slot["slot_id"])
        character_id = int(slot["character_id"])
        if int(plan.get("character_id") or 0) != character_id:
            raise UserDataValidationError(f"장비 세팅 슬롯 {slot_id}의 현재 방안이 이 캐릭터의 것이 아닙니다")
        payload = plan.get("payload")
        role_name = payload.get("source_role_name") if isinstance(payload, Mapping) else None
        if not isinstance(role_name, str) or not role_name.strip():
            raise UserDataValidationError(
                f"장비 세팅 슬롯 [{slot.get('slot_name') or slot_id}]의 방안에 캐릭터 이름이 없습니다"
            )
        plan_id = int(plan.get("plan_id") or 0)
        if plan_id <= 0:
            raise UserDataValidationError(f"장비 세팅 슬롯 {slot_id}의 현재 방안이 유효하지 않습니다")
        return ResolvedLoadoutSlotSelection(
            slot_id=slot_id,
            character_id=character_id,
            slot_key=str(slot.get("slot_key") or ""),
            slot_name=str(slot.get("slot_name") or ""),
            role_name=role_name.strip(),
            plan_id=plan_id,
            source_snapshot_id=(
                int(plan["source_snapshot_id"])
                if plan.get("source_snapshot_id") is not None
                else None
            ),
            plan=plan,
        )

    def _require_native_snapshot(self, selection: ResolvedLoadoutSlotSelection) -> None:
        custom_character_ids = {
            int(row["character_id"])
            for row in self.user_dao.list_custom_characters()
        }
        if selection.character_id in custom_character_ids:
            raise UserDataValidationError(
                f"[{selection.role_name} · {selection.slot_name}]은(는) 사용자 정의 캐릭터입니다."
                "고속 장착은 게임 내 캐릭터 인스턴스에만 적용되니 자동 장착을 사용하세요"
            )
        snapshot_id = selection.source_snapshot_id
        summary = (
            self.user_dao.inventory_snapshot_summary(snapshot_id)
            if snapshot_id is not None
            else None
        )
        if summary is None or summary.get("source") != "nte_core":
            raise UserDataValidationError(
                f"[{selection.role_name} · {selection.slot_name}]은(는) 공식 가방 스냅샷에서 온 것이 아닙니다."
                "고속 장착은 nte-core 원본 UID만 지원합니다"
            )

    @staticmethod
    def _assert_no_duplicate_equipment(
        selection: ResolvedLoadoutSlotSelection,
        equipment_owner: dict[tuple[int, int], ResolvedLoadoutSlotSelection],
    ) -> None:
        for assignment in selection.plan.get("assignments", ()):
            if not isinstance(assignment, Mapping):
                continue
            uid_slot = int(assignment.get("uid_slot") or 0)
            uid_serial = int(assignment.get("uid_serial") or 0)
            if uid_slot == 0:
                continue
            uid = (uid_slot, uid_serial)
            owner = equipment_owner.setdefault(uid, selection)
            if owner is not selection:
                raise UserDataValidationError(
                    f"장비 UID {uid}이(가) [{owner.role_name} · {owner.slot_name}]과(와)"
                    f"[{selection.role_name} · {selection.slot_name}]에 동시에 있습니다. 장착 대상을 하나만 남기세요"
                )
