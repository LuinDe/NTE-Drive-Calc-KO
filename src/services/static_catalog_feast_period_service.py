# 游戏资料库争锋赏宴活动期与挑战的 Qt 无关投影。
"""Versioned Feast period projection for the monster catalog service."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from src.services.static_catalog_feast_periods import (
    feast_period,
    feast_periods,
    historical_boss_name,
)
from src.services.static_catalog_monster_display import NAME_UNAVAILABLE
from src.services.static_catalog_monster_feast import (
    apply_feast_options,
    build_feast_setup,
    selected_feast_options,
)
from src.services.static_catalog_monster_models import (
    CatalogDetail,
    CatalogRelation,
    CatalogSection,
    FeastPeriod,
    FeastSetup,
)


DERIVED = "project_annotation"


def _key(kind: str, *parts: object) -> str:
    encoded = [quote(str(part), safe="") for part in parts]
    return "|".join((kind, *encoded))


def _text(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    return text if text and "\ufffd" not in text else fallback


class StaticCatalogFeastPeriodMixin:
    """Own versioned period membership separately from formal challenge rows."""

    def list_feast_periods(self) -> tuple[FeastPeriod, ...]:
        return feast_periods(self._mainland_now)

    def get_feast_setup(
        self, period_id: str, stage_id: str,
    ) -> FeastSetup | None:
        period = feast_period(period_id, self._mainland_now)
        if period is None or stage_id not in period.challenge_ids:
            return None
        row = self._queries.feast_setup(stage_id)
        condition_note = ""
        if row is None:
            row = self._queries.historical_feast_setup(stage_id)
            if row is None:
                return None
            boss_name = historical_boss_name(stage_id)
            for difficulty in row.get("difficulties", ()):
                difficulty["boss_name_zh"] = boss_name
            condition_note = "지난 기의 도전 조건 구성원 관계가 보존되지 않아 현재 기의 조건을 적용하지 않습니다."
        return build_feast_setup(
            row,
            period_id=period.period_id,
            period_label=period.display_label,
            period_state=period.release_state,
            schedule_label=period.schedule_label,
            challenge_ordinal=period.challenge_ids.index(stage_id) + 1,
            condition_note=condition_note,
            project_option=self._gameplay.feast_option,
        )

    def get_feast_detail(
        self,
        period_id: str,
        stage_id: str,
        difficulty_id: int,
        *,
        selected_option_ids: tuple[str, ...] = (),
    ) -> CatalogDetail | None:
        period = feast_period(period_id, self._mainland_now)
        if period is None or stage_id not in period.challenge_ids:
            return None
        row = self._queries.feast_encounter(stage_id, difficulty_id)
        if row is None:
            row = self._queries.historical_feast_encounter(
                stage_id, difficulty_id
            )
            if row is None:
                return None
            row["boss_name_zh"] = historical_boss_name(stage_id)
        return self._build_feast_detail(
            row,
            selected_option_ids=selected_option_ids,
            period=period,
            challenge_ordinal=period.challenge_ids.index(stage_id) + 1,
        )

    def _feast_detail(
        self,
        stage_id: str,
        difficulty_id: int,
        *,
        selected_option_ids: tuple[str, ...] = (),
    ) -> CatalogDetail | None:
        row = self._queries.feast_encounter(stage_id, difficulty_id)
        if row is None:
            return None
        return self._build_feast_detail(
            row,
            selected_option_ids=selected_option_ids,
        )

    def _build_feast_detail(
        self,
        row: dict[str, Any],
        *,
        selected_option_ids: tuple[str, ...],
        period: FeastPeriod | None = None,
        challenge_ordinal: int = 0,
    ) -> CatalogDetail:
        stage_id = str(row.get("stage_id") or "")
        difficulty_id = int(row.get("difficulty_id") or 0)
        selected = selected_feast_options(row.get("options", []), selected_option_ids)
        profile = apply_feast_options(row.get("profile"), selected)
        difficulty_name = _text(row.get("difficulty_name_zh"), NAME_UNAVAILABLE)
        entry_key = (
            _key("feast_period", period.period_id, stage_id, difficulty_id)
            if period is not None
            else _key("feast", stage_id, difficulty_id)
        )
        subtitle = (
            f"쟁봉 연회 · {period.display_label} · {difficulty_name}"
            if period is not None
            else f"쟁봉 연회 · {difficulty_name}"
        )
        entry = self._entry(
            entry_key, domain="encounter", play_mode="feast",
            title_value=row.get("name_zh"), fallback=NAME_UNAVAILABLE,
            subtitle=subtitle, primary_id=stage_id,
            secondary_id=str(row.get("boss_monster_id") or ""),
            resource_path=str(row.get("boss_icon_path") or ""),
        )
        sections = []
        if period is not None:
            sections.append(CatalogSection("이벤트 기간", (
                self._value("이벤트 기간", period.display_label, DERIVED),
                self._value("도전 순서", challenge_ordinal, DERIVED),
                self._value("중국 서버 일정", period.schedule_label, DERIVED),
                self._value(
                    "일정 근거", period.evidence_label, DERIVED,
                    note=period.evidence_url,
                ),
            ), (
                "이벤트 기간과 구성원 순서는 공개 일정과 같은 시기의 실기 근거를 바탕으로 한 프로젝트 주석"
                "이며, 적 수치는 여전히 배포 정적 리소스에서 가져옵니다."
            )))
        sections.append(CatalogSection("정식 콘텐츠 설정", (
            self._value("도전 대상 ID", stage_id, copyable=True),
            self._localized_value("도전 이름", row.get("name_zh")),
            self._value("Boss 템플릿 ID", row.get("boss_monster_id"), copyable=True),
            self._localized_value("Boss 중국어 이름", row.get("boss_name_zh")),
            self._localized_value("난이도", row.get("difficulty_name_zh")),
            self._value("몬스터 레벨", row.get("monster_level")),
            self._value("기본 점수", row.get("base_score")),
            self._value("점수 배율", row.get("score_rate")),
            self._value("특수 고난도", bool(row.get("special_high_difficulty"))),
        )))
        sections.append(self._combat_profile_section(
            profile, level=row.get("monster_level"), title="현재 선택 프로필"
        ))
        if selected:
            option_values = []
            for option in selected:
                category = _text(
                    option.get("category_name_zh"), NAME_UNAVAILABLE
                )
                option_values.append(self._gameplay.feast_option(category, option))
            sections.append(CatalogSection(
                "선택한 도전 조건",
                tuple(option_values),
                (
                    "공격력 상승을 선택했습니다. 현재 정식 프로필에는 적 공격력 수치가 없습니다."
                    if any(
                        option.get("effect_kind") == "attack_up"
                        for option in selected
                    )
                    else ""
                ),
            ))
        relations = tuple(dict.fromkeys(
            CatalogRelation(
                f"Boss 템플릿 보기: {candidate['static_table']}",
                _key(
                    "profile_monster",
                    candidate["static_table"],
                    candidate["monster_id"],
                ),
                "exact_official_template_id",
            )
            for candidate in self._queries.template_profile_candidates(
                row["boss_monster_id"]
            )
        ))
        sections.append(self._source_section(row.get("source")))
        return CatalogDetail(entry, tuple(sections), relations)
