# 按冻结身份读取有界原生分页，供装备、角色与独立物品归档共用。
from copy import deepcopy
import json

from src.integrations.native_inventory_snapshot import NativeSnapshotPending
from src.integrations.nte_core_protocol import NteCoreProtocolError

MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 32768
IDENTITY_FIELDS = ("providerId", "domain", "snapshotId", "generation", "domainKey", "revision",
                   "observedUnixUs", "observedMonotonicMs", "ready", "enabled", "dirty",
                   "recordCount", "enumerationComplete", "complete", "sourceCoverage", "changeCoverage",
                   "failed", "truncated", "missing")



def read_native_raw_domain(call, check, domain, *, header=None, identity_fields=IDENTITY_FIELDS):
    if header is None:
        header = call("native.snapshot.refresh", {"domain": domain})
    if not isinstance(header, dict) or any(k not in header for k in identity_fields):
        raise NteCoreProtocolError("네이티브 스냅샷에 출처 식별 정보 또는 변경 리비전이 없습니다.")
    if header["domain"] != domain:
        raise NteCoreProtocolError("네이티브 스냅샷이 잘못된 데이터 영역을 반환했습니다.")
    if (header["ready"] is not True or header["enabled"] is not True or header["dirty"] is not False
            or header["failed"] is not False or header["truncated"] is not False
            or header["enumerationComplete"] is not True):
        raise NativeSnapshotPending("네이티브 스냅샷이 아직 완료되지 않았습니다.")
    count = header["recordCount"]
    if type(count) is not int or not 0 <= count <= MAX_RECORDS:
        raise NteCoreProtocolError("네이티브 스냅샷 항목 수가 한도를 초과했습니다.")
    records, offset, size = [], 0, 0
    while True:
        check()
        page = call("native.snapshot.page", {"domain": domain, "snapshotId": header["snapshotId"],
                                             "offset": offset, "limit": 64})
        if not isinstance(page, dict) or any(type(page.get(k)) is not type(header[k]) or page.get(k) != header[k]
                                            for k in identity_fields):
            raise NteCoreProtocolError("네이티브 스냅샷 페이지네이션 식별 정보가 변경되었습니다.")
        rows, next_offset = page.get("records"), page.get("nextOffset")
        end = count if next_offset is None else next_offset
        if ("nextOffset" not in page or not isinstance(rows, list)
                or type(end) is not int or not offset <= end <= min(offset + 64, count)
                or any(not isinstance(row, dict) for row in rows) or len(rows) != end - offset
                or (next_offset is not None and (end == offset or end >= count))):
            raise NteCoreProtocolError("네이티브 스냅샷 페이지네이션 범위가 불완전합니다.")
        size += len(json.dumps(page, ensure_ascii=False).encode("utf-8"))
        if size > MAX_BUNDLE_BYTES:
            raise NteCoreProtocolError("네이티브 스냅샷이 크기 제한을 초과했습니다.")
        records.extend(deepcopy(rows))
        offset = end
        if next_offset is None:
            break
    return {**deepcopy(header), "records": records}
