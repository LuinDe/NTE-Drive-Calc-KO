# 将角色资料域映射到公共只读提供器契约。
"""Adapt the character catalog domain to the common read-only provider contract."""

from __future__ import annotations

from pathlib import Path

from src.features.static_catalog.contracts import (
    CatalogDetail,
    CatalogDomain,
    CatalogItem,
    CatalogPage,
    CatalogReference,
    CatalogSection,
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
from src.services.static_catalog_character_models import CharacterDetail, CharacterSummary
from src.services.static_catalog_character_service import StaticCatalogCharacterService
from src.storage.sqlite.static_catalog_character_queries import (
    StaticCatalogCharacterQueries,
)


CHARACTER_DOMAIN = CatalogDomain(
    key="character",
    label="캐릭터 데이터",
    description="레벨 패널, 돌파 상태, 스킬 소모, 각성, 호감도, 육성 및 졸업 템플릿",
    order=10,
)


class CharacterCatalogProvider:
    """Own one read-only character DAO and expose only common catalog DTOs."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path).resolve()
        self._queries = StaticCatalogCharacterQueries(self._database_path)
        self._service = StaticCatalogCharacterService(self._queries)
        self._closed = False

    @property
    def domain(self) -> CatalogDomain:
        return CHARACTER_DOMAIN

    def close(self) -> None:
        if not self._closed:
            self._queries.close()
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
        page = self._service.list_characters(query=query, offset=offset, limit=limit)
        self._ensure_dataset(release, page.dataset)
        return CatalogPage(
            items=tuple(self._item(item) for item in page.items),
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )

    def detail(
        self, release: StaticCatalogRelease, record_id: str,
    ) -> CatalogDetail | None:
        ensure_release_path(release, self._database_path)
        try:
            character_id = int(record_id)
        except (TypeError, ValueError):
            return None
        detail = self._service.get_character_detail(character_id)
        if detail is None:
            return None
        self._ensure_dataset(release, detail.dataset)
        growth = self._service.list_growth(character_id, limit=200)
        combat = self._service.list_combat_links(character_id, limit=500)
        sections = [
            self._identity(detail),
            self._availability(detail),
            CatalogSection(
                title="1–80레벨 패널 (임계 레벨 돌파 전후 포함)",
                fields=tuple(
                    official(
                        f"Lv.{point.level} · 단계 {point.breakthrough_stage} · {point.state}",
                        f"HP {point.hp_base:g} / 공격 {point.atk_base:g} / 방어 {point.def_base:g}",
                    )
                    for point in growth.items
                ) or (derived("패널 상태", "unavailable: 이 캐릭터에는 성장 행이 없습니다"),),
            ),
        ]
        sections.extend(self._detail_sections(detail))
        if combat.items:
            sections.append(CatalogSection(
                title="GA, GE 및 Buff 정식 관계",
                fields=tuple(
                    official(
                        f"{link.relationship_kind} · {link.binding_kind}",
                        " / ".join(filter(None, (
                            link.ability_id,
                            link.event_tag,
                            link.gameplay_effect_id,
                            link.buff_definition_id,
                            link.effect_asset_path,
                        ))),
                        copyable=True,
                    )
                    for link in combat.items
                ),
            ))
        return CatalogDetail(
            item=self._item(detail.character),
            sections=tuple(sections),
            notes=(
                "캐릭터 레벨업과 돌파 요구 관계가 없으면 unavailable 상태를 유지하며, 텍스트·동명 캐릭터·계정 데이터로 추측하지 않습니다.",
                "스킬 레벨업 재료 이름은 공용 용어 서비스가 해석합니다. 이 프로바이더의 raw ID는 내부 감사용으로만 쓰이며 플레이어 메인 화면에는 표시되지 않습니다.",
            ),
        )

    @staticmethod
    def _item(summary: CharacterSummary) -> CatalogItem:
        subtitle = (
            f"{summary.element_label} · 스킬 {summary.skill_count} · "
            f"각성 {summary.awakening_count} · 성장 상태 {summary.growth_count}"
        )
        return CatalogItem(
            domain_key=CHARACTER_DOMAIN.key,
            record_id=str(summary.character_id),
            title=summary.name_zh,
            subtitle=subtitle,
        )

    @staticmethod
    def _ensure_dataset(release: StaticCatalogRelease, dataset: object) -> None:
        ensure_release_metadata(
            release,
            dataset_id=str(getattr(dataset, "dataset_id")),
            schema_version=int(getattr(dataset, "schema_version")),
            importer_version=int(getattr(dataset, "importer_version")),
            built_at_utc=str(getattr(dataset, "built_at_utc")),
        )

    @staticmethod
    def _identity(detail: CharacterDetail) -> CatalogSection:
        item = detail.character
        return CatalogSection(
            title="캐릭터 식별 정보와 목록",
            fields=(
                official("character_id", item.character_id, copyable=True),
                official("중국어 이름", item.name_zh),
                official("属性", f"{item.element_label} · {item.element_type or '未保留'}"),
                official("그룹", item.group_type),
                official("Actor 경로", item.actor_path, copyable=True),
                official("중국 서버 공개 시간", item.mainland_show_time),
                annotation("논리 캐릭터 키", item.logical_character_key, copyable=True),
                annotation("정규 캐릭터 ID", item.canonical_character_id, copyable=True),
                annotation("분류", item.classification),
                annotation("주석 출처", detail.annotation_source),
            ),
        )

    @staticmethod
    def _availability(detail: CharacterDetail) -> CatalogSection:
        return CatalogSection(
            title="데이터 가용성",
            fields=(
                derived("성장 상태 수", detail.growth_count),
                derived("전투 관계 수", detail.combat_link_count),
                *(derived(gap.label, f"{gap.status}: {gap.reason}") for gap in detail.gaps),
            ),
        )

    def _detail_sections(self, detail: CharacterDetail) -> list[CatalogSection]:
        sections: list[CatalogSection] = []
        if detail.likeability is not None:
            likeability = detail.likeability
            sections.append(CatalogSection(
                title=f"호감도 {likeability.required_level}레벨 보너스",
                fields=(
                    official("modify_data_id", likeability.modify_data_id, copyable=True),
                    *(official(
                        prop.display_name,
                        f"{prop.value:g} · {prop.modifier_operation}",
                    ) for prop in likeability.properties),
                ),
            ))
        for awakening in detail.awakenings:
            sections.append(CatalogSection(
                title=f"각성 {awakening.ordinal} · {awakening.title_zh or awakening.effect_id}",
                fields=(
                    official("effect_id", awakening.effect_id, copyable=True),
                    official("유형", awakening.awaken_type),
                    official("설명", awakening.description_zh),
                    official("Gameplay Effects", lines(list(awakening.gameplay_effect_ids)), copyable=True),
                    official(
                        "스킬 레벨 수정",
                        lines([f"{row.skill_id} {row.level_delta:+d}" for row in awakening.skill_level_bonuses]),
                    ),
                    official(
                        "구조화된 효과",
                        lines([f"{row.path} = {row.value_json}" for row in awakening.structured_effects]),
                    ),
                ),
            ))
        for skill in detail.skills:
            sections.append(CatalogSection(
                title=f"스킬 · {skill.name_zh or skill.skill_id}",
                fields=(
                    official("skill_id", skill.skill_id, copyable=True),
                    official("유형 / 순번", f"{skill.ability_type} / {skill.ability_index}"),
                    official("Gameplay Tag", skill.gameplay_tag, copyable=True),
                    official("GA 경로", skill.gameplay_ability_path, copyable=True),
                    official("GE 경로", skill.gameplay_effect_path, copyable=True),
                    official(
                        "레벨, 해금 및 소모",
                        lines([
                            f"Lv.{level.level} · 돌파 {level.required_breakthrough_stage} · "
                            f"각성 {level.required_awaken_level} · "
                            + (", ".join(
                                f"{cost.item_id} × {cost.quantity:g}" for cost in level.costs
                            ) or "재료 없음")
                            for level in skill.levels
                        ]),
                    ),
                    official(
                        "정식 설명",
                        lines([
                            " · ".join(filter(None, (row.title_zh, row.description_zh, row.unlock_description_zh)))
                            for row in skill.descriptions
                        ]),
                    ),
                    official(
                        "레벨 안내와 피해 인덱스",
                        lines([
                            f"{row.name_id or row.ordinal} · {row.value_description_zh or row.description_zh or '未保留'}"
                            f" · damage={','.join(row.damage_effect_ids) or '无'}"
                            for row in skill.level_hints
                        ]),
                    ),
                ),
            ))
        if detail.cultivation is not None:
            guide = detail.cultivation
            references = tuple(
                CatalogReference("추천 아크 보기", "fork", fork_id)
                for fork_id, _name, _description in guide.fork_recommendations
            )
            sections.append(CatalogSection(
                title="육성 가이드",
                fields=(
                    official("점수 임계값", f"S {guide.s_score:g} / A {guide.a_score:g}"),
                    official("추천 속성", lines([f"{name} ({property_id})" for property_id, name in guide.attribute_recommendations])),
                    official("추천 아크", lines([f"{name} ({fork_id})" for fork_id, name, _description in guide.fork_recommendations])),
                    official(
                        "단계별 루트",
                        lines([
                            f"단계 {row.ordinal}: 캐릭터 {row.character_level} / 아크 {row.fork_level} / "
                            f"콘솔 {row.core_item_id} Lv.{row.core_level} / 드라이브 Lv.{row.equipment_level} / "
                            + "스킬:"
                            + (
                                ", ".join(
                                    f"{sex_kind}:{ability_id} Lv.{recommended_level}"
                                    for sex_kind, ability_id, recommended_level
                                    in row.recommended_skills
                                )
                                or "정식 단계 스킬 없음"
                            )
                            for row in guide.stages
                        ]),
                    ),
                ),
                references=references,
            ))
        if detail.graduation is not None:
            graduation = detail.graduation
            references = (
                (CatalogReference("졸업 아크 보기", "fork", graduation.fork_id),)
                if graduation.fork_id else ()
            )
            sections.append(CatalogSection(
                title="졸업 템플릿 (프로젝트 파생)",
                fields=(
                    annotation("출처 유형", graduation.source_kind),
                    annotation("아크", f"{graduation.fork_name_zh or '未保留'} ({graduation.fork_id or '无'})"),
                    annotation("아크 레벨 / 믹싱", f"{graduation.fork_level} / {graduation.fork_refinement_level}"),
                    annotation("콘솔 세트", f"{graduation.core_suit_name_zh or '未保留'} ({graduation.core_suit_id or '无'})"),
                    annotation("카트리지 메인 스탯", graduation.core_main_property_name_zh or graduation.core_main_property_id),
                    annotation("기준 피해", graduation.benchmark_damage),
                ),
                references=references,
            ))
        return sections
