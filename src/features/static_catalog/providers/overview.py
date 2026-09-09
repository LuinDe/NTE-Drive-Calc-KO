# 将全量静态表覆盖登记映射到公共资料库契约。
"""Public provider for the audited 125-table static release registry."""

from __future__ import annotations

from pathlib import Path

from src.features.static_catalog.contracts import (
    CatalogDetail,
    CatalogDomain,
    CatalogField,
    CatalogItem,
    CatalogPage,
    CatalogSection,
    CatalogValueSource,
    StaticCatalogRelease,
)
from src.storage.sqlite.static_catalog_overview_queries import (
    STATIC_TABLE_CATALOG,
    StaticCatalogOverviewQueries,
    StaticTableOverview,
)


_STATE_LABELS = {
    "A": "완전한 정식 카탈로그",
    "B": "공개됨, 고급 근거 포함",
    "C": "표시 가능, 구조화 갭 있음",
    "D": "정식 ID 또는 제한된 근거만 있음",
    "E": "빈 테이블 또는 배포 payload에서 명시적으로 생략됨",
}


class StaticCatalogOverviewProvider:
    """Expose every normalized table, including empty and omitted-payload facts."""

    domain = CatalogDomain(
        key="coverage",
        label="커버리지 개요",
        description="배포 정적 라이브러리 125개 테이블의 테이블별 행 수, 도메인과 표시 가능 상태",
        order=0,
    )

    def __init__(self, database_path: str) -> None:
        self._queries = StaticCatalogOverviewQueries(database_path)
        self._rows = self._queries.list_tables()

    def close(self) -> None:
        self._queries.close()

    @staticmethod
    def _ensure_release(release: StaticCatalogRelease, database_path: str) -> None:
        if release.database_path != Path(database_path).resolve():
            raise RuntimeError("커버리지 개요가 현재 고정된 배포 경로와 일치하지 않습니다")

    def search(
        self,
        release: StaticCatalogRelease,
        *,
        query: str,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        self._ensure_release(release, str(self._queries.database_path))
        needle = query.casefold()
        matched = tuple(
            row
            for row in self._rows
            if not needle
            or needle in row.name.casefold()
            or needle in row.domain.casefold()
            or needle in _STATE_LABELS[row.coverage_state].casefold()
        )
        return CatalogPage(
            items=tuple(self._item(row) for row in matched[offset : offset + limit]),
            total=len(matched),
            offset=offset,
            limit=limit,
        )

    def detail(
        self, release: StaticCatalogRelease, record_id: str
    ) -> CatalogDetail | None:
        self._ensure_release(release, str(self._queries.database_path))
        row = next((item for item in self._rows if item.name == record_id), None)
        if row is None:
            return None
        notes = []
        if row.name == "source_row" and release.source_payloads_omitted:
            notes.append("배포 매니페스트에서 source_row.payload_json을 명시적으로 생략했습니다. 출처 키와 콘텐츠 해시는 여전히 추적할 수 있습니다.")
        if row.rows == 0:
            notes.append("이 테이블은 정식 schema에 속하지만 이번 배포에는 레코드가 없습니다. 알 수 없는 업무 값을 0으로 위장하지 않습니다.")
        return CatalogDetail(
            item=self._item(row),
            sections=(
                CatalogSection(
                    title="테이블별 커버리지 감사",
                    fields=(
                        self._field("정식 테이블 이름", row.name, copyable=True),
                        self._field("데이터 도메인", row.domain),
                        self._field("레코드 수", f"{row.rows:,}"),
                        self._field("커버리지 상태", f"{row.coverage_state} · {_STATE_LABELS[row.coverage_state]}"),
                        self._field("Dataset", release.dataset_id, copyable=True),
                        self._field("Schema", f"v{release.schema_version}"),
                        self._field("Importer", f"v{release.importer_version}"),
                        self._field("읽기 전용", "예" if release.read_only else "아니요"),
                    ),
                ),
            ),
            notes=tuple(notes),
        )

    @staticmethod
    def _item(row: StaticTableOverview) -> CatalogItem:
        return CatalogItem(
            domain_key="coverage",
            record_id=row.name,
            title=row.name,
            subtitle=f"{row.domain} · {row.rows:,} 행 · 상태 {row.coverage_state}",
        )

    @staticmethod
    def _field(label: str, value: str, *, copyable: bool = False) -> CatalogField:
        return CatalogField(label, value, CatalogValueSource.OFFICIAL_STATIC, copyable)


def registered_static_table_count() -> int:
    """Executable gate used by tests and composition audits."""

    return len(STATIC_TABLE_CATALOG)
