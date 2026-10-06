# 在保存线程中复核冻结输入并保存配装，数据库连接不跨线程传递。
from __future__ import annotations

from src.services.allocation_lock_service import verify_allocation_lock_snapshot
from src.services.saved_state_loadout_bridge import SavedStateLoadoutBridge
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


def save_allocation_plans(*, database_path, static_database_path, static_identity,
                          lock_snapshot, rows, checkpoint, progress):
    def verify_static():
        pinned_path, dataset_id, file_identity = static_identity
        current = static_database_path.stat()
        if static_database_path != pinned_path or (current.st_size, current.st_mtime_ns) != file_identity:
            raise RuntimeError("계산에 사용된 정적 데이터셋이 업데이트되었습니다. 계산을 다시 실행하세요.")
        return dataset_id

    checkpoint()
    dataset_id = verify_static()
    with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
        if static_dao.summary()["dataset"]["dataset_id"] != dataset_id:
            raise RuntimeError("계산에 사용된 정적 데이터셋의 식별 정보가 변경되었습니다. 계산을 다시 실행하세요.")
        verify_allocation_lock_snapshot(user_dao, lock_snapshot)
        bridge = SavedStateLoadoutBridge(
            user_dao, static_dao, frozen_snapshot_id=lock_snapshot.inventory_snapshot_id,
        )
        prepared = []
        for index, row in enumerate(rows, 1):
            checkpoint()
            arguments = {key: value for key, value in row.items() if key != "slot_id"}
            plan = bridge.prepare_role_plan(**arguments)
            slot_id = row["slot_id"]
            if slot_id is None:
                if user_dao.list_loadout_slots(plan.character_id):
                    raise RuntimeError("대상 장비 구성의 슬롯이 변경되었습니다. 다시 선택하세요.")
            else:
                slot = user_dao.get_loadout_slot(slot_id)
                if slot is None or int(slot["character_id"]) != plan.character_id:
                    raise RuntimeError("대상 장비 구성의 슬롯이 변경되었습니다. 다시 선택하세요.")
            prepared.append({
                **plan.as_record(), "slot_id": slot_id,
                "comparison_baseline": (row.get("payload") or {}).get("last_diff"),
                "create_slot_name": row["role_name"] if slot_id is None else None,
            })
            progress((f"장비 구성 방안 {index}/{len(rows)} 검증 완료", index, len(rows) + 4))
        checkpoint()
        verify_static()
        verify_allocation_lock_snapshot(user_dao, lock_snapshot)
        progress(("방안을 기록하고 장비 점유를 확인하는 중…", len(rows), len(rows) + 4))

        def commit_checkpoint():
            checkpoint()
            verify_static()

        user_dao.save_calculated_loadout_plans(
            prepared, checkpoint=commit_checkpoint,
            validate=lambda: verify_allocation_lock_snapshot(user_dao, lock_snapshot),
        )
        progress(("방안이 저장되었습니다. 페이지를 새로 고치는 중…", len(rows) + 1, len(rows) + 4))
    return len(prepared)
