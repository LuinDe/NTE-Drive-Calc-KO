# 将公式证据和反事实支持矩阵映射到公共资料库。
"""Adapt formula evidence and counterfactual support to the public catalog."""

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
from src.features.static_catalog.providers._adapter_common import (
    validate_release_identity,
    validate_release_path,
)
from src.services.static_catalog_formula_service import (
    CounterfactualSupportEntry,
    FormulaEntry,
    StaticCatalogFormulaDomain,
    StaticCatalogFormulaService,
)


_NATIVE_CORE_NOTE = (
    "독립 C++ sidecar는 Python 골드 스탠더드와의 차분 검증에만 사용되며 프로덕션 실행 진입점이 아닙니다."
    "이 페이지의 프로덕션 기능 상태는 여전히 현재 소비자 근거에 따라 보고되며, 이를 근거로 실행기를 선택하거나 활성화할 수 없습니다."
)


class _FormulaProviderBase:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path).expanduser().resolve()
        self._service = StaticCatalogFormulaService(self._database_path)
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def _load(self, release: StaticCatalogRelease) -> StaticCatalogFormulaDomain:
        if self._closed:
            raise RuntimeError("공식 자료 어댑터가 닫혔습니다")
        validate_release_path(release, self._database_path)
        domain = self._service.load()
        snapshot = domain.evidence_snapshot
        validate_release_identity(
            release,
            dataset_id=snapshot.dataset_id,
            schema_version=snapshot.schema_version,
            importer_version=snapshot.importer_version,
        )
        return domain

    @staticmethod
    def _page(
        items: tuple[CatalogItem, ...],
        *,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(int(limit), 200))
        return CatalogPage(
            items=items[safe_offset : safe_offset + safe_limit],
            total=len(items),
            offset=safe_offset,
            limit=safe_limit,
        )


