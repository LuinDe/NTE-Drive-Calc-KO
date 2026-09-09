# 游戏资料库争锋赏宴按活动期浏览的页面行为。
"""Feast period and challenge browsing mixed into the monster archive page."""

from __future__ import annotations

from pathlib import Path

from src.features.static_catalog.domain_pages.monster_browse_models import (
    PLAY_LABELS,
    BrowseCard,
    BrowseSection,
    BrowseState,
)
from src.services.static_catalog_monster_models import FeastPeriod, FeastSetup


class FeastCatalogBrowserMixin:
    """Render period cards first, then the period's ordered challenges."""

    def _feast_state(self) -> BrowseState:
        periods = self._controller.feast_periods()
        active = tuple(
            self._feast_period_card(period) for period in periods
            if period.release_state in {"current", "next", "scheduled"}
        )
        history = tuple(
            self._feast_period_card(period) for period in periods
            if period.release_state == "historical"
        )
        sections = []
        if active:
            sections.append(BrowseSection(
                "현재·예정",
                "중국 서버 공개 일정을 기준으로 판정합니다. 이벤트 기간에 들어간 뒤 도전 대상을 선택하세요.",
                active,
            ))
        if history:
            sections.append(BrowseSection(
                "지난 기",
                "같은 기의 정식 도전 구성원을 유지하며, 수치는 배포 리소스에서 아직 검증 가능한 구성을 읽습니다.",
                history,
            ))
        return BrowseState(
            PLAY_LABELS["feast"],
            "먼저 이벤트 기를 선택한 뒤 해당 기의 정식 순서대로 도전, 난이도, 적 프로필을 확인합니다.",
            tuple(sections),
        )

    def _feast_period_card(self, period: FeastPeriod) -> BrowseCard:
        setup = self._controller.feast_setup(
            period.period_id, period.challenge_ids[0]
        )
        state_label = {
            "current": "현재 기", "next": "예정", "scheduled": "예정",
            "historical": "지난 기",
        }
        return BrowseCard(
            period.display_label,
            f"도전 {len(period.challenge_ids)}개 · {period.schedule_label}",
            state_label.get(period.release_state, period.release_state),
            self._feast_setup_icon(setup),
            lambda checked=False, value=period: self._open_feast_period(value),
            formal_id=period.period_id,
            category=state_label.get(period.release_state, ""),
            period=period.display_label,
        )

    def _open_feast_period(self, period: FeastPeriod) -> None:
        cards = []
        for stage_id in period.challenge_ids:
            setup = self._controller.feast_setup(period.period_id, stage_id)
            if setup is None:
                continue
            cards.append(BrowseCard(
                f"도전 {setup.challenge_ordinal} · {setup.title}",
                f"{setup.boss_name} · 난이도 {len(setup.difficulties)}개",
                "도전 대상",
                self._feast_setup_icon(setup),
                lambda checked=False, value=setup: self._open_feast_stage(value),
                formal_id=stage_id,
                period=period.display_label,
            ))
        self._show_state(BrowseState(
            f"쟁봉 연회 · {period.display_label}",
            f"{period.schedule_label} · 도전을 선택하면 난이도, 조건, 적 프로필을 확인할 수 있습니다.",
            (BrowseSection(
                "도전 대상", f"총 {len(cards)}개, 해당 기의 정식 순서대로 표시합니다.",
                tuple(cards),
            ),),
        ), push=True)

    def _feast_setup_icon(self, setup: FeastSetup | None) -> Path | None:
        if setup is None:
            return None
        detail = self._controller.feast_detail(
            setup.period_id, setup.stage_id, setup.default_difficulty_id, ()
        )
        return self._formal_icon(detail)

    def _open_feast_stage(self, setup: FeastSetup) -> None:
        self.feast_view.set_stage(
            setup,
            icon=self._feast_setup_icon(setup),
            loader=self._controller.feast_detail,
            blessings=self._controller.witch_blessings(),
            blessing_loader=self._controller.detail,
        )
        self.stack.setCurrentWidget(self.feast_view)
        self._catalog_navigation_listener()
