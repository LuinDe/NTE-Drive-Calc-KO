# 根据已保存锚点与正式形状校验倒带驱动输入。
from dataclasses import replace

from src.domain.loadout_plan_scores import assignment_score_key
from src.domain.rewind_loadout import (
    RewindSlotReference, RewindSlotSummary, RewindSavedDrive, finite_score, positive_id, supported_saved_plan, shape_key,
)
from src.services.virtual_equipment_service import normalized_equipment_assignment, is_virtual_equipment_assignment


def inspect_slot(slot, plan, inventory, shapes) -> RewindSlotSummary:
    """Validate saved anchors, not the role's current default blueprint.

    All supported official/custom boards have twenty playable cells in a 5x5
    canvas (blueprint_service.official_board/custom_board). Historical anchors
    with twenty distinct legal cells prove full coverage without looking up a
    potentially changed template. An optional frozen mask also verifies holes.
    """
    result = RewindSlotSummary(
        RewindSlotReference(int(slot["character_id"]), int(slot["slot_id"]), positive_id(plan.get("plan_id"))),
        str(slot.get("slot_name") or slot["slot_id"]), str(slot.get("slot_key") or ""),
        int(slot.get("sort_order") or 0), plan.get("source_snapshot_id"),
        finite_score(plan.get("score")), "empty", "저장된 방안이 아직 없습니다. 먼저 저장하거나 다른 슬롯을 선택하세요.",
    )

    def failure(state, reason):
        return replace(result, state=state, reason=reason)

    if not plan:
        return result
    if result.reference.plan_id is None or positive_id(plan.get("character_id")) != result.reference.character_id:
        return failure("layout_invalid", "방안 식별 정보 또는 캐릭터 소속이 유효하지 않습니다. 다시 계산하고 저장하세요.")
    if not supported_saved_plan(plan):
        return failure("unsupported", "지원되는 저장 방안이 아직 없습니다. 다시 계산하고 저장하세요.")
    assignments = tuple(normalized_equipment_assignment(row) for row in plan.get("assignments") or ())
    modules = tuple(row for row in assignments if row.get("kind") == "module")
    if not modules or any(is_virtual_equipment_assignment(row) for row in modules):
        return failure("incomplete", "드라이브가 모두 장착되지 않았습니다. 빈 칸을 채우거나 다른 슬롯을 선택하세요.")
    if positive_id(result.source_snapshot_id) is None:
        return failure("source_missing", "출처 장비 데이터가 없습니다. 다시 계산하고 저장하세요.")
    shape_map = {shape_key(row["shape_id"]): row for row in shapes}
    occupied, seen, drives = set(), set(), []
    scores = (plan.get("payload") or {}).get("assignment_scores") or {}
    score_missing = False
    for row in modules:
        uid = (positive_id(row.get("uid_slot")), positive_id(row.get("uid_serial")))
        if None in uid or uid in seen:
            return failure("layout_invalid", "드라이브 식별 정보 또는 배치 데이터가 유효하지 않습니다. 다시 계산하고 저장하세요.")
        seen.add(uid)
        item = inventory.get(uid)
        if item is None or item.get("kind") != "module":
            return failure("source_missing", "출처 장비 데이터가 없습니다. 다시 계산하고 저장하세요.")
        shape = shape_map.get(shape_key(item.get("geometry")))
        if shape is None or not shape.get("cells"):
            return failure("layout_invalid", "형태 또는 배치 데이터가 부족합니다. 다시 계산하고 저장하세요.")
        anchor = (positive_id(row.get("target_row")), positive_id(row.get("target_column")))
        # Production saved anchors use orientation-specific formal shape IDs;
        # unrecognised rotations must not be silently rendered as rotation zero.
        if None in anchor or row.get("rotation", 0) not in (None, 0):
            return failure("layout_invalid", "드라이브 위치 또는 회전 데이터가 부족합니다. 다시 계산하고 저장하세요.")
        cells = {(anchor[0] + int(cell["x"]), anchor[1] + int(cell["y"])) for cell in shape["cells"]}
        if (len(cells) != int(shape["cell_count"]) or occupied & cells
                or any(not (1 <= r <= 5 and 1 <= c <= 5) for r, c in cells)):
            return failure("layout_invalid", "드라이브 배치가 겹치거나 범위를 벗어났습니다. 다시 계산하고 저장하세요.")
        occupied.update(cells)
        value = scores.get(assignment_score_key(row))
        if value is None:
            value = row.get("score")
        score = finite_score(value)
        score_missing |= score is None
        area = int(shape["cell_count"])
        if item.get("grid_count") is not None and positive_id(item["grid_count"]) != area:
            return failure("layout_invalid", "드라이브 면적이 정식 형태와 맞지 않습니다. 다시 계산하고 저장하세요.")
        drives.append(RewindSavedDrive(str(shape["shape_id"]), area, score if score is not None else 0))
    if len(occupied) < 20:
        return failure("incomplete", "드라이브가 모두 장착되지 않았습니다. 빈 칸을 채우거나 다른 슬롯을 선택하세요.")
    if len(occupied) != 20:
        return failure("layout_invalid", "드라이브 점유 칸이 20칸 청사진과 맞지 않습니다. 다시 계산하고 저장하세요.")
    mask = (plan.get("payload") or {}).get("drive_board_cells")
    if mask is not None and occupied != {(int(r), int(c)) for r, c in mask}:
        return failure("layout_invalid", "드라이브 점유 칸이 저장된 청사진과 맞지 않습니다. 다시 계산하고 저장하세요.")
    if score_missing:
        return failure("score_missing", "점수 데이터가 부족합니다. 다시 계산하고 저장하세요.")
    return replace(result, state="ready", reason="드라이브 장착 완료", drives=tuple(drives))
