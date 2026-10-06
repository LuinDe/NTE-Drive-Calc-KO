# 等待完整库存的新观测，允许同内容快照复用身份而不伪造新快照。
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from time import monotonic


@dataclass(frozen=True)
class InventoryObservation:
    last_snapshot_id: int
    cursor: int
    items: tuple[dict, ...] = ()
    characters: tuple[dict, ...] = ()


def record_inventory_observation(service, payload):
    # Payload has already passed the complete-snapshot stabilizer and the
    # native revision check. Never read the accepted-command UI projection.
    if payload.get("complete") is not True:
        return
    rows = tuple(
        {**deepcopy(item), "uid_slot": item["uid"]["slot"], "uid_serial": item["uid"]["serial"]}
        for item in payload["items"]
    )
    with service._state_condition:
        snapshot_id = service._state.last_snapshot_id
        if snapshot_id is not None:
            service._inventory_observation_cursor += 1
            service._inventory_observation = InventoryObservation(
                snapshot_id,
                service._inventory_observation_cursor,
                rows,
                tuple(deepcopy(payload.get("characters", ()))),
            )
            service._state_condition.notify_all()


def wait_for_inventory_observation(service, *, after_cursor: int, timeout: float):
    deadline = monotonic() + timeout
    with service._state_condition:
        while True:
            state = service._state
            observation = service._inventory_observation
            if observation is not None and observation.cursor > after_cursor:
                return deepcopy(observation)
            if state.stop_reason == "connection_lost" or (state.phase == "error" and not state.running):
                raise RuntimeError("네이티브 인벤토리 연결이 중지되어 자동 복구를 종료했습니다")
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("새 전체 인벤토리 관측 대기 시간이 초과되었습니다")
            service._state_condition.wait(remaining)


def wait_for_snapshot(service, *, after_snapshot_id: int | None, timeout: float):
    deadline = monotonic() + timeout
    with service._state_condition:
        while True:
            state = service._state
            snapshot_id = state.last_snapshot_id
            if snapshot_id is not None and (after_snapshot_id is None or snapshot_id > after_snapshot_id):
                return state
            if state.phase == "error" and not state.running:
                raise RuntimeError(state.error or state.message)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("새 안정 가방 스냅샷 대기 시간 초과")
            service._state_condition.wait(remaining)
