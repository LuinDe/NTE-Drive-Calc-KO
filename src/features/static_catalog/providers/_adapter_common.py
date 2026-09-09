# 提供领域资料适配器共用的发行校验与记录编码。
"""Shared, Qt-free mapping helpers for static-catalog provider adapters."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, unquote

from src.features.static_catalog.contracts import (
    CatalogField,
    CatalogValueSource,
    StaticCatalogRelease,
)


_SOURCE_BY_ORIGIN = {
    "official_static": CatalogValueSource.OFFICIAL_STATIC,
    "formal_static": CatalogValueSource.OFFICIAL_STATIC,
    "formula_profile": CatalogValueSource.DERIVED_DISPLAY,
    "project_annotation": CatalogValueSource.PROJECT_ANNOTATION,
    "derived_display": CatalogValueSource.DERIVED_DISPLAY,
    "source_metadata": CatalogValueSource.OFFICIAL_STATIC,
    "unavailable": CatalogValueSource.DERIVED_DISPLAY,
}


def validate_release_path(
    release: StaticCatalogRelease,
    database_path: str | Path,
) -> None:
    """Reject requests whose frozen release is not this provider's database."""

    expected = Path(database_path).expanduser().resolve()
    actual = release.database_path.expanduser().resolve()
    if actual != expected:
        raise RuntimeError("자료실 요청의 배포 정적 데이터베이스가 변경되었습니다")
    if not release.read_only:
        raise RuntimeError("게임 자료실은 읽기 전용 배포 스냅샷만 받습니다")


def validate_release_identity(
    release: StaticCatalogRelease,
    *,
    dataset_id: str,
    schema_version: int,
    importer_version: int,
    built_at_utc: str | None = None,
) -> None:
    """Compare service evidence with the identity frozen by the controller."""

    if release.dataset_id != dataset_id:
        raise RuntimeError("자료실 요청 중 정적 dataset이 변경되었습니다")
    if release.schema_version != schema_version:
        raise RuntimeError("자료실 요청 중 정적 schema가 변경되었습니다")
    if release.importer_version != importer_version:
        raise RuntimeError("자료실 요청 중 importer 버전이 변경되었습니다")
    if built_at_utc is not None and release.built_at_utc != built_at_utc:
        raise RuntimeError("자료실 요청 중 정적 빌드 식별자가 변경되었습니다")


def source_for(origin: str) -> CatalogValueSource:
    """Map domain provenance without upgrading unavailable data to official."""

    return _SOURCE_BY_ORIGIN.get(
        str(origin),
        CatalogValueSource.PROJECT_ANNOTATION,
    )


def field(
    label: object,
    value: object,
    *,
    source: CatalogValueSource,
    copyable: bool = False,
) -> CatalogField:
    return CatalogField(
        label=str(label),
        value="不可用" if value is None or value == "" else str(value),
        source=source,
        copyable=copyable,
    )


def encode_typed_record_id(entity_kind: str, entity_key: object) -> str:
    return f"{entity_kind}|{quote(str(entity_key), safe='')}"


def decode_typed_record_id(record_id: str) -> tuple[str, str]:
    kind, separator, encoded_key = str(record_id).partition("|")
    if not separator or not kind or not encoded_key:
        raise ValueError("자료실 기록 키 형식이 잘못되었습니다")
    return kind, unquote(encoded_key)
