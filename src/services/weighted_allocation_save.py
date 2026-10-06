# 保存冻结加权配装与对应槽位的同权重变动记录。
from __future__ import annotations

from typing import Any, Mapping

from src.optimizer.contracts import (
    EQUIP_SHAPE_ID, EQUIP_UID, ROLE_BLUEPRINT_LAYOUT, ROLE_EQUIPPED_DRIVES, ROLE_EQUIPPED_TAPE,
)
from src.services.allocation_solver import RoleAllocationOption
from src.services.sqlite_allocation_inventory import legacy_shape_id

from src.services.allocation_main_value_service import weighted_option_tape_main_values
from src.services.saved_state_loadout_bridge import SavedStateLoadoutBridge
from src.services.weighted_loadout_comparison_service import select_frozen_comparison
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao

def save_weighted_preview_records(
    preview,
    *,
    slot_ids_by_character: Mapping[int, int] | None = None,
) -> tuple[int, ...]:
    result = preview.result
    with UserDataDao(preview.user_database_path) as user_dao, StaticGameDataDao(preview.static_database_path) as static_dao:
        if preview.static_file_identity is not None:
            current_stat = static_dao.database_path.stat()
            if (current_stat.st_size, current_stat.st_mtime_ns) != preview.static_file_identity:
                raise RuntimeError("계산에 사용된 정적 데이터셋이 업데이트되었습니다. 계산을 다시 실행하세요.")
        if static_dao.summary()["dataset"]["dataset_id"] != preview.static_dataset.dataset_id:
            raise RuntimeError("계산에 사용된 정적 데이터셋이 업데이트되었습니다. 계산을 다시 실행하세요.")
        role_names = {
            int(character["character_id"]): str(character.get("name_zh") or character["character_id"])
            for character in static_dao.list_characters()
        }
        bridge = SavedStateLoadoutBridge(user_dao, static_dao)
        prepared_plans: list[dict[str, Any]] = []
        for option in result.unified.selected:
            role_name = role_names.get(option.character_id)
            if role_name is None:
                raise RuntimeError(f"정적 데이터셋에서 캐릭터 {option.character_id}을(를) 찾을 수 없습니다.")
            comparisons = preview.loadout_comparisons.get(int(option.character_id), ())
            if slot_ids_by_character is not None:
                slot_id = slot_ids_by_character.get(int(option.character_id))
                if slot_id is None:
                    raise RuntimeError(f"캐릭터 [{role_name}]에 명확한 장비 세팅 슬롯 저장 대상이 없습니다.")
            else:
                primary = next((row for row in comparisons if row.slot_key == "primary"), None)
                slot_id = primary.slot_id if primary is not None else None
            diff = select_frozen_comparison(comparisons, slot_id)
            prepared = bridge.prepare_role_plan(
                role_name=role_name,
                role_state=role_state(option),
                character_id=option.character_id,
                snapshot_id=result.snapshot_id,
                name=f"스탯 세팅: {role_name}",
                score=option.score,
                payload={
                    "schema": "allocation-official-snapshot-v1",
                    "source": "weighted_allocation",
                    "source_role_name": role_name,
                    "allocation_strategy": result.unified.strategy,
                    "profile_id": result.profile_id,
                    "profile_version": result.profile_version,
                    "solver_version": result.solver_version,
                    "last_diff": diff,
                    "assignment_scores": {
                        f"nte-{assignment.kind}-{assignment.uid[0]}-{assignment.uid[1]}": assignment.score
                        for assignment in option.assignments
                    },
                    "tape_main_values": weighted_option_tape_main_values(preview.context, option),
                    "static_dataset": {
                        "schema_version": preview.static_dataset.schema_version,
                        "dataset_id": preview.static_dataset.dataset_id,
                        "importer_version": preview.static_dataset.importer_version,
                        "built_at_utc": preview.static_dataset.built_at_utc,
                    },
                },
            )
            record = prepared.as_record()
            record["comparison_baseline"] = diff
            if slot_ids_by_character is not None:
                record["slot_id"] = int(slot_id)
            prepared_plans.append(record)
        if slot_ids_by_character is not None:
            return user_dao.save_plans_to_slots(prepared_plans)
        return user_dao.replace_active_loadout_plans(prepared_plans)



def role_state(option: RoleAllocationOption) -> dict[str, object]:
    """Project a Context result into the existing SQLite plan bridge input."""

    drives = [
        {
            EQUIP_UID: f"nte-module-{assignment.uid[0]}-{assignment.uid[1]}",
            EQUIP_SHAPE_ID: str(legacy_shape_id(assignment.geometry or "")),
            "geometry": assignment.geometry,
            "grid_count": assignment.grid_count,
            "virtual": assignment.virtual,
            "virtual_equipment": (
                {
                    "item_id": assignment.item_id,
                    "kind": "module",
                    "suit_id": assignment.suit_id,
                    "geometry": assignment.geometry,
                    "grid_count": assignment.grid_count,
                    "quality": "orange",
                }
                if assignment.virtual
                else None
            ),
        }
        for assignment in option.assignments
        if assignment.kind == "module"
    ]
    core = next((assignment for assignment in option.assignments if assignment.kind == "core"), None)
    return {
        ROLE_BLUEPRINT_LAYOUT: [list(row) for row in option.generated_board],
        ROLE_EQUIPPED_DRIVES: drives,
        ROLE_EQUIPPED_TAPE: (
            {
                EQUIP_UID: f"nte-core-{core.uid[0]}-{core.uid[1]}",
                "virtual": core.virtual,
                "virtual_equipment": (
                    {
                        "item_id": core.item_id,
                        "kind": "core",
                        "suit_id": core.suit_id,
                        "geometry": None,
                        "grid_count": None,
                        "quality": "orange",
                    }
                    if core.virtual
                    else None
                ),
            }
            if core is not None
            else None
        ),
    }
