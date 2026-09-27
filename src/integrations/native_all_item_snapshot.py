# 读取独立 all_items 游标，逐页验证范围并在结束时复核源修订。
from src.domain.all_item_snapshot import validate_all_item_snapshot
from src.integrations.native_inventory_snapshot import NativeSnapshotPending
from src.integrations.native_raw_snapshot import IDENTITY_FIELDS, read_native_raw_domain
from src.integrations.nte_core_protocol import NteCoreProtocolError


def read_all_item_snapshot(call, check):
    def scoped(method, params):
        check()
        return call(method, {**params, "scope": "all_items"})

    header = scoped("native.snapshot.refresh", {"domain": "inventory"})
    try:
        validate_all_item_snapshot(header, with_records=False)
    except ValueError as error:
        raise NativeSnapshotPending("전체 아이템 집합이 아직 완성되지 않아 이전 아카이브를 유지합니다.") from error
    snapshot = read_native_raw_domain(
        scoped, check, "inventory", header=header,
        identity_fields=(*IDENTITY_FIELDS, "sourceRecordCount", "collectionScope", "collectionComplete"),
    )
    check()
    status = call("native.snapshot.status", {})
    if not isinstance(status, dict) or not isinstance(status.get("domains"), list):
        raise NteCoreProtocolError("전체 아이템 스냅샷 재확인 상태가 유효하지 않습니다.")
    rows = [row for row in status["domains"] if isinstance(row, dict) and row.get("domain") == "inventory"]
    if len(rows) != 1:
        raise NteCoreProtocolError("전체 아이템 스냅샷 재확인에 유일한 데이터 도메인이 없습니다.")
    current = rows[0]
    if (status.get("providerId") != snapshot["providerId"]
            or current.get("ready") is not True or current.get("dirty") is not False
            or any(current.get(key) != snapshot[key] for key in ("domainKey", "revision"))):
        raise NativeSnapshotPending("아이템을 읽는 동안 출처가 변경되어 재수집을 기다립니다.")
    check()
    return snapshot
