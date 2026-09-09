# 将游戏导入配装转换为统一配装方案投影。
"""Project game-equipped inventory rows into importable loadout plans."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.loadout_plan_scores import exact_assignment_score_total
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


class GameLoadoutProjectionError(RuntimeError):
    """The frozen game snapshot can no longer support the requested action."""


@dataclass(frozen=True)
class GameLoadoutRoleProjection:
    snapshot_id: int
    character_id: int
    role_name: str
    items: tuple[dict[str, Any], ...]
    assignments: tuple[dict[str, Any], ...]
    equipment_fingerprint: str
    importable: bool
    status: str
    reason: str
    imported: bool
    existing_plan_id: int | None
    existing_plan_name: str
    existing_plan_locked: bool


@dataclass(frozen=True)
class GameLoadoutSnapshotProjection:
    snapshot_id: int | None
    source: str
    captured_at_utc: str
    supported: bool
    message: str
    equipped_item_count: int
    roles: tuple[GameLoadoutRoleProjection, ...]


@dataclass(frozen=True)
class GameLoadoutImportRequest:
    projection: GameLoadoutRoleProjection
    score: float
    assignment_scores: Mapping[str, float]
    slot_id: int | None = None


def _geometry_key(value: Any) -> str:
    return str(value or "").removeprefix("EquipmentGeometry_").casefold()


def _equipment_fingerprint(items: Sequence[Mapping[str, Any]]) -> str:
    rows = sorted(
        (
            str(item.get("kind") or ""),
            int(item.get("uid_slot") or 0),
            int(item.get("uid_serial") or 0),
        )
        for item in items
    )
    payload = "|".join(f"{kind}:{slot}:{serial}" for kind, slot, serial in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _placement_candidates(
    shape_cells: Sequence[Mapping[str, Any]],
    available_cells: frozenset[tuple[int, int]],
) -> tuple[tuple[int, int, frozenset[tuple[int, int]]], ...]:
    offsets = tuple(
        (int(cell["x"]), int(cell["y"]))
        for cell in shape_cells
    )
    result = []
    for row in range(1, 6):
        for column in range(1, 6):
            occupied = frozenset(
                (row + delta_row, column + delta_column)
                for delta_row, delta_column in offsets
            )
            if occupied and occupied <= available_cells:
                result.append((row, column, occupied))
    return tuple(result)


def _module_assignments(
    modules: Sequence[Mapping[str, Any]],
    *,
    equipment_plan: Mapping[str, Any],
    shapes: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...] | None:
    available_cells = frozenset(
        (int(cell["row"]), int(cell["column"]))
        for cell in equipment_plan.get("cells") or ()
    )
    candidates = []
    for item in modules:
        shape = shapes.get(_geometry_key(item.get("geometry")))
        if shape is None:
            return None
        placements = _placement_candidates(shape.get("cells") or (), available_cells)
        if not placements:
            return None
        candidates.append((dict(item), placements))
    candidates.sort(
        key=lambda row: (
            len(row[1]),
            _geometry_key(row[0].get("geometry")),
            int(row[0].get("uid_slot") or 0),
            int(row[0].get("uid_serial") or 0),
        )
    )

    selected: list[tuple[dict[str, Any], int, int]] = []
    dead_states: set[tuple[int, frozenset[tuple[int, int]]]] = set()

    def place(index: int, occupied: frozenset[tuple[int, int]]) -> bool:
        state = (index, occupied)
        if state in dead_states:
            return False
        if index >= len(candidates):
            return occupied == available_cells
        item, placements = candidates[index]
        for row, column, cells in placements:
            if occupied & cells:
                continue
            selected.append((item, row, column))
            if place(index + 1, occupied | cells):
                return True
            selected.pop()
        dead_states.add(state)
        return False

    if not place(0, frozenset()):
        return None
    assignments = [
        {
            "uid_serial": int(item["uid_serial"]),
            "uid_slot": int(item["uid_slot"]),
            "kind": "module",
            "target_row": row,
            "target_column": column,
            "rotation": 0,
            "geometry": item.get("geometry"),
            "grid_count": item.get("grid_count"),
        }
        for item, row, column in selected
    ]
    assignments.sort(
        key=lambda item: (
            int(item.get("target_row") or 0),
            int(item.get("target_column") or 0),
        )
    )
    return tuple(assignments)


class GameLoadoutProjectionService:
    """Read and import game-observed loadouts from one immutable snapshot."""

    def __init__(
        self,
        user_dao: UserDataDao,
        static_dao: StaticGameDataDao,
    ) -> None:
        self.user_dao = user_dao
        self.static_dao = static_dao

    def project_current(self) -> GameLoadoutSnapshotProjection:
        snapshot_id = self.user_dao.current_inventory_snapshot_id()
        if snapshot_id is None:
            return GameLoadoutSnapshotProjection(
                None, "", "", False, "먼저 가방 동기화를 한 번 완료하세요.", 0, (),
            )
        summary = self.user_dao.inventory_snapshot_summary(snapshot_id) or {}
        source = str(summary.get("source") or "")
        captured_at = str(summary.get("captured_at_utc") or "")
        if source != "nte_core":
            return GameLoadoutSnapshotProjection(
                snapshot_id,
                source,
                captured_at,
                False,
                "현재 스냅샷은 nte-core 동기화 결과가 아니라 신뢰할 수 있는 게임 내 장비 귀속 정보가 없습니다.",
                int(summary.get("equipped_count") or 0),
                (),
            )
        equipped = self.user_dao.list_inventory_items(snapshot_id, equipped=True)
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for item in equipped:
            character_id = item.get("equipped_character_id")
            if character_id is not None:
                grouped[int(character_id)].append(dict(item))
        characters = {
            int(row["character_id"]): str(row.get("name_zh") or row["character_id"])
            for row in self.static_dao.list_characters()
        }
        shapes = {
            _geometry_key(shape["shape_id"]): shape
            for shape in self.static_dao.list_shapes()
        }
        active_plans = {
            int(plan["character_id"]): plan
            for plan in self.user_dao.list_loadout_plans()
            if plan.get("is_active")
        }
        roles = []
        for character_id, items in sorted(
            grouped.items(),
            key=lambda row: characters.get(row[0], str(row[0])),
        ):
            role_name = characters.get(character_id, str(character_id))
            modules = sorted(
                (item for item in items if item.get("kind") == "module"),
                key=lambda item: (int(item["uid_slot"]), int(item["uid_serial"])),
            )
            cores = sorted(
                (item for item in items if item.get("kind") == "core"),
                key=lambda item: (int(item["uid_slot"]), int(item["uid_serial"])),
            )
            equipment_plan = self.static_dao.get_equipment_plan(character_id)
            module_assignments = (
                _module_assignments(
                    modules,
                    equipment_plan=equipment_plan,
                    shapes=shapes,
                )
                if equipment_plan is not None and modules
                else None
            )
            importable = module_assignments is not None and len(cores) <= 1
            if not modules:
                status, reason = "empty", "게임 스냅샷에 장착된 드라이브가 없습니다."
            elif len(cores) > 1:
                status, reason = "incomplete", "장착된 카트리지가 정확히 한 장 있어야 합니다."
            elif module_assignments is None:
                status, reason = "layout_unresolved", "현재 드라이브 형태로는 이 캐릭터의 완전한 청사진을 복원할 수 없습니다."
            elif not cores:
                status, reason = "missing_tape", "현재 카트리지만 없으므로 완전한 드라이브 청사진을 먼저 가져올 수 있습니다."
            else:
                status, reason = "ready", ""
            assignments = list(module_assignments or ())
            if len(cores) == 1:
                core = cores[0]
                assignments.append({
                    "uid_serial": int(core["uid_serial"]),
                    "uid_slot": int(core["uid_slot"]),
                    "kind": "core",
                    "target_row": None,
                    "target_column": None,
                    "rotation": 0,
                })
            fingerprint = _equipment_fingerprint(items)
            active = active_plans.get(character_id)
            payload = dict((active or {}).get("payload") or {})
            imported = bool(
                active
                and payload.get("source") == "game_inventory"
                and payload.get("equipment_fingerprint") == fingerprint
            )
            roles.append(GameLoadoutRoleProjection(
                snapshot_id=snapshot_id,
                character_id=character_id,
                role_name=role_name,
                items=tuple(dict(item) for item in items),
                assignments=tuple(dict(item) for item in assignments),
                equipment_fingerprint=fingerprint,
                importable=importable,
                status=status,
                reason=reason,
                imported=imported,
                existing_plan_id=(int(active["plan_id"]) if active else None),
                existing_plan_name=str((active or {}).get("name") or ""),
                existing_plan_locked=bool((active or {}).get("allocation_locked")),
            ))
        return GameLoadoutSnapshotProjection(
            snapshot_id,
            source,
            captured_at,
            True,
            "",
            len(equipped),
            tuple(roles),
        )

    def import_role(
        self,
        projection: GameLoadoutRoleProjection,
        *,
        score: float,
        assignment_scores: Mapping[str, float],
        slot_id: int | None = None,
    ) -> int:
        return self.import_roles((GameLoadoutImportRequest(
            projection=projection,
            score=score,
            assignment_scores=assignment_scores,
            slot_id=slot_id,
        ),))[0]

    def import_roles(
        self,
        requests: Sequence[GameLoadoutImportRequest],
    ) -> tuple[int, ...]:
        if not requests:
            raise GameLoadoutProjectionError("가져올 게임 내 방안이 없습니다.")
        current_snapshot_id = self.user_dao.current_inventory_snapshot_id()
        if any(
            request.projection.snapshot_id != current_snapshot_id
            for request in requests
        ):
            raise GameLoadoutProjectionError("가방 스냅샷이 바뀌었습니다. 게임 내 모드를 새로 고친 뒤 다시 시도하세요.")
        all_items = self.user_dao.list_inventory_items(
            int(current_snapshot_id),
            equipped=True,
        )
        items_by_character: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for item in all_items:
            character_id = item.get("equipped_character_id")
            if character_id is not None:
                items_by_character[int(character_id)].append(dict(item))

        plans = []
        character_ids: set[int] = set()
        for request in requests:
            projection = request.projection
            if not projection.importable:
                raise GameLoadoutProjectionError(
                    projection.reason or f"[{projection.role_name}]의 현재 게임 내 방안은 가져올 수 없습니다."
                )
            if projection.character_id in character_ids:
                raise GameLoadoutProjectionError("원클릭 가져오기에 같은 캐릭터를 중복 포함할 수 없습니다.")
            character_ids.add(projection.character_id)
            current_items = items_by_character.get(projection.character_id, [])
            if _equipment_fingerprint(current_items) != projection.equipment_fingerprint:
                raise GameLoadoutProjectionError(
                    f"[{projection.role_name}]의 게임 내 장비가 바뀌었습니다. 새로 고친 뒤 다시 시도하세요."
                )
            exact_score = exact_assignment_score_total(
                projection.assignments,
                request.assignment_scores,
            )
            if exact_score is None:
                raise GameLoadoutProjectionError(
                    f"[{projection.role_name}] 가져오기 방안에 완전한 개별 점수가 없습니다."
                )
            if abs(float(request.score) - exact_score) > 1e-6:
                raise GameLoadoutProjectionError(
                    f"[{projection.role_name}] 방안의 총점이 개별 점수와 일치하지 않습니다."
                )
            plans.append({
                "slot_id": request.slot_id,
                "name": f"게임 내 방안: {projection.role_name}",
                "character_id": projection.character_id,
                "source_snapshot_id": projection.snapshot_id,
                "status": (
                    "ready"
                    if any(
                        str(item.get("kind")) == "core"
                        for item in projection.assignments
                    )
                    else "incomplete"
                ),
                "score": exact_score,
                "assignments": [dict(item) for item in projection.assignments],
                "payload": {
                    "schema": "game-observed-loadout-v1",
                    "source": "game_inventory",
                    "source_role_name": projection.role_name,
                    "equipment_fingerprint": projection.equipment_fingerprint,
                    "missing_tape": not any(
                        str(item.get("kind")) == "core"
                        for item in projection.assignments
                    ),
                    "assignment_scores": {
                        str(uid): float(value)
                        for uid, value in request.assignment_scores.items()
                    },
                },
            })
        slot_ids = [plan.get("slot_id") for plan in plans]
        if any(slot_id is not None for slot_id in slot_ids):
            if any(slot_id is None for slot_id in slot_ids):
                raise GameLoadoutProjectionError(
                    "일괄 게임 내 가져오기는 캐릭터마다 장비 세팅 슬롯을 지정해야 하며 이전 주력 진입점과 섞어 쓸 수 없습니다."
                )
            try:
                return tuple(int(plan_id) for plan_id in self.user_dao.save_plans_to_slots(plans))
            except Exception as exc:
                raise GameLoadoutProjectionError(f"지정한 장비 세팅 슬롯 저장 실패: {exc}") from exc
        return tuple(
            int(plan_id)
            for plan_id in self.user_dao.replace_active_loadout_plans(plans)
        )
