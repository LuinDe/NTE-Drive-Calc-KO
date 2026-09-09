# 将怪物和玩法静态事实投影为 Qt 无关的资料库 DTO。
"""Qt-free monster and encounter catalog service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, unquote

from src.services.static_catalog_monster_display import (
    NAME_UNAVAILABLE,
    display_catalog_scalar,
    display_damage_type,
    display_fight_stage,
)
from src.services.static_catalog_feast_period_service import (
    StaticCatalogFeastPeriodMixin,
)
from src.services.static_catalog_monster_gameplay import (
    StaticCatalogMonsterGameplayProjector,
)
from src.services.static_catalog_monster_models import (
    CatalogDataset,
    CatalogDetail,
    CatalogEntry,
    CatalogFilter,
    CatalogPage,
    CatalogRelation,
    CatalogSection,
    CatalogValue,
)
from src.services.static_catalog_outer_realm_detail import (
    StaticCatalogOuterRealmDetailMixin,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_catalog_monster_queries import (
    StaticCatalogMonsterQueries,
)


OFFICIAL = "official_static"
FORMULA = "formula_profile"
DERIVED = "project_annotation"
UNAVAILABLE = "unavailable"
_MAINLAND_TIMEZONE = timezone(timedelta(hours=8))

_PLAY_MODE_LABELS = {
    "official_illustrated": "공식 도감 / 오픈 월드",
    "template_profile": "몬스터 템플릿과 레벨 프로필",
    "world_boss": "이상 현상 사냥 단일 Boss",
    "feast": "争锋赏宴",
    "outer_realm": "轨外之境",
    "clone": "재료 / 육성 던전",
    "high_risk": "고위험 의뢰",
}
_RELEASE_LABELS = {
    "current": "현재 회차",
    "next": "다음 회차",
    "historical": "종료됨",
    "scheduled": "오픈 예정",
    "unscheduled": "일정 미제공",
}
def _key(kind: str, *parts: object) -> str:
    encoded = [quote(str(part), safe="") for part in parts]
    return "|".join((kind, *encoded))


def _parse_key(key: str) -> tuple[str, tuple[str, ...]]:
    parts = str(key).split("|")
    if not parts or not parts[0]:
        raise ValueError("자료 키는 비워 둘 수 없습니다")
    return parts[0], tuple(unquote(part) for part in parts[1:])


def _text_state(value: object, fallback: str) -> tuple[str, bool]:
    text = str(value or "").strip()
    if not text or "\ufffd" in text:
        return fallback, False
    return text, True


class StaticCatalogMonsterService(
    StaticCatalogFeastPeriodMixin,
    StaticCatalogOuterRealmDetailMixin,
):
    """Builds display DTOs without changing or inferring static identity."""

    def __init__(
        self,
        queries: StaticCatalogMonsterQueries,
        *,
        terminology_service: StaticCatalogTerminologyService,
        mainland_now: datetime | None = None,
    ) -> None:
        self._queries = queries
        self._terminology_service = terminology_service
        self._gameplay = StaticCatalogMonsterGameplayProjector(terminology_service)
        now = mainland_now or datetime.now(_MAINLAND_TIMEZONE)
        if now.tzinfo is not None:
            now = now.astimezone(_MAINLAND_TIMEZONE).replace(tzinfo=None)
        self._mainland_now = now

    @classmethod
    def from_database(
        cls,
        database_path: str | Path | None = None,
        *,
        terminology_service: StaticCatalogTerminologyService,
        mainland_now: datetime | None = None,
    ) -> "StaticCatalogMonsterService":
        return cls(
            StaticCatalogMonsterQueries(database_path),
            terminology_service=terminology_service,
            mainland_now=mainland_now,
        )

    @property
    def terminology_service(self) -> StaticCatalogTerminologyService:
        """Return the frozen central terminology dependency for composition checks."""

        return self._terminology_service

    def close(self) -> None:
        self._queries.close()

    def dataset(self) -> CatalogDataset:
        metadata = self._queries.catalog_metadata()
        return CatalogDataset(
            dataset_id=str(metadata.get("dataset_id") or ""),
            importer_version=int(metadata.get("importer_version") or 0),
            built_at_utc=str(metadata.get("built_at_utc") or ""),
        )

    def list_witch_blessings(self) -> tuple[CatalogEntry, ...]:
        """Return the seven formal battle-wide blessing choices."""

        return self._gameplay.witch_entries(self._queries.list_divination_buffs())

    def profile_family_keys(self, monster_id: str) -> tuple[str, ...]:
        """Return profile keys from the same explicit formal ID family."""

        return tuple(
            _key("profile_monster", row["static_table"], row["monster_id"])
            for row in self._queries.profile_family_candidates(monster_id)
        )

    def clone_drop_status_counts(self) -> dict[str, int]:
        """Return gameplay-difficulty coverage for the v30 drop closure."""

        return self._queries.clone_drop_status_counts()

    def list_entries(self, filters: CatalogFilter = CatalogFilter()) -> CatalogPage:
        rows, total = self._queries.list_catalog_index(
            search=filters.search,
            domain=filters.domain,
            play_mode=filters.play_mode,
            region=filters.region,
            difficulty=filters.difficulty,
            version=filters.version,
            release_scope=filters.release_scope,
            as_of_mainland=self._mainland_now.strftime("%Y-%m-%dT%H:%M:%S"),
            limit=filters.page_size,
            offset=filters.offset,
        )
        items = tuple(self._entry_from_row(row) for row in rows)
        return CatalogPage(
            items=items,
            total=total,
            offset=max(0, filters.offset),
            page_size=max(1, min(filters.page_size, 200)),
            has_more=max(0, filters.offset) + len(items) < total,
        )

    def get_detail(self, key: str) -> CatalogDetail | None:
        kind, parts = _parse_key(key)
        if kind in {"manual_monster", "world_boss"} and len(parts) == 1:
            return self._manual_detail(parts[0], world_boss=kind == "world_boss")
        if kind == "profile_monster" and len(parts) == 2:
            return self._profile_detail(parts[0], parts[1])
        if kind == "feast" and len(parts) == 2:
            return self._feast_detail(parts[0], int(parts[1]))
        if kind == "feast_period" and len(parts) == 3:
            return self.get_feast_detail(parts[0], parts[1], int(parts[2]))
        if kind == "outer_realm" and len(parts) == 3:
            return self._outer_detail(parts[0], int(parts[1]), parts[2])
        if kind == "outer_member" and len(parts) == 6:
            return self._outer_member_detail(
                parts[0], int(parts[1]), parts[2], int(parts[3]),
                parts[4], int(parts[5]),
            )
        if kind == "clone" and len(parts) == 2:
            return self._clone_detail(parts[0], int(parts[1]))
        if kind == "high_risk" and len(parts) == 2:
            return self._high_risk_detail(parts[0], int(parts[1]))
        if kind == "witch_buff" and len(parts) == 1:
            row = self._queries.divination_buff(parts[0])
            return self._gameplay.witch_detail(row) if row is not None else None
        if kind == "outer_buff" and len(parts) == 1:
            row = self._queries.outer_realm_season_buff(parts[0])
            return self._gameplay.outer_buff_detail(row) if row is not None else None
        raise ValueError(f"지원하지 않는 몬스터/플레이 모드 자료 키: {key}")

    def _entry_from_row(self, row: dict[str, Any]) -> CatalogEntry:
        title, localization_available = _text_state(row.get("title_zh"), NAME_UNAVAILABLE)
        play_mode = str(row.get("play_mode") or "")
        region, region_available = _text_state(
            row.get("region"), _PLAY_MODE_LABELS.get(play_mode, play_mode)
        )
        release_state = str(row.get("release_state") or "")
        subtitle_parts = list(dict.fromkeys((
            _PLAY_MODE_LABELS.get(play_mode, play_mode), region,
        )))
        if row.get("difficulty"):
            subtitle_parts.append(f"난이도 / 층: {row['difficulty']}")
        if release_state:
            subtitle_parts.append(_RELEASE_LABELS.get(release_state, release_state))
        key_parts = [row.get("identity_1", "")]
        if row.get("identity_2") != "":
            key_parts.append(row["identity_2"])
        if row.get("identity_3") != "":
            key_parts.append(row["identity_3"])
        return CatalogEntry(
            key=_key(str(row["entity_kind"]), *key_parts),
            domain=str(row.get("domain") or ""),
            play_mode=play_mode,
            title=title,
            subtitle=" · ".join(part for part in subtitle_parts if part),
            primary_id=str(row.get("primary_id") or ""),
            secondary_id=str(row.get("secondary_id") or ""),
            resource_path=str(row.get("resource_path") or ""),
            release_state=release_state,
            localization_available=localization_available and region_available,
            secondary_label=(
                display_fight_stage(
                    self._terminology_service, row.get("secondary_id"),
                )
                if play_mode == "outer_realm" else ""
            ),
        )

    @staticmethod
    def _entry(
        key: str,
        *,
        domain: str,
        play_mode: str,
        title_value: object,
        fallback: str,
        subtitle: str,
        primary_id: str,
        secondary_id: str = "",
        resource_path: str = "",
        release_state: str = "",
        secondary_label: str = "",
    ) -> CatalogEntry:
        title, available = _text_state(title_value, fallback)
        return CatalogEntry(
            key=key,
            domain=domain,
            play_mode=play_mode,
            title=title,
            subtitle=subtitle,
            primary_id=primary_id,
            secondary_id=secondary_id,
            resource_path=resource_path,
            release_state=release_state,
            localization_available=available,
            secondary_label=secondary_label,
        )

    @staticmethod
    def _value(
        label: str,
        value: object,
        provenance: str = OFFICIAL,
        *,
        copyable: bool = False,
        note: str = "",
        display_label: str = "",
        display_value: str = "",
    ) -> CatalogValue:
        return CatalogValue(
            label=label,
            value=display_catalog_scalar(value),
            provenance=provenance,
            copyable=copyable,
            note=note,
            display_label=display_label,
            display_value=display_value,
        )

    @staticmethod
    def _localized_value(label: str, value: object) -> CatalogValue:
        text, available = _text_state(value, NAME_UNAVAILABLE)
        return CatalogValue(label, text, OFFICIAL if available else UNAVAILABLE)

    def _source_section(self, source: dict[str, Any] | None) -> CatalogSection:
        if not source:
            return CatalogSection(
                "출처 추적",
                (self._value("출처", None, UNAVAILABLE),),
                "현재 정규화 레코드에 사용 가능한 source_row_id가 없습니다.",
            )
        return CatalogSection(
            "출처 추적",
            (
                self._value("출처 파일", source.get("relative_path"), copyable=True),
                self._value("원본 행 키", source.get("row_key"), copyable=True),
                self._value("원본 행 SHA-256", source.get("content_sha256"), copyable=True),
                self._value("원본 파일 SHA-256", source.get("source_file_sha256"), copyable=True),
                self._value(
                    "원본 payload",
                    "사용 가능" if source.get("payload_available") else "사용 불가 (배포 라이브러리에서 생략됨)",
                    OFFICIAL if source.get("payload_available") else UNAVAILABLE,
                ),
            ),
        )

    def _combat_profile_section(
        self,
        profile: dict[str, Any] | None,
        *,
        title: str = "등가 공식 프로필",
        level: object = None,
        variant_kind: str = "",
        note: str = "공식 프로필이 몬스터의 정식 신원을 뜻하지는 않습니다.",
    ) -> CatalogSection:
        if not profile:
            return CatalogSection(
                title,
                (self._value("프로필", None, UNAVAILABLE),),
                note,
            )
        values = [
            self._value("몬스터 레벨", level, FORMULA),
            self._value("profile_set", profile.get("profile_set"), FORMULA, copyable=True),
            self._value("pack_id", profile.get("pack_id"), FORMULA, copyable=True),
            self._value("HP 기본", profile.get("health_base"), FORMULA),
            self._value("HP 보너스", profile.get("health_up"), FORMULA),
            self._value("HP 고정값", profile.get("health_add"), FORMULA),
            self._value("방어 기본", profile.get("defense_base"), FORMULA),
            self._value("방어 보너스", profile.get("defense_up"), FORMULA),
            self._value("방어 고정값", profile.get("defense_add"), FORMULA),
            self._value("방어 무시", profile.get("defense_ignore"), FORMULA),
            self._value("브레이크 상한", profile.get("topple_limit"), FORMULA),
            self._value("브레이크 회복", profile.get("topple_reduce_reset"), FORMULA),
            self._value(
                "공격 티어",
                "사용 불가 (schema v30에 공격 속성 필드 없음)",
                UNAVAILABLE,
                display_value="데이터 없음",
            ),
        ]
        if variant_kind:
            values.append(self._value("프로필 티어 유형", variant_kind, FORMULA))
        for resistance in profile.get("resistances", ()):
            damage_type = str(resistance["damage_type"])
            values.append(self._value(
                f"抗性 {damage_type}",
                f"{display_catalog_scalar(resistance.get('resistance_base'))} / "
                f"면역 {display_catalog_scalar(resistance.get('immunity'))}",
                FORMULA,
                display_label=display_damage_type(
                    self._terminology_service, damage_type,
                ),
            ))
        return CatalogSection(title, tuple(values), note)

    def _manual_detail(self, manual_id: str, *, world_boss: bool) -> CatalogDetail | None:
        row = self._queries.manual_monster(manual_id)
        if row is None:
            return None
        mode = "world_boss" if world_boss else "official_illustrated"
        entry = self._entry(
            _key("world_boss" if world_boss else "manual_monster", manual_id),
            domain="encounter" if world_boss else "monster",
            play_mode=mode,
            title_value=row.get("name_zh"),
            fallback=NAME_UNAVAILABLE,
            subtitle=_PLAY_MODE_LABELS[mode],
            primary_id=manual_id,
            secondary_id=str(row.get("enemy_type") or ""),
            resource_path=str(row.get("world_image_path") or row.get("image_path") or ""),
        )
        values = (
            self._value("정식 도감 ID", manual_id, copyable=True),
            self._localized_value("중국어 이름", row.get("name_zh")),
            self._value("적 유형", row.get("enemy_type")),
            self._localized_value("지역 / 위치", row.get("place_zh")),
            self._value("추적 유형", row.get("trace_type")),
        )
        sections = [CatalogSection("정식 신원", values)]
        if row.get("aliases"):
            sections.append(CatalogSection("정식 별칭", tuple(
                self._value(
                    str(alias.get("alias_kind") or "alias"),
                    alias.get("alias_value"),
                    copyable=True,
                )
                for alias in row["aliases"]
            )))
        relations: list[CatalogRelation] = []
        bindings = [
            binding for binding in row.get("bindings", ())
            if not world_boss or binding.get("binding_kind") == "world_boss_id"
        ]
        for binding in bindings:
            sections.append(CatalogSection(
                f"템플릿 바인딩 · {binding['binding_kind']}",
                (
                    self._value("템플릿 ID", binding.get("monster_template_name"), copyable=True),
                    self._value("바인딩 유형", binding.get("binding_kind")),
                ),
                "정적 라이브러리의 명시적 바인딩이며, 중국어 이름으로 매칭한 것이 아닙니다.",
            ))
            for profile in binding.get("profiles", ()):
                relations.append(CatalogRelation(
                    f"템플릿 프로필 보기: {profile['static_table']}",
                    _key("profile_monster", profile["static_table"], profile["monster_id"]),
                    "explicit_template_binding",
                    "monster_template_binding의 정식 템플릿 이름으로 연결됩니다.",
                ))
        if not world_boss and row.get("enemy_type") == "WeeklyBoss":
            relations.append(CatalogRelation(
                "이상 현상 사냥 플레이 모드 항목 보기",
                _key("world_boss", manual_id),
                "official_enemy_type",
            ))
        sections.append(self._source_section(row.get("source")))
        notices = () if entry.localization_available else (
            "이 레코드의 중국어 텍스트는 배포 정적 라이브러리에 이미 대체 문자가 포함되어 있으며, 이 페이지에서는 수정하거나 추측해 보완하지 않습니다.",
        )
        return CatalogDetail(entry, tuple(sections), unique_relations(relations), notices)

    def _profile_detail(self, static_table: str, monster_id: str) -> CatalogDetail | None:
        row = self._queries.profile_monster(static_table, monster_id)
        if row is None:
            return None
        bindings = row.get("manual_bindings", ())
        family_evidence_row = None
        if not bindings:
            family_evidence = self._queries.profile_family_display_evidence(
                monster_id
            )
            names = {
                str(candidate.get("name_zh") or "").strip()
                for candidate in family_evidence
                if _text_state(candidate.get("name_zh"), "")[1]
            }
            if len(names) == 1:
                family_evidence_row = next(
                    candidate for candidate in family_evidence
                    if str(candidate.get("name_zh") or "").strip() in names
                )
        title_value = (
            bindings[0].get("name_zh")
            if bindings else (
                family_evidence_row.get("name_zh")
                if family_evidence_row else None
            )
        )
        entry = self._entry(
            _key("profile_monster", static_table, monster_id),
            domain="monster",
            play_mode="template_profile",
            title_value=title_value,
            fallback=NAME_UNAVAILABLE,
            subtitle=_PLAY_MODE_LABELS["template_profile"],
            primary_id=monster_id,
            secondary_id=static_table,
        )
        sections = [CatalogSection("정식 템플릿 레코드", (
            self._value("정적 테이블", static_table, copyable=True),
            self._value("몬스터 템플릿 ID", monster_id, copyable=True),
            self._value("기본 레벨", row.get("monster_level")),
            self._value("online_ratio_id", row.get("online_ratio_id"), copyable=True),
        ))]
        default_profile = None
        if row.get("default_profile_set") and row.get("default_pack_id"):
            default_profile = self._queries.combat_profile(
                row["default_profile_set"], row["default_pack_id"]
            )
        sections.append(self._combat_profile_section(
            default_profile,
            level=row.get("monster_level"),
            title="기본 등가 공식 프로필",
        ))
        for variant in row.get("variants", ()):
            sections.append(self._combat_profile_section(
                self._queries.combat_profile(variant["profile_set"], variant["pack_id"]),
                level=variant.get("threshold_level"),
                variant_kind=str(variant.get("variant_kind") or ""),
                title=f"레벨 프로필 · 레벨 {variant['threshold_level']}",
            ))
        relations: list[CatalogRelation] = [CatalogRelation(
            f"정식 도감: {binding['monster_manual_id']}",
            _key("manual_monster", binding["monster_manual_id"]),
            "explicit_template_binding",
            f"바인딩 유형: {binding['binding_kind']}",
        ) for binding in bindings]
        for reference in self._queries.template_encounter_references(monster_id):
            kind = str(reference["entity_kind"])
            parts = [reference["identity_1"]]
            if reference.get("identity_2") != "":
                parts.append(reference["identity_2"])
            if reference.get("identity_3") != "":
                parts.append(reference["identity_3"])
            title, _available = _text_state(reference.get("title_zh"), kind)
            relations.append(CatalogRelation(
                f"플레이 모드 참조: {_PLAY_MODE_LABELS.get(kind, kind)} · {title}",
                _key(kind, *parts),
                str(reference.get("relation_kind") or "exact_official_reference"),
                "정식 템플릿 ID 또는 Unreal 클래스 경로 객체 이름에서 가져오며, 중국어 이름은 사용하지 않습니다.",
            ))
        sections.append(self._source_section(row.get("source")))
        notice = (
            "제목은 같은 정식 mon/boss 숫자 계열의 사용 가능한 모든 이름 근거가 내는 유일한 결과를 사용합니다;"
            "이는 보조 표시일 뿐이며, 현재 템플릿과 도감 사이의 신원 관계를 설정하지 않습니다."
            if family_evidence_row else (
                "몬스터 템플릿 ID는 정식 정적 필드입니다. 명시적 monster_template_binding "
                "바인딩만이 도감 ID와의 신원 관계를 설정합니다. 수치 프로필을 공유한다고 신원이 증명되지는 않습니다."
            )
        )
        return CatalogDetail(entry, tuple(sections), unique_relations(relations), (notice,))

    def _release_state(self, starts: object, ends: object) -> str:
        if not starts or not ends:
            return "unscheduled"
        start = datetime.fromisoformat(str(starts))
        end = datetime.fromisoformat(str(ends))
        if start <= self._mainland_now <= end:
            return "current"
        if end < self._mainland_now:
            return "historical"
        future_start = self._queries.next_outer_realm_start(
            self._mainland_now.strftime("%Y-%m-%dT%H:%M:%S")
        )
        return "next" if future_start == str(starts) else "scheduled"

    def _clone_detail(self, clone_id: str, ordinal: int) -> CatalogDetail | None:
        row = self._queries.clone_encounter(clone_id, ordinal)
        if row is None:
            return None
        entry = self._entry(
            _key("clone", clone_id, ordinal), domain="encounter", play_mode="clone",
            title_value=row.get("name_zh"), fallback=NAME_UNAVAILABLE,
            subtitle=f"{_PLAY_MODE_LABELS['clone']} · 난이도 {ordinal}",
            primary_id=clone_id, secondary_id=str(row.get("clone_type") or ""),
        )
        sections = [CatalogSection("정식 던전 구성", (
            self._value("던전 ID", clone_id, copyable=True),
            self._value(
                "던전 유형", row.get("clone_type"),
                OFFICIAL if row.get("clone_type") else UNAVAILABLE,
                copyable=bool(row.get("clone_type")),
            ),
            self._localized_value("분류", row.get("category_name_zh")),
            self._value(
                "모험 수첩 표시",
                bool(row.get("show_in_adventure")) if row.get("show_in_adventure") is not None else None,
                OFFICIAL if row.get("show_in_adventure") is not None else UNAVAILABLE,
            ),
            self._value(
                "크로스 씬",
                bool(row.get("cross_scene")) if row.get("cross_scene") is not None else None,
                OFFICIAL if row.get("cross_scene") is not None else UNAVAILABLE,
            ),
            self._value("난이도 번호", ordinal),
            self._value("난이도 등급", row.get("difficulty_level")),
            self._value("팀 레벨", row.get("team_level")),
            self._value("체력", row.get("stamina_cost")),
            self._value("몬스터 스폰 구성 ID", row.get("spawn_id"), copyable=True),
            self._value("처치 제한 시간", row.get("kill_monster_time_limit")),
        ))]
        sections.append(self._gameplay.drop_section(row.get("drop_projection")))
        relations: list[CatalogRelation] = []
        members = row.get("members", ())
        if not members:
            sections.append(CatalogSection("스폰 슬롯", (
                self._value("정식 몬스터 풀", None, UNAVAILABLE),
            )))
        for index, member in enumerate(members, 1):
            sections.append(CatalogSection(f"스폰 슬롯 {index}", (
                self._value("웨이브", member.get("wave_ordinal")),
                self._value("템플릿 경로", member.get("monster_template_path"), copyable=True),
                self._value("템플릿 ID", member.get("monster_template_name"), copyable=True),
                self._value("수량", member.get("monster_count")),
            )))
            profiles = self._queries.template_profile_candidates(
                member.get("monster_template_name") or ""
            )
            for candidate in profiles:
                relations.append(CatalogRelation(
                    f"스폰 슬롯 {index} 템플릿 프로필: {candidate['static_table']}",
                    _key("profile_monster", candidate["static_table"], candidate["monster_id"]),
                    "exact_official_template_id",
                ))
        sections.append(self._source_section(row.get("source")))
        return CatalogDetail(entry, tuple(sections), unique_relations(relations))

    def _high_risk_detail(self, commission_id: str, difficulty: int) -> CatalogDetail | None:
        row = self._queries.high_risk_encounter(commission_id, difficulty)
        if row is None:
            return None
        entry = self._entry(
            _key("high_risk", commission_id, difficulty), domain="encounter",
            play_mode="high_risk", title_value=row.get("name_zh"), fallback=NAME_UNAVAILABLE,
            subtitle=f"고위험 의뢰 · 난이도 {difficulty}", primary_id=commission_id,
            secondary_id=str(row.get("monster_pool_id") or ""),
        )
        sections = [CatalogSection("정식 의뢰 구성", (
            self._value("의뢰 ID", commission_id, copyable=True),
            self._localized_value("의뢰 이름", row.get("name_zh")),
            self._value("난이도", difficulty),
            self._value("추천 캐릭터 레벨", row.get("recommended_character_level")),
            self._value("씬 ID", row.get("scene_data_id"), copyable=True),
            self._value("난이도별 몬스터 풀", row.get("monster_pool_id"), copyable=True),
            self._value("공용 폴백 풀", row.get("fallback_monster_pool_id"), copyable=True),
        ))]
        relations: list[CatalogRelation] = []
        members = row.get("members", ())
        if not row.get("monster_pool_id"):
            sections.append(CatalogSection("난이도별 정식 몬스터 풀", (
                self._value("몬스터 풀", "사용 불가 (이 난이도에는 공용 폴백 풀만 있음)", UNAVAILABLE),
            )))
        for index, member in enumerate(members, 1):
            sections.append(CatalogSection(f"몬스터 풀 멤버 {index}", (
                self._value("클래스 경로", member.get("monster_class_path"), copyable=True),
                self._value("템플릿 ID", member.get("monster_template_name"), copyable=True),
                self._value("수량", member.get("monster_count")),
                self._value("설정 레벨", member.get("configured_monster_level")),
                self._value("속성 ID", member.get("attribute_id"), copyable=True),
            )))
            for candidate in self._queries.template_profile_candidates(
                member.get("monster_template_name") or ""
            ):
                relations.append(CatalogRelation(
                    f"몬스터 풀 멤버 {index} 템플릿 프로필: {candidate['static_table']}",
                    _key("profile_monster", candidate["static_table"], candidate["monster_id"]),
                    "exact_official_template_id",
                ))
        sections.append(self._source_section(row.get("source")))
        return CatalogDetail(entry, tuple(sections), unique_relations(relations))

    def _path_relations(self, class_path: object) -> list[CatalogRelation]:
        path = str(class_path or "").strip()
        if not path or "." not in path:
            return []
        object_name = path.rsplit(".", 1)[-1]
        if object_name.endswith("_C"):
            object_name = object_name[:-2]
        relations = []
        for candidate in self._queries.template_profile_candidates(object_name):
            relations.append(CatalogRelation(
                f"클래스 경로 객체의 템플릿 프로필: {candidate['static_table']}",
                _key("profile_monster", candidate["static_table"], candidate["monster_id"]),
                "exact_class_path_object",
                "정식 클래스 경로의 객체 이름으로만 정확히 연결하며, 중국어 이름은 사용하지 않습니다.",
            ))
        return relations


def provenance_label(provenance: str) -> str:
    return {
        OFFICIAL: "공식 정적 사실",
        FORMULA: "등가 공식 프로필",
        DERIVED: "프로젝트 파생 / 주석",
        UNAVAILABLE: "不可用",
    }.get(provenance, provenance)


def play_mode_choices() -> tuple[tuple[str, str], ...]:
    return (("all", "모든 플레이 모드"), *tuple(_PLAY_MODE_LABELS.items()))


def release_scope_choices() -> tuple[tuple[str, str], ...]:
    return (
        ("all", "전체 기수"),
        ("current_next", "현재 + 다음 회차"),
        ("current", "현재 회차만"),
        ("next", "다음 회차만"),
    )


def unique_relations(relations: Iterable[CatalogRelation]) -> tuple[CatalogRelation, ...]:
    unique: dict[tuple[str, str], CatalogRelation] = {}
    for relation in relations:
        unique.setdefault((relation.target_key, relation.relation_kind), relation)
    return tuple(unique.values())
