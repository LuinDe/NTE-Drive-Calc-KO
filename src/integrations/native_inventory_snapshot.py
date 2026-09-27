# 按固定原生快照身份读取有界分页，不推测库存字段或提升来源完整性。
from __future__ import annotations

from copy import deepcopy
import json
from typing import Callable, Any

from src.integrations.nte_core_protocol import NteCoreProtocolError


_METADATA = (
    "providerId", "domain", "snapshotId", "generation", "domainKey",
    "observedUnixUs", "observedMonotonicMs", "enabled", "ready", "state",
    "recordCount", "sourceRecordCount", "enumerationComplete", "complete",
    "sourceCoverage", "changeCoverage", "missing", "failed", "truncated",
)
_MAX_BYTES = 64 * 1024 * 1024
_MAX_RECORDS = 32768


class NativeSnapshotPending(RuntimeError):
    """A domain cannot yet supply a complete observation; keep the saved inventory."""


def read_native_projection(call: Callable[..., dict[str, Any]], check: Callable[[], None], *, domain: str, header: dict[str, Any] | None = None) -> dict[str, Any]:
    """Consume versioned Core business DTOs; raw game rows never reach Calc storage."""
    if domain not in {"inventory", "character"}:
        raise ValueError("지원하지 않는 동기화 데이터 영역입니다.")
    field = "items" if domain == "inventory" else "profiles"
    check()
    if header is None:
        header = call("native.snapshot.refresh", {"domain": domain})
    check()
    if not isinstance(header, dict) or any(key not in header for key in _METADATA):
        raise NteCoreProtocolError("네이티브 동기화 스냅샷 메타데이터가 불완전합니다.")
    if header["domain"] != domain:
        raise NteCoreProtocolError("네이티브 동기화가 다른 데이터 영역을 반환했습니다.")
    if (header["ready"] is not True or header["enabled"] is not True
            or header["enumerationComplete"] is not True or header["failed"] is not False
            or header["truncated"] is not False):
        raise NativeSnapshotPending("게임이 완전한 동기화 데이터를 제공할 때까지 기다리는 중입니다.")
    count = header["recordCount"]
    if type(count) is not int or not 0 <= count <= _MAX_RECORDS:
        raise NteCoreProtocolError("네이티브 동기화 항목 수가 페이지네이션 프로토콜 범위를 벗어났습니다.")
    for key in ("providerId", "snapshotId", "generation", "domainKey", "observedUnixUs", "observedMonotonicMs"):
        if not isinstance(header[key], str) or not header[key]:
            raise NteCoreProtocolError("네이티브 동기화에 유효한 스냅샷 식별 정보가 없습니다.")
    metadata = {key: deepcopy(header[key]) for key in _METADATA}
    if "revision" in header:
        revision = header["revision"]
        if (not isinstance(revision, str) or not revision.isascii() or not revision.isdecimal()
                or len(revision) > 20 or (len(revision) > 1 and revision.startswith("0"))
                or int(revision) > 18446744073709551615 or header.get("dirty") is not False):
            raise NteCoreProtocolError("네이티브 동기화 변경 리비전 형식이 유효하지 않습니다.")
        metadata.update(revision=revision, dirty=False)
    if domain == "inventory":
        for key in ("collectionComplete", "collectionScope", "characterRefsComplete"):
            if key not in header:
                raise NteCoreProtocolError("네이티브 가방에 이번 회차 집합 또는 캐릭터 연관 증명이 없습니다.")
            metadata[key] = deepcopy(header[key])
        if (header["collectionComplete"] is not True or header["collectionScope"] != "EQUIP"
                or header["characterRefsComplete"] is not True):
            raise NativeSnapshotPending("이번 전체 가방 또는 캐릭터 장비 연결을 아직 다 읽지 못했습니다.")
    projection_missing = set()
    stat_provenance = None
    result_rows, characters, references = [], None, None
    offset = total_bytes = 0
    while True:
        check()
        page = call(f"native.{domain}.page", {"snapshotId": header["snapshotId"], "offset": offset, "limit": 64})
        check()
        if not isinstance(page, dict) or any(type(page.get(key)) is not type(value) or page.get(key) != value for key, value in metadata.items()):
            raise NteCoreProtocolError("네이티브 동기화 페이지네이션의 신원 또는 무결성이 변경되어 이번 결과를 폐기했습니다.")
        total_bytes += len(json.dumps(page, ensure_ascii=False).encode("utf-8"))
        if total_bytes > _MAX_BYTES:
            raise NteCoreProtocolError("네이티브 동기화 페이지네이션 응답이 크기 제한을 초과했습니다.")
        diagnostics = page.get("projectionMissing", [])
        if not isinstance(diagnostics, list) or len(diagnostics) > 64 or any(not isinstance(item, str) or len(item) > 128 for item in diagnostics):
            raise NteCoreProtocolError("네이티브 동기화 필드 진단 형식이 유효하지 않습니다.")
        projection_missing.update(diagnostics)
        if len(projection_missing) > 64:
            raise NteCoreProtocolError("네이티브 동기화 필드 진단 수가 한도를 초과했습니다.")
        rows = page.get(field)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise NteCoreProtocolError("네이티브 동기화 비즈니스 항목 형식이 유효하지 않습니다.")
        if "nextOffset" not in page:
            raise NteCoreProtocolError("네이티브 동기화에 페이지네이션 종료 커서가 없습니다.")
        next_offset = page["nextOffset"]
        end = count if next_offset is None else next_offset
        if type(end) is not int or not offset <= end <= min(offset + 64, count):
            raise NteCoreProtocolError("네이티브 동기화 페이지네이션 커서가 유효하지 않습니다.")
        if next_offset is not None and (end == offset or end >= count):
            raise NteCoreProtocolError("네이티브 동기화 페이지네이션 커서가 올바르게 전진하지 않았습니다.")
        if len(rows) > end - offset or (domain == "inventory" and len(rows) != end - offset):
            raise NteCoreProtocolError("네이티브 동기화 항목이 이 페이지가 선언한 범위를 모두 포함하지 못했습니다.")
        if domain == "inventory":
            provenance = page.get("statProvenance")
            if offset and provenance != stat_provenance:
                raise NteCoreProtocolError("네이티브 가방 페이지네이션의 스탯 출처가 변경되었습니다.")
            stat_provenance = deepcopy(provenance)
            if page.get("projectionComplete") is not True:
                raise NativeSnapshotPending("현재 컴포넌트가 이번 전체 가방의 필드 매핑을 아직 확인하지 못해, 저장된 가방을 유지합니다.")
            page_characters = page.get("characters")
            if not isinstance(page_characters, list):
                raise NteCoreProtocolError("네이티브 가방에 동일 회차 관측의 캐릭터 인스턴스가 없습니다.")
            if characters is not None and page_characters != characters:
                raise NteCoreProtocolError("네이티브 가방 페이지네이션의 캐릭터 인스턴스가 변경되었습니다.")
            characters = deepcopy(page_characters)
            page_references = page.get("referencedItemUids")
            if not isinstance(page_references, list):
                raise NteCoreProtocolError("네이티브 가방에 동일 회차의 캐릭터 장비 참조 증명이 없습니다.")
            if references is not None and references != page_references:
                raise NteCoreProtocolError("네이티브 가방 페이지네이션의 캐릭터 장비 참조가 변경되었습니다.")
            references = deepcopy(page_references)
        result_rows.extend(deepcopy(rows))
        offset = end
        if next_offset is None:
            break
    check()
    if domain == "inventory":
        if not result_rows and not characters:
            raise NativeSnapshotPending("가방 또는 캐릭터가 아직 관측되지 않아 로그인 데이터 준비를 기다리는 중입니다; 저장된 가방은 유지합니다.")
        item_uids = [_formal_uid(item.get("uid")) for item in result_rows]
        referenced_uids = [_formal_uid(uid) for uid in references]
        if len(item_uids) != len(set(item_uids)):
            raise NteCoreProtocolError("네이티브 가방에 중복된 장비 인스턴스가 있습니다.")
        if referenced_uids != sorted(set(referenced_uids)) or not set(referenced_uids).issubset(item_uids):
            raise NteCoreProtocolError("캐릭터 장비 참조가 이번 전체 가방에 포함되지 않았습니다.")
    else:
        character_ids = [profile.get("character_id") for profile in result_rows]
        if any(type(value) is not int or value <= 0 for value in character_ids):
            raise NteCoreProtocolError("네이티브 캐릭터 상태에 정식 캐릭터 식별 정보가 없습니다.")
        if len(character_ids) != len(set(character_ids)):
            raise NteCoreProtocolError("네이티브 캐릭터 상태 페이지네이션에 중복 캐릭터가 있어 이번 업데이트를 폐기했습니다.")
    return {**metadata, field: result_rows, "projectionMissing": sorted(projection_missing),
            **({"characters": characters, "projectionComplete": True, "statProvenance": stat_provenance,
                                              "referencedItemUids": references} if domain == "inventory" else {})}


def _formal_uid(value):
    if not isinstance(value, dict):
        raise NteCoreProtocolError("네이티브 장비 인스턴스 식별 정보 형식이 유효하지 않습니다.")
    parts = value.get("slot"), value.get("serial")
    if any(type(part) is not int or not 0 < part < 4294967295 for part in parts):
        raise NteCoreProtocolError("네이티브 장비 인스턴스 식별 정보가 정식 인터페이스 범위에 없습니다.")
    return parts
