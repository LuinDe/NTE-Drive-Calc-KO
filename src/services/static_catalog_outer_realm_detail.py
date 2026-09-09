# 轨外之境关卡与单个刷怪成员的只读资料投影。
"""Build outer-realm catalog details without hiding unscheduled configurations."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from src.services.static_catalog_monster_display import (
    NAME_UNAVAILABLE,
    display_fight_stage,
)
from src.services.static_catalog_monster_models import (
    CatalogDetail,
    CatalogRelation,
    CatalogSection,
)


_RELEASE_LABELS = {
    "current": "현재 회차",
    "next": "다음 회차",
    "historical": "종료됨",
    "scheduled": "오픈 예정",
    "unscheduled": "일정 미제공",
}


def _key(kind: str, *parts: object) -> str:
    return "|".join((kind, *(quote(str(part), safe="") for part in parts)))


def _unique_relations(relations: list[CatalogRelation]) -> tuple[CatalogRelation, ...]:
    return tuple(dict.fromkeys(relations))


class StaticCatalogOuterRealmDetailMixin:
    """Project exact level/member rows while keeping schedule optional."""

    _queries: Any
    _terminology_service: Any

    def _outer_detail(
        self, config_id: str, level_id: int, fight_stage: str,
    ) -> CatalogDetail | None:
        row = self._queries.outer_realm_encounter(config_id, level_id, fight_stage)
        if row is None:
            return None
        state = self._release_state(
            row.get("starts_at_mainland"), row.get("ends_at_mainland")
        )
        title = str(row.get("name_zh") or "").strip()
        if not title or "\ufffd" in title:
            title = NAME_UNAVAILABLE
        half_label = display_fight_stage(self._terminology_service, fight_stage)
        entry = self._entry(
            _key("outer_realm", config_id, level_id, fight_stage),
            domain="encounter", play_mode="outer_realm", title_value=title,
            fallback=NAME_UNAVAILABLE,
            subtitle=f"궤외 영역 · {_RELEASE_LABELS[state]} · {half_label}",
            primary_id=config_id, secondary_id=fight_stage, release_state=state,
            secondary_label=half_label,
        )
        sections = [CatalogSection("정식 회차와 구역", (
            self._value("구성 ID", config_id, copyable=True),
            self._value("층", level_id),
            self._value(
                "전반/후반", fight_stage, copyable=True,
                display_value=half_label,
            ),
            self._value("중국 서버 시작", row.get("starts_at_mainland")),
            self._value("중국 서버 종료", row.get("ends_at_mainland")),
            self._value(
                "회차 상태", _RELEASE_LABELS[state], "project_annotation",
            ),
        ), (
            "정식 일정이 있으면 중국 서버 시간 기준으로 판정합니다. 일정이 없는 구성도 정식 층·"
            "하프·몬스터 풀을 표시하며, 오픈 시간은 추측하지 않습니다."
        ))]
        relations: list[CatalogRelation] = []
        for index, member in enumerate(row.get("members", ()), 1):
            section_title = f"스폰 슬롯 {index}"
            sections.append(CatalogSection(section_title, (
                self._value("스폰 풀 ID", member.get("monster_pool_id"), copyable=True),
                self._value("스폰 순서", member.get("spawn_ordinal")),
                self._value("웨이브", member.get("wave")),
                self._value("다음 리스폰 방식", member.get("next_spawn_type")),
                self._value("리스폰 시간", member.get("spawn_time")),
                self._value("몬스터 순서", member.get("monster_ordinal")),
                self._value("몬스터 클래스 경로", member.get("monster_class_path"), copyable=True),
                self._localized_value("몬스터 중국어 이름", member.get("monster_name_zh")),
                self._value("수량", member.get("monster_count")),
                self._value("레벨", member.get("monster_level")),
            )))
            sections.append(self._combat_profile_section(
                member.get("profile"), level=member.get("monster_level"),
                title=f"{section_title} 공식 프로필",
            ))
            relations.append(CatalogRelation(
                f"{section_title} 적 정보",
                _key(
                    "outer_member", config_id, level_id, fight_stage,
                    member.get("spawn_ordinal"), member.get("monster_pool_id"),
                    member.get("monster_ordinal"),
                ),
                "exact_spawn_pool_member",
                "정식 스테이지·하프·스폰 순서·몬스터 풀·몬스터 순서로 함께 특정됩니다.",
            ))
            relations.extend(self._path_relations(member.get("monster_class_path")))
        sections.append(self._source_section(row.get("source")))
        return CatalogDetail(entry, tuple(sections), _unique_relations(relations))

    def _outer_member_detail(
        self,
        config_id: str,
        level_id: int,
        fight_stage: str,
        spawn_ordinal: int,
        monster_pool_id: str,
        monster_ordinal: int,
    ) -> CatalogDetail | None:
        row = self._queries.outer_realm_member(
            config_id, level_id, fight_stage, spawn_ordinal,
            monster_pool_id, monster_ordinal,
        )
        if row is None:
            return None
        path = str(row.get("monster_class_path") or "")
        object_name = path.rsplit(".", 1)[-1]
        if object_name.endswith("_C"):
            object_name = object_name[:-2]
        state = self._release_state(
            row.get("starts_at_mainland"), row.get("ends_at_mainland")
        )
        half_label = display_fight_stage(self._terminology_service, fight_stage)
        entry = self._entry(
            _key(
                "outer_member", config_id, level_id, fight_stage,
                spawn_ordinal, monster_pool_id, monster_ordinal,
            ),
            domain="monster", play_mode="outer_realm",
            title_value=row.get("monster_name_zh"), fallback=NAME_UNAVAILABLE,
            subtitle=f"궤외 영역 · {_RELEASE_LABELS[state]} · {half_label}",
            primary_id=object_name or monster_pool_id,
            secondary_id=path, release_state=state,
            secondary_label=half_label,
        )
        sections = [CatalogSection("정식 등장 기록", (
            self._value("구성 ID", config_id, copyable=True),
            self._value("층", level_id),
            self._value("전반/후반", fight_stage, copyable=True, display_value=half_label),
            self._value("스폰 풀 ID", monster_pool_id, copyable=True),
            self._value("스폰 순서", spawn_ordinal),
            self._value("몬스터 순서", monster_ordinal),
            self._value("몬스터 클래스 경로", path, copyable=True),
            self._localized_value("몬스터 중국어 이름", row.get("monster_name_zh")),
            self._value("수량", row.get("monster_count")),
            self._value("레벨", row.get("monster_level")),
        ), "정식 스폰 풀 멤버이며, 중국어 이름으로 몬스터 신원을 역추정하지 않습니다.")]
        sections.append(self._combat_profile_section(
            row.get("profile"), level=row.get("monster_level"),
            title="이번 등장 공식 프로필",
            note="이 스폰 멤버의 정식 profile_set + pack_id로 분석합니다.",
        ))
        sections.append(self._source_section(row.get("source")))
        return CatalogDetail(
            entry,
            tuple(sections),
            _unique_relations(self._path_relations(path)),
        )
