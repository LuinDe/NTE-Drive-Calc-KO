# 定义倒带所选槽位的冻结身份与驱动完整性投影。
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping



@dataclass(frozen=True, slots=True)
class RewindSlotReference:
    character_id: int
    slot_id: int
    plan_id: int | None

    def __post_init__(self):
        values = (self.character_id, self.slot_id) + (() if self.plan_id is None else (self.plan_id,))
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("슬롯 선택 식별 정보는 양의 정수여야 합니다")


@dataclass(frozen=True, slots=True)
class RewindSavedDrive:
    shape_id: str
    area: int
    score: float


@dataclass(frozen=True, slots=True)
class RewindSlotSummary:
    reference: RewindSlotReference
    slot_name: str
    slot_key: str
    sort_order: int
    source_snapshot_id: int | None
    score: float | None
    state: str
    reason: str
    drives: tuple[RewindSavedDrive, ...] = ()


def finite_score(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) and number >= 0 else None


def positive_id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    if isinstance(value, str) and not value.isdecimal():
        return None
    number = int(value)
    return number if number > 0 else None


def supported_saved_plan(plan):
    payload = plan.get("payload") or {}
    schema = payload.get("schema")
    return schema in {"allocation-official-snapshot-v1", "saved-state-official-loadout-v1"} or (
        schema == "game-observed-loadout-v1" and payload.get("source") == "game_inventory"
    )


def shape_key(value):
    return str(value or "").removeprefix("EquipmentGeometry_").casefold()



def default_slot(rows, explicit_slot_id) -> RewindSlotReference | None:
    if explicit_slot_id is not None:
        chosen = next((row for row in rows if row.reference.slot_id == explicit_slot_id), None)
        return chosen.reference if chosen else (
            RewindSlotReference(rows[0].reference.character_id, explicit_slot_id, None) if rows else None
        )
    if len(rows) == 1:
        return rows[0].reference
    eligible = [row for row in rows if row.state == "ready" and row.score is not None]
    if not eligible:
        return None
    return min(eligible, key=lambda row: (-row.score, row.slot_key != "primary",
                                         row.sort_order, row.reference.slot_id)).reference


def read_slot_preferences(preferences, *, strategy="balanced") -> dict[int, int | None]:
    version = preferences.get("slot_selection_version")
    if type(version) is int and version == 2:
        maps = preferences.get("selected_slots_by_strategy")
        raw = maps.get(strategy) if isinstance(maps, Mapping) else None
    else:
        # Seed each strategy separately from the legacy shared choices.
        raw = preferences.get("selected_slots")
    if not isinstance(raw, Mapping):
        return {}
    return {identifier: value if type(version) is int and version in {1, 2} and type(value) is int and value > 0 else None
            for key, value in raw.items() if (identifier := positive_id(key)) is not None}


def references_for_roles(roles, saved_ids):
    result = {}
    for role in roles:
        identifier = role.character_id
        if identifier in saved_ids:
            slot_id = saved_ids[identifier]
            result[identifier] = (default_slot(role.slots, slot_id) if slot_id is not None else None)
            if slot_id is not None and result[identifier] is None:
                result[identifier] = RewindSlotReference(identifier, slot_id, None)
        else:
            result[identifier] = default_slot(role.slots, None)
    return result
