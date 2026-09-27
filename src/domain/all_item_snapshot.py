# 校验客户端全部已观测物品集合，保留来源未知且不生成装备投影。
from __future__ import annotations

import json

ALL_ITEMS_CAPABILITY = "inventory.all_items.v1"
ALL_ITEMS_SCOPE = "all_observed_InventoryContainerMap_containers"
MAX_RAW_BYTES = 64 * 1024 * 1024


def validate_all_item_snapshot(snapshot, *, with_records=True):
    if not isinstance(snapshot, dict) or snapshot.get("domain") != "inventory":
        raise ValueError("전체 아이템 스냅샷의 데이터 도메인이 유효하지 않습니다")
    for key in ("providerId", "domainKey", "snapshotId"):
        value = snapshot.get(key)
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise ValueError("전체 아이템 스냅샷에 소스 식별 정보가 없습니다")
    for key in ("generation", "revision", "observedUnixUs", "observedMonotonicMs"):
        value = snapshot.get(key)
        if (not isinstance(value, str) or not value.isascii() or not value.isdecimal()
                or len(value) > 20 or int(value) > 18446744073709551615):
            raise ValueError("전체 아이템 스냅샷의 리비전 또는 시간이 유효하지 않습니다")
    for key, expected in (("ready", True), ("enabled", True), ("dirty", False),
                          ("enumerationComplete", True), ("failed", False),
                          ("truncated", False), ("collectionComplete", True)):
        if snapshot.get(key) is not expected:
            raise ValueError("전체 아이템 스냅샷이 아직 완료되지 않았습니다")
    if snapshot.get("collectionScope") != ALL_ITEMS_SCOPE:
        raise ValueError("전체 아이템 스냅샷의 수집 범위가 일치하지 않습니다")
    count = snapshot.get("recordCount")
    if (type(count) is not int or not 0 <= count <= 32768
            or type(snapshot.get("sourceRecordCount")) is not int
            or snapshot["sourceRecordCount"] != count):
        raise ValueError("전체 아이템 스냅샷의 항목 수가 일치하지 않습니다")
    # Client enumeration never proves server account coverage.
    if (snapshot.get("complete") is not False or snapshot.get("sourceCoverage") != "unknown"
            or snapshot.get("changeCoverage") != "unknown"
            or not isinstance(snapshot.get("missing"), list)
            or any(not isinstance(value, str) for value in snapshot["missing"])):
        raise ValueError("전체 아이템 스냅샷에 소스 커버리지 경계가 없습니다")
    if with_records:
        rows = snapshot.get("records")
        if not isinstance(rows, list) or len(rows) != count or any(not isinstance(row, dict) for row in rows):
            raise ValueError("전체 아이템 스냅샷의 페이징 레코드가 온전하지 않습니다")


def encode_all_item_snapshot(snapshot):
    validate_all_item_snapshot(snapshot)
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_RAW_BYTES:
        raise ValueError("전체 아이템 스냅샷이 저장 상한을 초과했습니다")
    return encoded
