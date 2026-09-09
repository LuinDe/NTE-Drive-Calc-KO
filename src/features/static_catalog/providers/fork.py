# 将弧盘资料域映射到公共只读提供器契约。
"""Adapt the fork catalog domain to the common read-only provider contract."""

from __future__ import annotations

from pathlib import Path

from src.features.static_catalog.contracts import (
    CatalogDetail,
    CatalogDomain,
    CatalogItem,
    CatalogPage,
    CatalogReference,
    CatalogSection,
    CatalogValueSource,
    StaticCatalogRelease,
)
from src.features.static_catalog.providers._shared import (
    annotation,
    derived,
    ensure_release_metadata,
    ensure_release_path,
    lines,
    official,
)
from src.services.static_catalog_fork_service import (
    CatalogOrigin,
    ForkCatalogDetail,
    ForkCatalogSummary,
    StaticCatalogForkService,
)


FORK_DOMAIN = CatalogDomain(
    key="fork",
    label="아크 데이터",
    description="레벨업 경험치와 패널, 돌파 소모, 믹싱 스킬, Buff 및 캐릭터 관계",
    order=20,
)


class ForkCatalogProvider:
    """Own one read-only fork service and adapt it without Qt or SQL."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path).resolve()
        self._service = StaticCatalogForkService.from_database(self._database_path)
        self._closed = False

    @property
    def domain(self) -> CatalogDomain:
        return FORK_DOMAIN

    def close(self) -> None:
        if not self._closed:
            self._service.close()
            self._closed = True

    def search(
        self,
        release: StaticCatalogRelease,
        *,
        query: str,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        ensure_release_path(release, self._database_path)
        metadata = self._service.metadata()
        self._ensure_metadata(release, metadata)
        page = self._service.list_forks(query=query, page=1, page_size=200)
        end = offset + limit
        return CatalogPage(
            items=tuple(self._item(item) for item in page.items[offset:end]),
            total=page.total_items,
            offset=offset,
            limit=limit,
        )

    def detail(
        self, release: StaticCatalogRelease, record_id: str,
    ) -> CatalogDetail | None:
        ensure_release_path(release, self._database_path)
        metadata = self._service.metadata()
        self._ensure_metadata(release, metadata)
        detail = self._service.get_fork(str(record_id))
        if detail is None:
            return None
        sections = [
            self._identity(detail),
            self._capabilities(detail, metadata),
            self._growth(detail),
            self._breakthroughs(detail),
            self._refinements(detail),
        ]
        if detail.buff_definitions:
            sections.append(self._buffs(detail))
        if detail.resources or detail.relations:
            sections.append(self._relations(detail))
        return CatalogDetail(
            item=self._item(detail.summary),
            sections=tuple(sections),
            notes=tuple((*detail.audit_notes, *metadata.audit_notes)),
        )

    @staticmethod
    def _ensure_metadata(release: StaticCatalogRelease, metadata: object) -> None:
        ensure_release_metadata(
            release,
            dataset_id=str(getattr(metadata, "dataset_id")),
            schema_version=int(getattr(metadata, "schema_version")),
            importer_version=int(getattr(metadata, "importer_version")),
            built_at_utc=str(getattr(metadata, "built_at_utc")),
        )

    @staticmethod
    def _item(summary: ForkCatalogSummary) -> CatalogItem:
        subtitle = (
            f"{summary.quality} · {summary.fork_type_name_zh or summary.raw_group_type or '未分类'}"
            f" · 돌파 {summary.max_breakthrough if summary.max_breakthrough is not None else '未知'}"
            f" · 믹싱 {summary.max_refinement if summary.max_refinement is not None else '未知'}"
        )
        return CatalogItem(
            domain_key=FORK_DOMAIN.key,
            record_id=summary.fork_id,
            title=summary.name_zh,
            subtitle=subtitle,
        )

    @staticmethod
    def _identity(detail: ForkCatalogDetail) -> CatalogSection:
        item = detail.summary
        return CatalogSection(
            title="아크 식별 정보와 리소스",
            fields=(
                official("fork_id", item.fork_id, copyable=True),
                official("중국어 이름", item.name_zh),
                official("품질", item.quality),
                official("유형", f"{item.fork_type_name_zh or '未保留'} ({item.fork_type_id})"),
                official("설명", item.description_zh),
                official("업그레이드 팩", detail.upgrade_pack_id, copyable=True),
                official("돌파 팩", detail.breakthrough_pack_id, copyable=True),
                official("믹싱 데이터 팩", detail.star_pack_id, copyable=True),
                official("이름 텍스트 키", f"{detail.name_text_table or '未保留'}:{detail.name_text_key or '未保留'}"),
            ),
        )

    @staticmethod
    def _capabilities(detail: ForkCatalogDetail, metadata: object) -> CatalogSection:
        return CatalogSection(
            title="데이터 가용성",
            fields=(
                derived("레벨 성장 행", len(detail.growth_levels)),
                derived("돌파 단계", len(detail.breakthroughs)),
                derived("믹싱 레벨", len(detail.refinement_levels)),
                derived("Buff 정의", len(detail.buff_definitions)),
                derived(
                    "독립 아크 스킬 테이블",
                    "available" if bool(getattr(metadata, "has_fork_skill_tables"))
                    else "unavailable: schema v30에는 fork_skill / fork_skill_level이 없습니다. 믹싱 1–5레벨 설명, 매개변수, Buff로 스킬을 표시합니다",
                ),
                derived(
                    "원본 source_row payload",
                    "available" if int(getattr(metadata, "source_payloads_preserved")) > 0
                    else "unavailable: 배포 라이브러리는 원본 payload를 생략하고 출처 경로, 행 키, 해시만 보존합니다",
                ),
            ),
        )

    @staticmethod
    def _growth(detail: ForkCatalogDetail) -> CatalogSection:
        return CatalogSection(
            title="1–80레벨 레벨업 경험치와 패널 수정",
            fields=tuple(
                official(
                    f"Lv.{row.level} · NeedExp {row.need_exp}",
                    lines([
                        f"{modifier.property_name_zh or modifier.property_id} "
                        f"{modifier.display_value} ({modifier.operation})"
                        for modifier in row.modifiers
                    ]),
                )
                for row in detail.growth_levels
            ) or (derived("성장 데이터", "unavailable: 이 아크에는 정식 성장 행이 없습니다"),),
        )

    @staticmethod
    def _breakthroughs(detail: ForkCatalogDetail) -> CatalogSection:
        fields = []
        for row in detail.breakthroughs:
            item_costs = ", ".join(
                f"{cost.item_id} × {cost.amount if cost.amount is not None else cost.raw_value}"
                for cost in row.item_costs
            ) or "정식 필드가 비어 있음"
            gold_costs = ", ".join(
                f"{cost.item_id} × {cost.amount if cost.amount is not None else cost.raw_value}"
                for cost in row.gold_costs
            ) or "정식 필드가 비어 있음"
            modifiers = "; ".join(
                f"{modifier.property_name_zh or modifier.property_id} {modifier.display_value}"
                for modifier in row.modifiers
            ) or "패널 수정 없음"
            fields.append(official(
                f"단계 {row.stage} · 상한 Lv.{row.max_fork_level}",
                f"재료: {item_costs}; 폰스: {gold_costs}; 패널: {modifiers}",
            ))
        for state in detail.critical_level_states:
            fields.append(derived(
                f"Lv.{state.level} {state.state}",
                f"돌파 단계 {state.stage} / NeedExp {state.growth.need_exp}",
            ))
        return CatalogSection(
            title="돌파 단계, 정식 소모와 임계 상태",
            fields=tuple(fields) or (derived("돌파 데이터", "unavailable: 이 아크에는 정식 돌파 행이 없습니다"),),
        )

    @staticmethod
    def _refinements(detail: ForkCatalogDetail) -> CatalogSection:
        return CatalogSection(
            title="아크 스킬 / 믹싱 1–5 레벨",
            fields=tuple(
                official(
                    f"믹싱 {row.level} · {row.title_zh or '未保留标题'}",
                    lines([
                        row.description_zh or "정식 설명 없음",
                        "매개변수:" + (", ".join(
                            f"{parameter.name_id}={parameter.display_value}"
                            for parameter in row.parameters
                        ) or "없음"),
                        "폰스 필드:" + (row.need_gold_raw or "비어 있음"),
                        "Buff:" + (", ".join(row.buff_asset_paths) or "없음"),
                    ]),
                )
                for row in detail.refinement_levels
            ) or (derived("믹싱 스킬", "unavailable: 믹싱 레벨 행이 없습니다"),),
        )

    @staticmethod
    def _buffs(detail: ForkCatalogDetail) -> CatalogSection:
        fields = []
        for buff in detail.buff_definitions:
            source = (
                CatalogValueSource.OFFICIAL_STATIC
                if buff.target_available else CatalogValueSource.PROJECT_ANNOTATION
            )
            values = [
                f"에셋={buff.asset_path}",
                f"정의={buff.definition_id or '未解析'}",
                f"사용 가능={buff.target_available}",
                f"지속={buff.duration_policy or '未保留'}",
                f"중첩={buff.stacking_type or '未保留'} / {buff.stack_limit_count}",
                f"GE={buff.gameplay_effect_id or '未解析'}",
                "수정=" + (", ".join(
                    f"{row.property_name_zh or row.property_id}:{row.magnitude_kind}={row.magnitude_value}"
                    for row in buff.modifiers
                ) or "없음"),
                "트리거=" + (", ".join(
                    f"{row.event_type or '未知'}->{row.target_gameplay_effect_id or row.target_effect_asset_path or '未解析'}"
                    for row in buff.triggers
                ) or "없음"),
            ]
            fields.append(official(
                f"믹싱 {buff.refinement_level} · {buff.definition_id or buff.asset_path}",
                lines(values),
                copyable=True,
            ) if source is CatalogValueSource.OFFICIAL_STATIC else annotation(
                f"믹싱 {buff.refinement_level} · 가져오지 않은 대상",
                lines(values),
                copyable=True,
            ))
        return CatalogSection(title="Buff, GE, modifier와 트리거", fields=tuple(fields))

    @staticmethod
    def _relations(detail: ForkCatalogDetail) -> CatalogSection:
        references = tuple(
            CatalogReference("연관 캐릭터 보기", "character", relation.target_id)
            for relation in detail.relations
            if relation.available and relation.kind == "character" and relation.target_id
        )
        fields = [
            official(f"리소스 · {resource.kind}", resource.path, copyable=True)
            if resource.origin is CatalogOrigin.OFFICIAL_STATIC
            else annotation(f"리소스 · {resource.kind}", resource.path, copyable=True)
            for resource in detail.resources
        ]
        fields.extend(
            official(
                f"관계 · {relation.kind}",
                f"{relation.label} · {relation.copy_value}",
                copyable=True,
            ) if relation.origin is CatalogOrigin.OFFICIAL_STATIC else annotation(
                f"관계 · {relation.kind}",
                f"{relation.label} · {relation.copy_value}"
                + ("" if relation.available else " · unavailable: 대상을 가져오지 않음"),
                copyable=True,
            )
            for relation in detail.relations
        )
        return CatalogSection(
            title="리소스 경로와 구조화된 관계",
            fields=tuple(fields),
            references=references,
        )
