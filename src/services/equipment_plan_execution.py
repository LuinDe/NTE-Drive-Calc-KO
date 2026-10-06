# 执行已校验的装配方案，分离有界快速派发与完整快照诊断路径。
from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from typing import Any

from src.services.equipment_apply_service import (
    EquipmentApplyError,
    EquipmentApplyResult,
    _item_uid,
)
from src.services.equipment_apply_verification import plan_mismatch, module_plan_mismatch
from src.services.fast_equipment_execution import execute_fast_plan
from src.utils.logger import logger


def execute_plan(
    self,
    plan_id: int,
    *,
    character_uid: Mapping[str, Any] | None = None,
    target_character_id: int | None = None,
    timeout: float = 30.0,
    verify_after_dispatch: bool = True,
    exact_loadout: bool = False,
    force_dispatch: bool = False,
    reset_before_apply: bool = False,
    stable_snapshot_id: int | None = None,
) -> EquipmentApplyResult:
    """执行方案。

    ``verify_after_dispatch`` 适合诊断或登录页抓包可用的环境，会等待新
    稳定背包快照并逐项确认。游戏内极速装配则只依赖已有快照做前置校验；
    指令成功下发后立即返回，不能把登录时才会出现的背包快照当作成功条件。
    """

    if timeout <= 0:
        raise ValueError("timeout은 0보다 커야 합니다")
    hello = self.sync_service.core_hello_result or {}
    capabilities = hello.get("capabilities", [])
    if not isinstance(capabilities, list) or "equipment" not in capabilities:
        raise EquipmentApplyError("현재 nte-core는 equipment 기능을 지원하지 않습니다")
    if stable_snapshot_id is None:
        before_snapshot_id = self.require_stable_snapshot()
    else:
        before_snapshot_id = int(stable_snapshot_id)
        if self.user_dao.inventory_snapshot_summary(before_snapshot_id) is None:
            raise EquipmentApplyError("지정한 안정 가방 스냅샷이 없습니다")

    plan = self.validate_plan_for_fast_apply(
        plan_id,
        stable_snapshot_id=before_snapshot_id,
    )
    effective_character_id = int(plan["character_id"] if target_character_id is None else target_character_id)
    assignments = plan["assignments"]
    modules = [item for item in assignments if item["kind"] == "module"]
    cores = [item for item in assignments if item["kind"] == "core"]

    current_items = self.user_dao.list_inventory_items(before_snapshot_id)
    by_uid = {(item["uid_serial"], item["uid_slot"]): item for item in current_items}
    selected_uids: set[tuple[int, int]] = set()
    placements: list[dict[str, Any]] = []
    for index, assignment in enumerate(modules):
        uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
        if uid_pair in selected_uids:
            raise EquipmentApplyError("방안에 중복 장비 UID가 있습니다")
        selected_uids.add(uid_pair)
        item = by_uid.get(uid_pair)
        if item is None or item["kind"] != "module":
            raise EquipmentApplyError(f"방안 드라이브 UID {uid_pair}이(가) 현재 안정 가방에 없습니다")
        if assignment.get("rotation") not in (None, 0):
            raise EquipmentApplyError("nte-core 원클릭 장착은 회전 매개변수를 받지 않습니다")
        row = assignment.get("target_row")
        column = assignment.get("target_column")
        if row not in range(1, 6) or column not in range(1, 6):
            raise EquipmentApplyError(f"{index + 1}번째 드라이브 위치는 1..5 사이여야 합니다")
        placements.append(
            {
                "equipment": _item_uid(item),
                "row": row,
                "column": column,
            }
        )
    core_assignment = cores[0] if cores else None
    core_item = None
    if core_assignment is not None:
        core_pair = (core_assignment["uid_serial"], core_assignment["uid_slot"])
        if core_pair in selected_uids:
            raise EquipmentApplyError("방안에 중복 장비 UID가 있습니다")
        core_item = by_uid.get(core_pair)
        if core_item is None or core_item["kind"] != "core":
            raise EquipmentApplyError(f"방안 코어 UID {core_pair}이(가) 현재 안정 가방에 없습니다")
        if core_assignment.get("rotation") not in (None, 0):
            raise EquipmentApplyError("코어에는 회전 매개변수를 포함할 수 없습니다")

    resolved_character_uid = self.resolve_character_uid(effective_character_id, before_snapshot_id, character_uid)
    current_mismatch = (
        plan_mismatch(
            items=current_items,
            modules=modules,
            core_assignment=core_assignment,
            character_id=effective_character_id,
            character_uid=resolved_character_uid,
        )
        if core_assignment is not None or exact_loadout
        else module_plan_mismatch(
            items=current_items,
            modules=modules,
            character_id=effective_character_id,
            character_uid=resolved_character_uid,
        )
    )
    # A normal read-only apply may skip a plan already present in the
    # frozen snapshot.  Full-reset apply is deliberately different: every
    # requested role must first be cleared, even if that snapshot happens
    # to describe the target layout as already present.
    if current_mismatch is None and not force_dispatch and not reset_before_apply:
        return EquipmentApplyResult(
            plan_id=plan["plan_id"],
            before_snapshot_id=before_snapshot_id,
            after_snapshot_id=before_snapshot_id,
            character_uid=resolved_character_uid,
            rpc_result={"status": "already_applied"},
            already_applied=True,
        )

    if not verify_after_dispatch:
        return execute_fast_plan(
            self,
            plan=plan,
            snapshot_id=before_snapshot_id,
            character_id=effective_character_id,
            character_uid=resolved_character_uid,
            placements=placements,
            modules=modules,
            core_assignment=core_assignment,
            current_items=current_items,
            timeout=timeout,
            reset_before_apply=reset_before_apply,
        )

    with ExitStack() as dispatch_scope:
        self._dispatch_with_busy_retry(
            lambda: dispatch_scope.enter_context(self.sync_service.equipment_batch()),
            operation="장착 배치 준비 대기 중",
        )
        reset_target = reset_before_apply
        if reset_target:
            if current_mismatch is None:
                logger.info("캐릭터 {}을(를) 전체 해제 모드로 다시 장착합니다", effective_character_id)
            else:
                logger.info(
                    "캐릭터 {}의 현재 세팅이 일치하지 않아({}) 장비를 모두 해제한 뒤 다시 장착합니다",
                    effective_character_id,
                    current_mismatch,
                )
            self._dispatch_with_busy_retry(
                lambda: self.sync_service.unequip_all(character=resolved_character_uid),
                operation="캐릭터의 기존 장비 해제",
                settle_seconds=0.7,
            )

        if core_item is not None:
            rpc_result = self._dispatch_with_busy_retry(
                lambda: self.sync_service.equip_one_key(
                    character=resolved_character_uid,
                    placements=placements,
                    core=_item_uid(core_item),
                    timeout=timeout,
                ),
                operation="원클릭 장착",
            )
            dispatch_scope.close()
            after_state = self.sync_service.wait_for_snapshot(
                after_snapshot_id=before_snapshot_id,
                timeout=timeout,
            )
            after_snapshot_id = after_state.last_snapshot_id
        else:
            rpc_result = []
            after_snapshot_id = before_snapshot_id
            for placement, assignment in zip(placements, modules):
                source_item = by_uid[(assignment["uid_serial"], assignment["uid_slot"])]
                source_is_reset_target = (
                    source_item["equipped"] and source_item.get("equipped_character_uid") == resolved_character_uid
                )
                move_existing = bool(source_item["equipped"] and not (reset_target and source_is_reset_target))
                dispatcher = (
                    self.sync_service.move_module_to_character if move_existing else self.sync_service.equip_module
                )
                dispatch_name = "장착된 드라이브 이동" if move_existing else "드라이브 장착"
                rpc_item_result = self._dispatch_with_busy_retry(
                    lambda: dispatcher(
                        character=resolved_character_uid,
                        equipment=placement["equipment"],
                        row=placement["row"],
                        column=placement["column"],
                    ),
                    operation=dispatch_name,
                )
                rpc_result.append(rpc_item_result)
                logger.info(
                    "캐릭터 {} 드라이브 {}/{} 순차 전송 완료, 방식={}",
                    effective_character_id,
                    len(rpc_result),
                    len(modules),
                    dispatch_name,
                )
                # The plugin permits only one active and one queued request.
                # Wait after every module so driver-only plans cannot overfill
                # that queue when they contain several placements.
                dispatch_scope.close()
                after_state = self.sync_service.wait_for_snapshot(
                    after_snapshot_id=after_snapshot_id,
                    timeout=timeout,
                )
                after_snapshot_id = after_state.last_snapshot_id
    if after_snapshot_id is None or after_snapshot_id <= before_snapshot_id:
        raise EquipmentApplyError("코어 구성 요소가 장착 후 새 안정 스냅샷을 반환하지 않았습니다")

    mismatch = (
        plan_mismatch(
            items=self.user_dao.list_inventory_items(after_snapshot_id),
            modules=modules,
            core_assignment=core_assignment,
            character_id=effective_character_id,
            character_uid=resolved_character_uid,
        )
        if core_assignment is not None or exact_loadout
        else module_plan_mismatch(
            items=self.user_dao.list_inventory_items(after_snapshot_id),
            modules=modules,
            character_id=effective_character_id,
            character_uid=resolved_character_uid,
        )
    )
    if mismatch is not None:
        raise EquipmentApplyError(f"새 스냅샷이 목표 세팅을 확인하지 못했습니다: {mismatch}")
    return EquipmentApplyResult(
        plan_id=plan["plan_id"],
        before_snapshot_id=before_snapshot_id,
        after_snapshot_id=after_snapshot_id,
        character_uid=resolved_character_uid,
        rpc_result=rpc_result,
    )
