# 提供角色与弧盘资料适配器共用的投影函数。
"""Shared projection helpers used only by character and fork providers."""

from __future__ import annotations

from pathlib import Path

from src.features.static_catalog.contracts import (
    CatalogField,
    CatalogValueSource,
    StaticCatalogRelease,
)


def ensure_release_path(release: StaticCatalogRelease, expected_path: Path) -> None:
    """Reject requests frozen for another release before touching a provider DAO."""

    if not release.read_only:
        raise RuntimeError("게임 자료실은 읽기 전용 배포 스냅샷만 받습니다")
    if release.database_path.resolve() != expected_path:
        raise RuntimeError("게임 자료실 요청의 배포 데이터베이스가 영역 Provider와 일치하지 않습니다")


def ensure_release_metadata(
    release: StaticCatalogRelease,
    *,
    dataset_id: str,
    schema_version: int,
    importer_version: int,
    built_at_utc: str,
) -> None:
    """Verify DB metadata still matches the release identity frozen by the caller."""

    actual = (dataset_id, schema_version, importer_version, built_at_utc)
    frozen = (
        release.dataset_id,
        release.schema_version,
        release.importer_version,
        release.built_at_utc,
    )
    if actual != frozen:
        raise RuntimeError("자료 요청 중 배포 정적 라이브러리 메타 정보가 변경되었습니다")


def display(value: object) -> str:
    if value is None or value == "":
        return "보존되지 않음"
    if isinstance(value, bool):
        return "예" if value else "아니요"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def official(label: str, value: object, *, copyable: bool = False) -> CatalogField:
    return CatalogField(
        label=label,
        value=display(value),
        source=CatalogValueSource.OFFICIAL_STATIC,
        copyable=copyable,
    )


def derived(label: str, value: object, *, copyable: bool = False) -> CatalogField:
    return CatalogField(
        label=label,
        value=display(value),
        source=CatalogValueSource.DERIVED_DISPLAY,
        copyable=copyable,
    )


def annotation(label: str, value: object, *, copyable: bool = False) -> CatalogField:
    return CatalogField(
        label=label,
        value=display(value),
        source=CatalogValueSource.PROJECT_ANNOTATION,
        copyable=copyable,
    )


def lines(values: list[str] | tuple[str, ...]) -> str:
    return "\n".join(values) if values else "없음"