class StaticCatalogFormulaProvider(_FormulaProviderBase):
    domain = CatalogDomain(
        key="formulas",
        label="피해 공식",
        description="프로젝트 공식, 정식 정적 입력 경계, 변수, 제한과 감사 근거",
        order=90,
    )

    def search(
        self,
        release: StaticCatalogRelease,
        *,
        query: str,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        formulas = self._load(release).formulas
        needle = query.strip().casefold()
        matched = tuple(
            formula
            for formula in formulas
            if not needle
            or needle
            in " ".join(
                (formula.key, formula.section, formula.title, formula.expression)
            ).casefold()
        )
        return self._page(
            tuple(self._item(formula) for formula in matched),
            offset=offset,
            limit=limit,
        )

    def detail(
        self,
        release: StaticCatalogRelease,
        record_id: str,
    ) -> CatalogDetail | None:
        formula = next(
            (
                entry
                for entry in self._load(release).formulas
                if entry.key == record_id
            ),
            None,
        )
        if formula is None:
            return None
        sections = [
            CatalogSection(
                "公式",
                (
                    CatalogField(
                        "표현식",
                        formula.expression,
                        CatalogValueSource.PROJECT_ANNOTATION,
                        True,
                    ),
                    CatalogField(
                        "데이터 경계",
                        formula.boundary,
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                    CatalogField(
                        "적용 조건",
                        "；".join(formula.applicable_when) or "不可用",
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                    CatalogField(
                        "제한",
                        "；".join(formula.limitations) or "없음",
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                ),
            ),
            CatalogSection(
                "변수",
                tuple(
                    CatalogField(
                        variable.symbol,
                        variable.meaning,
                        CatalogValueSource.PROJECT_ANNOTATION,
                        True,
                    )
                    for variable in formula.variables
                ),
            ),
            self._evidence_section(formula),
        ]
        return CatalogDetail(
            item=self._item(formula),
            sections=tuple(sections),
            notes=("공식은 프로젝트 규칙 또는 감사 투영입니다. 정식 SQLite 필드는 명시적으로 표기된 입력으로만 사용됩니다.",),
        )

    @classmethod
    def _item(cls, formula: FormulaEntry) -> CatalogItem:
        return CatalogItem(
            domain_key=cls.domain.key,
            record_id=formula.key,
            title=formula.title,
            subtitle=f"{formula.section} · {formula.expression}",
            source=CatalogValueSource.PROJECT_ANNOTATION,
        )

    @staticmethod
    def _evidence_section(formula: FormulaEntry) -> CatalogSection:
        return CatalogSection(
            "감사 근거",
            tuple(
                CatalogField(
                    f"{evidence.kind} · {evidence.symbol}",
                    f"{evidence.path} · {evidence.note}",
                    CatalogValueSource.PROJECT_ANNOTATION,
                    True,
                )
                for evidence in formula.evidence
            ),
        )


class StaticCatalogCounterfactualProvider(_FormulaProviderBase):
    domain = CatalogDomain(
        key="counterfactual_models",
        label="반사실 모델",
        description="캐릭터 패시브, 각성, 아크, 콘솔 등 메커니즘의 근거 커버리지와 갭",
        order=100,
    )

    def search(
        self,
        release: StaticCatalogRelease,
        *,
        query: str,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        entries = self._load(release).counterfactual_support
        needle = query.strip().casefold()
        matched = tuple(
            entry
            for entry in entries
            if not needle
            or needle
            in " ".join(
                (
                    entry.key,
                    entry.category,
                    entry.mechanism,
                    entry.scope,
                    entry.status,
                )
            ).casefold()
        )
        return self._page(
            tuple(self._item(entry) for entry in matched),
            offset=offset,
            limit=limit,
        )

    def detail(
        self,
        release: StaticCatalogRelease,
        record_id: str,
    ) -> CatalogDetail | None:
        entry = next(
            (
                row
                for row in self._load(release).counterfactual_support
                if row.key == record_id
            ),
            None,
        )
        if entry is None:
            return None
        notes = list(entry.limitations)
        if entry.key == "native_counterfactual_core":
            notes.insert(0, _NATIVE_CORE_NOTE)
        sections = (
            CatalogSection(
                "지원 상태",
                (
                    CatalogField(
                        "프로덕션 기능 상태",
                        entry.status,
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                    CatalogField(
                        "범위",
                        entry.scope,
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                    CatalogField(
                        "모델링 방안",
                        entry.modeling_scheme,
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                    CatalogField(
                        "커버리지 dataset",
                        entry.covered_dataset,
                        CatalogValueSource.OFFICIAL_STATIC,
                        True,
                    ),
                    CatalogField(
                        "커버리지 대상",
                        "；".join(entry.covered_entities) or "없음",
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                    CatalogField(
                        "갭 코드",
                        "；".join(entry.gap_codes) or "없음",
                        CatalogValueSource.PROJECT_ANNOTATION,
                        bool(entry.gap_codes),
                    ),
                    CatalogField(
                        "프로덕션 소비자",
                        "；".join(entry.consumer_entries) or "프로덕션 진입점 없음",
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                ),
            ),
            CatalogSection(
                "감사 근거",
                tuple(
                    CatalogField(
                        f"{evidence.kind} · {evidence.symbol}",
                        f"{evidence.path} · {evidence.note}",
                        CatalogValueSource.PROJECT_ANNOTATION,
                        True,
                    )
                    for evidence in entry.evidence
                ),
            ),
        )
        return CatalogDetail(
            item=self._item(entry),
            sections=sections,
            notes=tuple(notes),
        )

    @classmethod
    def _item(cls, entry: CounterfactualSupportEntry) -> CatalogItem:
        subtitle = f"{entry.category} · 프로덕션 상태 {entry.status}"
        if entry.key == "native_counterfactual_core":
            subtitle += " · C++ 독립 차분 검증, 프로덕션 진입점 아님"
        return CatalogItem(
            domain_key=cls.domain.key,
            record_id=entry.key,
            title=entry.mechanism,
            subtitle=subtitle,
            source=CatalogValueSource.PROJECT_ANNOTATION,
        )
