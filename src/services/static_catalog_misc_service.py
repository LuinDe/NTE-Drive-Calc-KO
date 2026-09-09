# 将装备、技能效果、资源关系和来源追溯投影为 Qt 无关的资料库 DTO。
"""Qt-free projections for the B-domain static game catalog."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from src.services.static_catalog_misc_models import (
    CatalogDetail,
    CatalogDomain,
    CatalogField,
    CatalogRelation,
    CatalogRelationPage,
    CatalogSearchItem,
    CatalogSearchPage,
    CatalogSection,
    ORIGIN_ANNOTATION,
    ORIGIN_DERIVED,
    ORIGIN_FORMAL,
    ORIGIN_SOURCE,
    SourceTrace,
    StaticCatalogReleaseMetadata,
)
from src.services.static_catalog_misc_metadata import BASE_FIELD_KEYS
from src.storage.sqlite.static_catalog_misc_queries import StaticCatalogMiscDao


_ORIGIN_LABELS = {
    ORIGIN_FORMAL: "정식 정적 데이터",
    ORIGIN_ANNOTATION: "프로젝트 주석",
    ORIGIN_DERIVED: "파생 표시값",
    ORIGIN_SOURCE: "출처 메타데이터",
}


_DOMAIN_PRESENTATION = {
    "equipment": (
        "장비 및 육성",
        "콘솔, 드라이브, 카트리지, 세트, 형태, 속성, 강화 곡선, 청사진 및 졸업 템플릿",
    ),
    "skills": ("스킬 및 피해", "Gameplay Ability, 스킬 설명, 레벨 안내 및 정식 피해 항목"),
    "effects": ("Buff 및 효과", "Gameplay Effect, Buff, modifier, 발동 관계 및 Gameplay Tag"),
    "assets": ("리소스 및 애니메이션", "Blueprint, Montage, Section, Notify 및 리소스 관계"),
    "sources": ("출처 추적", "보존된 출처 경로, 파일 해시, 행 key 및 콘텐츠 해시"),
}

_ENTITY_DOMAIN = {
    "equipment_item": "equipment",
    "equipment_suit": "equipment",
    "equipment_shape": "equipment",
    "equipment_attribute": "equipment",
    "equipment_curve": "equipment",
    "equipment_buff_curve": "equipment",
    "equipment_modify_pack": "equipment",
    "equipment_plan": "equipment",
    "graduation_template": "equipment",
    "gameplay_ability": "skills",
    "skill_damage": "skills",
    "gameplay_effect": "effects",
    "buff": "effects",
    "combat_effect": "effects",
    "combat_curve": "effects",
    "combat_level_curve": "effects",
    "reaction": "effects",
    "combat_constant": "effects",
    "gameplay_tag": "effects",
    "roguelike_modifier": "effects",
    "blueprint": "assets",
    "montage": "assets",
    "source_file": "sources",
    "source_row": "sources",
}

_ENTITY_ORIGIN = {
    "graduation_template": ORIGIN_DERIVED,
    "combat_effect": ORIGIN_ANNOTATION,
    "source_file": ORIGIN_SOURCE,
    "source_row": ORIGIN_SOURCE,
}

_COPY_BY_KEY = {
    "item_id": "official_id",
    "suit_id": "official_id",
    "shape_id": "official_id",
    "attribute_id": "official_id",
    "curve_id": "official_id",
    "modify_pack_id": "official_id",
    "character_id": "official_id",
    "ability_id": "ga_id",
    "damage_id": "ge_id",
    "gameplay_effect_id": "ge_id",
    "definition_id": "buff_key",
    "effect_definition_id": "buff_key",
    "tag_name": "gameplay_tag",
    "asset_path": "resource_path",
    "class_path": "resource_path",
    "gameplay_ability_path": "resource_path",
    "icon_path": "resource_path",
    "plan_icon_path": "resource_path",
    "background_path": "resource_path",
    "character_image_path": "resource_path",
    "extended_icon_path": "resource_path",
    "buff_object_path": "resource_path",
    "calculation_asset_path": "resource_path",
    "target_effect_asset_path": "resource_path",
    "source_asset_path": "resource_path",
    "montage_asset_path": "resource_path",
    "linked_animation_asset_path": "resource_path",
    "curve_table_asset_path": "resource_path",
    "target_asset_path": "resource_path",
    "target_object_path": "resource_path",
    "effect_asset_path": "resource_path",
    "target_type_asset_path": "resource_path",
    "montage_object_path": "resource_path",
    "notify_object_path": "resource_path",
    "relative_path": "resource_path",
    "sha256": "sha256",
    "content_sha256": "sha256",
    "source_file_sha256": "sha256",
}

_FIELD_LABELS = {
    "item_id": "장비 정식 ID",
    "kind": "장비 유형",
    "quality": "품질",
    "name_zh": "중국어 이름",
    "geometry_id": "형태 ID",
    "geometry_enum": "형태 열거형",
    "grid_count": "점유 칸 수",
    "suit_id": "세트 정식 ID",
    "suit_type_enum": "세트 열거형",
    "max_level": "최고 레벨",
    "strength_pack_id": "강화 팩 ID",
    "is_guide_item": "가이드 아이템",
    "shape_id": "형태 정식 ID",
    "cell_count": "칸 수",
    "first_grid_delta_x": "첫 칸 X 오프셋",
    "first_grid_delta_y": "첫 칸 Y 오프셋",
    "attribute_id": "속성 정식 ID",
    "display_name_zh": "표시 이름",
    "filter_name_zh": "필터 이름",
    "random_attribute_name_zh": "랜덤 스탯 이름",
    "attribute_type": "속성 유형",
    "show_percent": "백분율 표시",
    "score": "공식 점수 매개변수",
    "curve_id": "곡선 정식 ID",
    "interpolation_mode": "보간 방식",
    "default_value": "기본값",
    "modify_pack_id": "수정 팩 정식 ID",
    "conditions": "적용 조건",
    "character_id": "캐릭터 정식 ID",
    "core_item_id": "카트리지 ID",
    "core_level": "카트리지 레벨",
    "module_level": "드라이브 레벨",
    "reference_score": "참고 점수",
    "fork_id": "아크 ID",
    "fork_level": "아크 레벨",
    "fork_refinement_level": "아크 믹싱",
    "core_suit_id": "세트 ID",
    "core_main_property_id": "카트리지 메인 스탯 ID",
    "drive_area": "드라이브 면적",
    "extra_shape_count": "추가 형태 수",
    "benchmark_damage": "템플릿 기준 피해",
    "source_kind": "생성 출처",
    "generated_at_utc": "생성 시간",
    "ability_id": "GA 정식 ID",
    "gameplay_ability_path": "GA 리소스 경로",
    "is_stolen": "탈취 가능 스킬",
    "damage_id": "피해 항목 / GE 정식 ID",
    "damage_type": "피해 유형",
    "damage_source_category": "피해 출처 분류",
    "charge_add": "에너지 충전 증가",
    "unbal_value": "브레이크 수치",
    "heterochrome_add": "이능력 증가",
    "fixed_crit_rate": "고정 치명 확률",
    "atk_rate_base": "공격 배율 배열",
    "def_rate_base": "방어 배율 배열",
    "hp_rate_base": "HP 배율 배열",
    "story_balance_ge_rate": "스토리 밸런스 배율",
    "attack_break_level": "파괴 등급",
    "override_breakable_damage": "파괴 가능 오브젝트 피해 오버라이드",
    "breakable_damage": "파괴 가능 오브젝트 피해",
    "override_breakable_impulse": "파괴 가능 오브젝트 충격량 오버라이드",
    "breakable_impulse": "파괴 가능 오브젝트 충격량",
    "override_vehicle_breakable_impulse": "탈것 파괴 가능 충격량 오버라이드",
    "vehicle_breakable_impulse": "탈것 파괴 가능 충격량",
    "ability_relation_status": "출처 GA 관계 상태",
    "same_name_gameplay_effect_relation_status": "동명 GE 관계 상태",
    "modifier_atk_rate_base_coefficient": "프로젝트 배율 보정 계수",
    "gameplay_effect_index": "GE 정식 인덱스",
    "gameplay_effect_id": "GE 정식 ID",
    "class_path": "GE 클래스 경로",
    "definition_id": "Buff key",
    "definition_kind": "정의 유형",
    "owner_character_id": "소속 캐릭터 ID",
    "duration_policy": "지속 정책",
    "duration_magnitude": "지속 시간 근거",
    "period": "주기 근거",
    "stacking_type": "중첩 유형",
    "stack_limit_count": "중첩 상한",
    "effect_definition_id": "효과 정의 key",
    "owner_kind": "소유자 유형",
    "owner_id": "소유자 ID",
    "effect_kind": "효과 유형",
    "activation_kind": "활성화 방식",
    "description_zh": "설명",
    "formula_version": "프로젝트 공식 버전",
    "curve_table_asset_path": "곡선 테이블 리소스 경로",
    "damage_kind": "피해 범주",
    "reaction_type": "반응 유형",
    "source_effect_id": "출처 효과 ID",
    "mapping_status": "매핑 상태",
    "element_type_1": "원소 1",
    "element_type_2": "원소 2",
    "default_damage_effect_id": "기본 피해 항목 ID",
    "constant_id": "상수 ID",
    "source_time": "입력값",
    "value": "수치",
    "unit": "단위",
    "tag_name": "Gameplay Tag",
    "modifier_id": "속성 팩 정식 ID",
    "ordinal": "序号",
    "property_id": "속성 정식 ID",
    "modifier_operation": "수정 연산",
    "property_value": "속성값",
    "sort_key": "정렬 키",
    "owner_resolution_status": "귀속 분석 상태",
    "asset_path": "리소스 경로",
    "source_asset_path": "출처 리소스 경로",
    "property_path": "속성 경로",
    "asset_name": "리소스 이름",
    "asset_type": "리소스 유형",
    "asset_kind": "리소스 도메인",
    "duration_seconds": "길이 (초)",
    "blend_in_seconds": "블렌드 인 (초)",
    "blend_out_seconds": "블렌드 아웃 (초)",
    "frame_rate_numerator": "프레임 레이트 분자",
    "frame_rate_denominator": "프레임 레이트 분모",
}

def _display(value: object) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "예" if value else "아니요"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return str(value)


def _origin_for(entity_kind: str) -> str:
    return _ENTITY_ORIGIN.get(entity_kind, ORIGIN_FORMAL)


def _field(
    label: str,
    value: object,
    *,
    origin: str,
    copy_kind: str | None = None,
) -> CatalogField:
    return CatalogField(
        label=label,
        value=_display(value),
        origin_kind=origin,
        origin_label=_ORIGIN_LABELS[origin],
        copy_kind=copy_kind,
    )


class StaticCatalogMiscService:
    """Own B-domain information architecture while DAO owns every SQL statement."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        manifest_path: str | Path | None = None,
        dao_factory: Callable[..., StaticCatalogMiscDao] = StaticCatalogMiscDao,
    ) -> None:
        self.database_path = Path(database_path)
        self.manifest_path = Path(manifest_path) if manifest_path is not None else None
        self.dao_factory = dao_factory

    def domains(self) -> tuple[CatalogDomain, ...]:
        with self.dao_factory(self.database_path) as dao:
            counts = dao.catalog_domain_counts()
        return tuple(
            CatalogDomain(key, title, description, counts.get(key, 0))
            for key, (title, description) in _DOMAIN_PRESENTATION.items()
        )

    def search(
        self,
        domain_key: str,
        query: str = "",
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> CatalogSearchPage:
        with self.dao_factory(self.database_path) as dao:
            page = dao.search_catalog_entries(
                domain_key, query, limit=limit, offset=offset
            )
        items = tuple(self._search_item(row) for row in page["items"])
        return CatalogSearchPage(
            domain_key=str(page["domain_key"]),
            query=str(page["query"]),
            offset=int(page["offset"]),
            limit=int(page["limit"]),
            total=int(page["total"]),
            items=items,
        )

    def detail(self, entity_kind: str, entity_key: str) -> CatalogDetail | SourceTrace:
        domain = _ENTITY_DOMAIN.get(str(entity_kind))
        if domain is None:
            raise ValueError(f"지원하지 않는 자료실 엔티티: {entity_kind!r}")
        if entity_kind == "source_file":
            return self.source_trace(source_file_id=int(entity_key))
        if entity_kind == "source_row":
            return self.source_trace(source_row_id=int(entity_key))
        with self.dao_factory(self.database_path) as dao:
            if domain == "equipment":
                raw = dao.get_equipment_catalog_detail(entity_kind, entity_key)
            elif domain in {"skills", "effects"}:
                raw = dao.get_effect_catalog_detail(entity_kind, entity_key)
            elif domain == "assets":
                raw = dao.get_asset_catalog_detail(entity_kind, entity_key)
            else:
                raw = None
        if raw is None:
            raise LookupError(f"자료실에 {entity_kind}:{entity_key}이(가) 없습니다")
        return self._detail_from_raw(entity_kind, entity_key, raw)

    def source_trace(
        self,
        *,
        source_row_id: int | None = None,
        source_file_id: int | None = None,
    ) -> SourceTrace:
        with self.dao_factory(self.database_path) as dao:
            raw = dao.get_source_trace(
                source_row_id=source_row_id, source_file_id=source_file_id
            )
        if raw is None:
            raise LookupError("해당 출처 레코드를 찾을 수 없습니다")
        omitted = self.release_metadata().source_payloads_omitted
        payload_present = bool(raw["payload_present"])
        if omitted:
            explanation = (
                "배포 패키지에는 원본 payload가 생략되어 있으며, 여기에는 보존된 출처 경로, 행 key 및 해시만 표시합니다."
            )
        elif payload_present:
            explanation = "현재 빌드에는 해당 출처 행의 payload가 보존되어 있지만, 이 자료실에는 여전히 출처 메타데이터만 표시합니다."
        else:
            explanation = "현재 출처 행에는 표시할 payload가 없으며, 출처 메타데이터만 보존합니다."
        return SourceTrace(
            source_file_id=int(raw["source_file_id"]),
            relative_path=str(raw["relative_path"]),
            source_file_sha256=str(raw["source_file_sha256"]),
            declared_row_count=int(raw["row_count"]),
            source_row_id=(
                int(raw["source_row_id"]) if raw.get("source_row_id") is not None else None
            ),
            row_key=str(raw["row_key"]) if raw.get("row_key") is not None else None,
            content_sha256=(
                str(raw["content_sha256"])
                if raw.get("content_sha256") is not None
                else None
            ),
            payload_present=payload_present,
            payloads_omitted=omitted,
            explanation=explanation,
        )

    def source_rows(
        self,
        source_file_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> CatalogRelationPage:
        with self.dao_factory(self.database_path) as dao:
            page = dao.list_source_file_rows(
                source_file_id, limit=limit, offset=offset
            )
        rows = tuple(
            CatalogSection(
                title=str(row["row_key"]),
                fields=(
                    _field(
                        "출처 행 ID", row["source_row_id"], origin=ORIGIN_SOURCE,
                        copy_kind="official_id",
                    ),
                    _field(
                        "콘텐츠 SHA-256", row["content_sha256"], origin=ORIGIN_SOURCE,
                        copy_kind="sha256",
                    ),
                ),
            )
            for row in page["items"]
        )
        return CatalogRelationPage(
            relation_kind="source_rows",
            offset=int(page["offset"]),
            limit=int(page["limit"]),
            total=int(page["total"]),
            rows=rows,
        )

    def asset_relations(
        self,
        entity_kind: str,
        entity_key: str,
        relation_kind: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> CatalogRelationPage:
        with self.dao_factory(self.database_path) as dao:
            page = dao.list_asset_relations(
                entity_kind,
                entity_key,
                relation_kind,
                limit=limit,
                offset=offset,
            )
        return CatalogRelationPage(
            relation_kind=relation_kind,
            offset=int(page["offset"]),
            limit=int(page["limit"]),
            total=int(page["total"]),
            rows=tuple(
                self._mapping_section(f"{relation_kind} #{offset + index + 1}", row)
                for index, row in enumerate(page["items"])
            ),
        )

    def release_metadata(self) -> StaticCatalogReleaseMetadata:
        if self.manifest_path is None:
            raise ValueError("정적 배포 manifest 경로가 제공되지 않았습니다")
        raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        database = raw.get("database") or {}
        build_tool = raw.get("build_tool") or {}
        return StaticCatalogReleaseMetadata(
            dataset_id=str(database.get("dataset_id") or ""),
            schema_version=int(database.get("schema_version") or 0),
            importer_version=int(build_tool.get("importer_version") or 0),
            generated_at_utc=str(database.get("generated_at_utc") or ""),
            database_sha256=str(database.get("sha256") or ""),
            source_payloads_omitted=bool(database.get("source_payloads_omitted")),
        )

    @staticmethod
    def _search_item(row: Mapping[str, Any]) -> CatalogSearchItem:
        entity_kind = str(row["entity_kind"])
        origin = _origin_for(entity_kind)
        return CatalogSearchItem(
            entity_kind=entity_kind,
            entity_key=str(row["entity_key"]),
            title=str(row["title"]),
            subtitle=str(row["subtitle"]),
            origin_kind=origin,
            origin_label=_ORIGIN_LABELS[origin],
            source_row_id=(
                int(row["source_row_id"]) if row.get("source_row_id") is not None else None
            ),
            source_file_id=(
                int(row["source_file_id"])
                if row.get("source_file_id") is not None
                else None
            ),
        )

    def _detail_from_raw(
        self,
        entity_kind: str,
        entity_key: str,
        raw: Mapping[str, Any],
    ) -> CatalogDetail:
        origin = _origin_for(entity_kind)
        relation_counts = dict(raw.get("relation_counts") or {})
        if entity_kind == "montage" and raw.get("notify_count") is not None:
            relation_counts["notifies"] = int(raw["notify_count"])
        fields = tuple(
            _field(
                _FIELD_LABELS.get(key, key),
                raw.get(key),
                origin=self._field_origin(entity_kind, key),
                copy_kind=_COPY_BY_KEY.get(key),
            )
            for key in BASE_FIELD_KEYS[entity_kind]
            if key in raw
        )
        sections: list[CatalogSection] = [CatalogSection("기본 정보", fields)]
        relations = list(self._relations(entity_kind, raw))
        for key, value in raw.items():
            if key in BASE_FIELD_KEYS[entity_kind] or key in {
                "source_row_id", "source_file_id", "relation_counts",
            }:
                continue
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                sections.extend(self._sequence_sections(entity_kind, key, value))
            elif isinstance(value, Mapping):
                sections.append(self._mapping_section(self._section_title(key), value, origin))
        title, subtitle = self._detail_title(entity_kind, entity_key, raw)
        if relation_counts:
            sections.append(
                self._mapping_section("페이징 관계 규모", relation_counts, ORIGIN_FORMAL)
            )
        return CatalogDetail(
            entity_kind=entity_kind,
            entity_key=str(entity_key),
            title=title,
            subtitle=subtitle,
            origin_kind=origin,
            origin_label=_ORIGIN_LABELS[origin],
            sections=tuple(sections),
            relations=tuple(relations),
            source_row_id=(
                int(raw["source_row_id"]) if raw.get("source_row_id") is not None else None
            ),
            source_file_id=(
                int(raw["source_file_id"])
                if raw.get("source_file_id") is not None
                else None
            ),
        )

    @staticmethod
    def _field_origin(entity_kind: str, key: str) -> str:
        if entity_kind == "graduation_template":
            return ORIGIN_DERIVED
        if entity_kind == "combat_effect":
            return ORIGIN_ANNOTATION
        if key.endswith("_relation_status") or key == "owner_resolution_status":
            return ORIGIN_DERIVED
        if key in {"modifier_atk_rate_base_coefficient", "formula_version"}:
            return ORIGIN_ANNOTATION
        return ORIGIN_FORMAL

    def _sequence_sections(
        self,
        entity_kind: str,
        key: str,
        values: Sequence[Any],
    ) -> list[CatalogSection]:
        title = self._section_title(key)
        if not values:
            return []
        origin = self._field_origin(entity_kind, key)
        if all(isinstance(value, Mapping) for value in values):
            return [
                self._mapping_section(f"{title} #{index + 1}", value, origin)
                for index, value in enumerate(values)
            ]
        return [
            CatalogSection(
                title,
                tuple(
                    _field(f"#{index + 1}", value, origin=origin)
                    for index, value in enumerate(values)
                ),
            )
        ]

    @staticmethod
    def _mapping_section(
        title: str,
        values: Mapping[str, Any],
        origin: str = ORIGIN_FORMAL,
    ) -> CatalogSection:
        return CatalogSection(
            title=title,
            fields=tuple(
                _field(
                    _FIELD_LABELS.get(key, key),
                    value,
                    origin=origin,
                    copy_kind=_COPY_BY_KEY.get(key),
                )
                for key, value in values.items()
            ),
        )

    @staticmethod
    def _section_title(key: str) -> str:
        return {
            "strength_levels": "강화 비용",
            "required_shapes": "세트 요구 형태",
            "effects": "세트 효과",
            "cells": "형태 칸",
            "curves": "연관 강화 곡선",
            "points": "곡선 포인트",
            "modifiers": "Modifier",
            "descriptions": "스킬 설명",
            "level_hints": "레벨 안내",
            "tags": "Gameplay Tag",
            "triggers": "발동 효과",
            "buff_links": "Buff / GE 관계",
            "sections": "Montage Section",
            "profile": "고정 계산 프로필",
            "equipment": "졸업 장비",
            "parameters": "프로젝트 구조화 매개변수",
            "properties": "속성 Modifier",
        }.get(key, key)

    @staticmethod
    def _detail_title(
        entity_kind: str,
        entity_key: str,
        raw: Mapping[str, Any],
    ) -> tuple[str, str]:
        title_keys = {
            "equipment_item": "name_zh",
            "equipment_suit": "name_zh",
            "equipment_attribute": "display_name_zh",
            "equipment_plan": "character_name_zh",
            "graduation_template": "character_name_zh",
            "gameplay_ability": "name_zh",
            "skill_damage": "damage_id",
            "gameplay_effect": "gameplay_effect_id",
            "buff": "definition_id",
            "combat_effect": "effect_definition_id",
            "combat_curve": "curve_id",
            "combat_level_curve": "curve_id",
            "reaction": "reaction_type",
            "combat_constant": "constant_id",
            "gameplay_tag": "tag_name",
            "roguelike_modifier": "modifier_id",
            "blueprint": "asset_name",
            "montage": "asset_path",
        }
        title = str(raw.get(title_keys.get(entity_kind, "")) or entity_key)
        subtitle = {
            "equipment_item": "장비 템플릿",
            "equipment_suit": "카트리지 세트",
            "equipment_shape": "드라이브 형태",
            "equipment_attribute": "장비 속성 목록",
            "equipment_curve": "장비 메인 속성 강화 곡선",
            "equipment_buff_curve": "장비 효과 곡선",
            "equipment_modify_pack": "장비 속성 수정 팩",
            "equipment_plan": "공식 장비 청사진",
            "graduation_template": "프로젝트 파생 졸업 템플릿",
            "gameplay_ability": "Gameplay Ability",
            "skill_damage": "정식 스킬 피해 항목",
            "gameplay_effect": "Gameplay Effect",
            "buff": "Buff 정의",
            "combat_effect": "프로젝트 구조화 효과 주석",
            "combat_curve": "정식 전투 곡선",
            "combat_level_curve": "정식 레벨 곡선",
            "reaction": "정식 반응 정의",
            "combat_constant": "정식 전투 상수",
            "gameplay_tag": "Gameplay Tag",
            "roguelike_modifier": "정식 게임플레이 속성 팩",
            "blueprint": "Blueprint 리소스",
            "montage": "Montage 애니메이션",
        }[entity_kind]
        return title, subtitle

    @staticmethod
    def _relations(
        entity_kind: str,
        raw: Mapping[str, Any],
    ) -> tuple[CatalogRelation, ...]:
        relations: list[CatalogRelation] = []

        def add(label: str, kind: str, key: object, title: object | None = None) -> None:
            if key not in (None, "", "None"):
                relations.append(
                    CatalogRelation(label, kind, str(key), str(title or key))
                )

        if entity_kind == "equipment_item":
            add("세트 보기", "equipment_suit", raw.get("suit_id"))
            add("형태 보기", "equipment_shape", raw.get("geometry_id"))
        elif entity_kind == "equipment_suit":
            for effect in raw.get("effects") or ():
                add("세트 Buff 보기", "buff", effect.get("buff_object_path"))
                add(
                    "속성 수정 팩 보기",
                    "equipment_modify_pack",
                    effect.get("modify_pack_id"),
                )
            for shape in raw.get("required_shapes") or ():
                add("요구 형태 보기", "equipment_shape", shape.get("shape_id"))
        elif entity_kind == "equipment_plan":
            add("카트리지 보기", "equipment_item", raw.get("core_item_id"), raw.get("core_name_zh"))
            for item_id in raw.get("module_item_ids") or ():
                add("드라이브 보기", "equipment_item", item_id)
        elif entity_kind == "graduation_template":
            add("아크 보기", "fork", raw.get("fork_id"))
            add("세트 보기", "equipment_suit", raw.get("core_suit_id"))
            add("메인 스탯 보기", "equipment_attribute", raw.get("core_main_property_id"))
        elif entity_kind == "gameplay_ability":
            add("GA 리소스 보기", "blueprint", raw.get("gameplay_ability_path"))
            for hint in raw.get("level_hints") or ():
                for effect_id in hint.get("damage_effect_ids") or ():
                    add("피해 항목 보기", "skill_damage", effect_id)
                for field in ("defense_effect_ids", "health_effect_ids"):
                    for effect_id in hint.get(field) or ():
                        add("GE 보기", "gameplay_effect", effect_id)
        elif entity_kind == "skill_damage":
            if raw.get("ability_relation_status") == "available":
                add("출처 GA 보기", "gameplay_ability", raw.get("ability_id"))
            if raw.get("same_name_gameplay_effect_relation_status") == "available":
                add("동명 GE 보기", "gameplay_effect", raw.get("damage_id"))
        elif entity_kind == "gameplay_effect":
            add("GE 리소스 보기", "blueprint", raw.get("asset_path"))
        elif entity_kind == "buff":
            add("Buff 리소스 보기", "blueprint", raw.get("asset_path"))
            for modifier in raw.get("modifiers") or ():
                add("Calculation 보기", "blueprint", modifier.get("calculation_asset_path"))
            for trigger in raw.get("triggers") or ():
                add("발동 대상 보기", "buff", trigger.get("target_effect_asset_path"))
        elif entity_kind == "combat_effect":
            for link in raw.get("buff_links") or ():
                if bool(link.get("target_available")):
                    add("Buff / GE 보기", "buff", link.get("target_asset_path"))
        elif entity_kind == "combat_level_curve":
            add("출처 GE 보기", "gameplay_effect", raw.get("source_effect_id"))
        elif entity_kind == "reaction":
            add("기본 피해 항목 보기", "skill_damage", raw.get("default_damage_effect_id"))
        elif entity_kind == "gameplay_tag":
            add("출처 Blueprint 보기", "blueprint", raw.get("source_asset_path"))
        return tuple(relations)
