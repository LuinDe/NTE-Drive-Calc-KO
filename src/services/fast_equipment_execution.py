# 保留极速装配步骤游标，恢复同步后仅续接尚未完成的当前指令。
from __future__ import annotations

from contextlib import nullcontext

from src.services.equipment_apply_service import EquipmentApplyResult, FAST_EQUIPMENT_COMMAND_SETTLE_SECONDS
from src.services.equipment_apply_verification import module_plan_mismatch, plan_mismatch
from src.utils.logger import logger


def execute_fast_plan(
    service,
    *,
    plan,
    snapshot_id,
    character_id,
    character_uid,
    placements,
    modules,
    core_assignment,
    current_items,
    timeout,
    reset_before_apply,
):
    runtime = service.recovery
    if runtime is not None:
        runtime.begin_role(int(plan["plan_id"]), character_id, character_uid)

    def current_rows():
        return runtime.observed_items if runtime is not None and runtime.observed_items is not None else current_items

    def one_key_confirmed(rows):
        return (
            plan_mismatch(
                items=rows,
                modules=modules,
                core_assignment=core_assignment,
                character_id=character_id,
                character_uid=character_uid,
            )
            is None
        )

    def reset_confirmed(rows):
        return all(
            not row.get("equipped")
            or (isinstance(row.get("equipped_character_uid"), dict) and row["equipped_character_uid"] != character_uid)
            for row in rows
        )

    # A task recovery owns the outer batch and must be able to release it.
    scope = (
        (nullcontext() if runtime.batch_active else runtime.batch())
        if runtime is not None
        else service.sync_service.equipment_batch()
    )
    reset_rows = None
    with scope:
        if reset_before_apply:
            logger.info("캐릭터 {}을(를) 전체 해제 모드로 다시 장착합니다", character_id)
            service._dispatch_with_busy_retry(
                lambda: service.sync_service.unequip_all(character=character_uid),
                operation="캐릭터의 기존 장비 해제",
                step="reset",
                settle_seconds=0.7,
                confirmed=reset_confirmed,
            )
            reset_rows = current_rows()
        if core_assignment is not None:
            result = service._dispatch_with_busy_retry(
                lambda: service.sync_service.equip_one_key(
                    character=character_uid,
                    placements=placements,
                    core={"slot": core_assignment["uid_slot"], "serial": core_assignment["uid_serial"]},
                    timeout=timeout,
                ),
                operation="원클릭 장착",
                step="one_key",
                settle_seconds=FAST_EQUIPMENT_COMMAND_SETTLE_SECONDS,
                confirmed=one_key_confirmed,
            )
        else:
            result = []
            for index, (placement, assignment) in enumerate(zip(placements, modules), 1):

                def dispatch_module(placement=placement, assignment=assignment):
                    pair = (assignment["uid_slot"], assignment["uid_serial"])
                    source = next(row for row in current_rows() if (row["uid_slot"], row["uid_serial"]) == pair)
                    was_reset_target = (
                        reset_before_apply
                        and source.get("equipped_character_uid") == character_uid
                        and current_rows() is reset_rows
                    )
                    move = bool(source.get("equipped") and not was_reset_target)
                    dispatcher = (
                        service.sync_service.move_module_to_character if move else service.sync_service.equip_module
                    )
                    return dispatcher(
                        character=character_uid,
                        equipment=placement["equipment"],
                        row=placement["row"],
                        column=placement["column"],
                    )

                response = service._dispatch_with_busy_retry(
                    dispatch_module,
                    operation="드라이브 장착",
                    step="module",
                    module_index=index,
                    settle_seconds=FAST_EQUIPMENT_COMMAND_SETTLE_SECONDS,
                    confirmed=lambda rows, assignment=assignment: (
                        module_plan_mismatch(
                            items=rows, modules=[assignment], character_id=character_id, character_uid=character_uid
                        )
                        is None
                    ),
                )
                result.append(response)
                logger.info("캐릭터 {} 드라이브 {}/{} 순차 전송 완료", character_id, index, len(modules))
    return EquipmentApplyResult(
        plan_id=plan["plan_id"],
        before_snapshot_id=snapshot_id,
        after_snapshot_id=snapshot_id,
        character_uid=character_uid,
        rpc_result=result,
        verified=False,
    )
