# 游戏资料库怪物与玩法的独立游戏化页面与公开接线工厂。
"""Card-based monster and encounter archive backed only by public DTOs."""

from __future__ import annotations

from src.i18n_display import ko_contains as _ko_contains

from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.monster_browse_models import (
    PLAY_COPY as _PLAY_COPY,
    PLAY_LABELS as _PLAY_LABELS,
    BrowseCard,
    BrowseSection,
    BrowseState,
    group_entries as _group,
    home_badge as _home_badge,
    key_parts as _key_parts,
    object_name as _object_name,
    period_label as _period_label,
    profile_parts as _profile_parts,
)
from src.features.static_catalog.domain_pages.monster_detail_view import (
    MonsterContext,
    MonsterDetailView,
)
from src.features.static_catalog.domain_pages.monster_feast_view import (
    FeastEncounterView,
)
from src.features.static_catalog.domain_pages.monster_feast_browser import (
    FeastCatalogBrowserMixin,
)
from src.features.static_catalog.domain_pages.monster_icon_resolver import (
    MonsterIconResolver,
)
from src.features.static_catalog.domain_pages.monster_page_controller import (
    MonsterCatalogPageController,
)
from src.features.static_catalog.domain_pages.monster_widgets import (
    ArchiveCard,
    clear_layout,
    section_title,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.static_catalog_monster_service import (
    CatalogDetail,
    CatalogEntry,
    StaticCatalogMonsterService,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)

class MonsterCatalogPage(FeastCatalogBrowserMixin, QWidget):
    """Independent card archive; shared catalog composition owns final wiring."""

    def __init__(
        self,
        *,
        controller: MonsterCatalogPageController,
        asset_catalog: GameUiAssetCatalog,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("monsterCatalogPage")
        self.setStyleSheet(themed_style("QWidget#monsterCatalogPage{background:#0d1117;}"))
        self._controller = controller
        self._history: list[BrowseState] = []
        self._active_state: BrowseState | None = None
        self._expanded_sections: set[str] = set()
        self._category_filter = ""
        self._updating_filters = False
        self._browser_layout_bucket = ""
        self._home_layout_bucket = ""
        self._catalog_navigation_listener: Callable[[], None] = lambda: None
        self.play_group_cards: list[ArchiveCard] = []
        self._icon_resolver = MonsterIconResolver(controller, asset_catalog)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget(self)
        self.home = self._build_home()
        self.browser = self._build_browser()
        self.detail_view = MonsterDetailView(self)
        self.feast_view = FeastEncounterView(self)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.browser)
        self.stack.addWidget(self.detail_view)
        self.stack.addWidget(self.feast_view)
        root.addWidget(self.stack)

    def _build_home(self) -> QWidget:
        page = QWidget(self)
        root = QVBoxLayout(page)
        root.setContentsMargins(4, 2, 4, 8)
        hero = QFrame(page)
        hero.setObjectName("monsterGalleryHero")
        hero.setStyleSheet(themed_style(
            "QFrame#monsterGalleryHero{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #10243f,stop:.70 #161b22,stop:1 #23170b);"
            "border:0;border-radius:18px;}"
        ))
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(22, 17, 22, 17)
        copy = QVBoxLayout()
        eyebrow = QLabel("적·도전 정보", hero)
        eyebrow.setStyleSheet(themed_style("color:#58a6ff;font-size:10px;font-weight:900"))
        title = QLabel("몬스터 및 콘텐츠", hero)
        title.setStyleSheet(themed_style("color:#f0f6fc;font-size:28px;font-weight:900"))
        subtitle = QLabel("콘텐츠에서 기수, 층, 하프, 스폰 슬롯으로 들어가 현재 선택에 따른 몬스터 프로필을 확인합니다.", hero)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        hero_layout.addLayout(copy, 1)
        root.addWidget(hero)
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(themed_style(
            "QScrollArea{background:#0d1117;border:0;}"
            "QScrollArea>QWidget>QWidget{background:#0d1117;}"
        ))
        host = QWidget(scroll)
        self.home_grid = QGridLayout(host)
        self.home_grid.setContentsMargins(4, 15, 12, 20)
        self.home_grid.setSpacing(13)
        self.home_grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        for index, mode in enumerate(_PLAY_LABELS):
            card = ArchiveCard(BrowseCard(
                _PLAY_LABELS[mode], _PLAY_COPY[mode], self._home_badge(mode),
                self._mode_icon(mode), lambda checked=False, key=mode: self.open_mode(key),
                formal_id=mode,
            ), host)
            self.play_group_cards.append(card)
        self._layout_home(force=True)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)
        return page

    def _build_browser(self) -> QWidget:
        page = QWidget(self)
        root = QVBoxLayout(page)
        root.setContentsMargins(4, 2, 4, 8)
        top = QHBoxLayout()
        self.browser_title = QLabel(page)
        self.browser_title.setStyleSheet(themed_style("color:#f0f6fc;font-size:22px;font-weight:900"))
        top.addWidget(self.browser_title)
        top.addStretch(1)
        root.addLayout(top)
        self.browser_subtitle = QLabel(page)
        self.browser_subtitle.setWordWrap(True)
        self.browser_subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
        root.addWidget(self.browser_subtitle)
        search_row = QHBoxLayout()
        self.browser_search = QLineEdit(page)
        self.browser_search.setClearButtonEnabled(True)
        self.browser_search.setPlaceholderText("몬스터 또는 콘텐츠 검색")
        self.browser_search.textChanged.connect(self._render_browser_state)
        search_row.addWidget(self.browser_search, 1)
        self.more_filters_button = QToolButton(page)
        self.more_filters_button.setText("추가 필터")
        self.more_filters_button.setCheckable(True)
        search_row.addWidget(self.more_filters_button)
        root.addLayout(search_row)
        self.category_host = QWidget(page)
        self.category_layout = QHBoxLayout(self.category_host)
        self.category_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.category_host)
        self.more_filters = QFrame(page)
        self.more_filters.setObjectName("monsterMoreFilters")
        self.more_filters.setStyleSheet(themed_style(
            "QFrame#monsterMoreFilters{background:#161b22;border:0;"
            "border-radius:10px;}"
        ))
        filters_layout = QHBoxLayout(self.more_filters)
        filters_layout.setContentsMargins(10, 8, 10, 8)
        self.difficulty_filter = self._filter_combo("전체 난이도", self.more_filters)
        self.region_filter = self._filter_combo("전체 지역", self.more_filters)
        self.period_filter = self._filter_combo("전체 기수", self.more_filters)
        for combo in (self.difficulty_filter, self.region_filter, self.period_filter):
            combo.currentIndexChanged.connect(self._render_browser_state)
            filters_layout.addWidget(combo)
        filters_layout.addStretch(1)
        self.more_filters.setVisible(False)
        self.more_filters_button.toggled.connect(self.more_filters.setVisible)
        root.addWidget(self.more_filters)
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(themed_style(
            "QScrollArea{background:#0d1117;border:0;}"
            "QScrollArea>QWidget>QWidget{background:#0d1117;}"
        ))
        self.browser_host = QWidget(scroll)
        self.browser_body = QVBoxLayout(self.browser_host)
        self.browser_body.setContentsMargins(0, 6, 12, 20)
        self.browser_body.setSpacing(11)
        scroll.setWidget(self.browser_host)
        root.addWidget(scroll, 1)
        return page

    @staticmethod
    def _filter_combo(default_text: str, parent: QWidget) -> QComboBox:
        combo = QComboBox(parent)
        combo.addItem(default_text, "")
        combo.setProperty("defaultText", default_text)
        return combo

    def open_mode(self, mode: str) -> None:
        self._history.clear()
        if mode == "outer_realm":
            state = self._outer_state()
        elif mode == "feast":
            state = self._feast_state()
        else:
            entries = self._controller.entries_for(mode)
            grouped = _group(entries, lambda row: row.primary_id)
            cards = tuple(self._entity_card(mode, key, rows) for key, rows in grouped.items())
            state = BrowseState(
                _PLAY_LABELS[mode], _PLAY_COPY[mode],
                (BrowseSection(
                    _PLAY_LABELS[mode], self._home_badge(mode), cards,
                ),),
            )
        self._show_state(state, push=True)

    def open_record(self, record_id: str) -> bool:
        """Open a typed relation without exposing controller internals."""

        requested = str(record_id)
        detail = self._controller.detail(requested)
        if detail is not None:
            self.open_detail(detail)
            return True
        for mode in _PLAY_LABELS:
            matches = tuple(
                entry for entry in self._controller.entries_for(mode)
                if entry.key == requested or entry.primary_id == requested
            )
            if not matches:
                continue
            if len(matches) == 1:
                self._open_encounter(matches[0])
            else:
                self._open_tiers(mode, matches)
            return True
        return False

    def _outer_state(self) -> BrowseState:
        rotations = self._controller.outer_rotations()
        active = tuple(
            self._rotation_card(row) for row in rotations
            if row.release_state in {"current", "next", "scheduled"}
        )
        history = tuple(
            self._rotation_card(row) for row in rotations
            if row.release_state == "historical"
        )
        unscheduled = tuple(
            self._rotation_card(row) for row in rotations
            if row.release_state == "unscheduled"
        )
        sections = []
        if active:
            sections.append(BrowseSection(
                "현재·예정",
                "왼쪽은 현재 진행 중인 기, 오른쪽은 아직 열리지 않은 예정 기입니다.",
                active,
            ))
        if history:
            sections.append(BrowseSection(
                "지난 기", "최근 종료된 기를 우선 표시하며, 펼쳐서 전체를 볼 수 있습니다.", history,
                initial_limit=3,
            ))
        if unscheduled:
            sections.append(BrowseSection(
                "일정 미제공",
                "정식 스테이지와 몬스터 데이터는 사용할 수 있습니다. 오픈 시간만 없으므로 현재 또는 지난 기 상태를 추정하지 않습니다.",
                unscheduled,
            ))
        return BrowseState(_PLAY_LABELS["outer_realm"], _PLAY_COPY["outer_realm"], tuple(sections))

    def _rotation_card(self, representative: CatalogEntry) -> BrowseCard:
        rows = tuple(
            row for row in self._controller.entries_for("outer_realm")
            if row.primary_id == representative.primary_id
        )
        state_label = {
            "current": "현재 기", "next": "예정", "scheduled": "예정",
            "historical": "지난 기", "unscheduled": "일정 없음",
        }
        ordinal = representative.primary_id.rsplit("_", 1)[-1]
        title = f"{ordinal}기" if ordinal.isdigit() else representative.primary_id
        return BrowseCard(
            title,
            f"{len(rows) // 2}층 · 중국 서버 오픈 시간 기준으로 갱신",
            state_label.get(representative.release_state, representative.release_state),
            self._first_icon(rows),
            lambda checked=False, values=rows: self._open_rotation(values),
            formal_id=representative.primary_id,
            category=state_label.get(representative.release_state, ""),
            period=title,
        )

    def _open_rotation(self, entries: tuple[CatalogEntry, ...]) -> None:
        period_label = _period_label(entries[0].primary_id)
        levels = _group(entries, lambda row: _key_parts(row.key)[2])
        sections = []
        season_buff = self._controller.outer_buff(entries[0].primary_id)
        if season_buff is not None:
            sections.append(BrowseSection(
                "이번 기 규칙", season_buff.entry.subtitle,
                (BrowseCard(
                    season_buff.entry.title, "정식 설명과 구조화된 세부 항목 보기", "시즌 Buff",
                    self._first_icon(entries),
                    lambda checked=False, detail=season_buff: self.open_detail(detail),
                    formal_id=season_buff.entry.primary_id,
                    period=period_label,
                ),),
            ))
        cards = []
        for level, rows in levels.items():
            cards.append(BrowseCard(
                f"{level}층 · {rows[0].title}",
                " / ".join(row.secondary_label or "名称暂未提供" for row in rows),
                "층수",
                self._first_icon(rows),
                lambda checked=False, values=rows: self._open_outer_level(values),
                formal_id=rows[0].primary_id,
                difficulty=level,
                period=period_label,
            ))
        sections.append(BrowseSection("층수", f"{len(cards)}층", tuple(cards)))
        self._show_state(BrowseState(
            f"궤외지경 · {period_label}", "층을 선택하면 전반·후반과 정식 스폰 슬롯을 확인할 수 있습니다.",
            tuple(sections),
        ), push=True)

    def _open_outer_level(self, entries: tuple[CatalogEntry, ...]) -> None:
        sections = []
        for entry in entries:
            detail = self._controller.detail(entry.key)
            cards = self._monster_cards(detail, entry) if detail else ()
            sections.append(BrowseSection(
                entry.secondary_label or "名称暂未提供",
                "스폰 슬롯 기준으로 표시하며, 정체가 확인되지 않으면 이름을 추측해 채우지 않습니다.", cards,
            ))
        self._show_state(BrowseState(
            entries[0].title,
            f"{_period_label(entries[0].primary_id)} · {_key_parts(entries[0].key)[2]}층",
            tuple(sections),
        ), push=True)

    def _entity_card(
        self, mode: str, formal_id: str, entries: tuple[CatalogEntry, ...],
    ) -> BrowseCard:
        entry = entries[0]
        action = (
            (lambda checked=False, row=entry: self._open_encounter(row))
            if len(entries) == 1
            else (lambda checked=False, rows=entries: self._open_tiers(mode, rows))
        )
        return BrowseCard(
            entry.title, f"난이도 / 티어 {len(entries)}개" if len(entries) > 1 else entry.subtitle,
            _PLAY_LABELS[mode], self._first_icon(entries), action, formal_id=formal_id,
            category=self._entry_category(entry), region=self._entry_region(entry),
        )

    def _open_tiers(self, mode: str, entries: tuple[CatalogEntry, ...]) -> None:
        cards = tuple(BrowseCard(
            f"난이도 / 티어 {_key_parts(entry.key)[2]}", entry.subtitle, "정식 티어",
            self._formal_icon(self._controller.detail(entry.key)),
            lambda checked=False, row=entry: self._open_encounter(row),
            formal_id=entry.primary_id,
            difficulty=_key_parts(entry.key)[2],
            category=self._entry_category(entry),
            region=self._entry_region(entry),
        ) for entry in entries)
        self._show_state(BrowseState(
            entries[0].title, "정식 난이도 / 티어 선택",
            (BrowseSection("난이도 / 티어", f"총 {len(cards)}개 티어", cards),),
        ), push=True)

    def _open_encounter(self, entry: CatalogEntry) -> None:
        detail = self._controller.detail(entry.key)
        if detail is None:
            return
        if entry.play_mode == "feast":
            periods = self._controller.feast_periods()
            period = next(
                (
                    row for row in periods
                    if row.release_state == "current"
                    and entry.primary_id in row.challenge_ids
                ),
                next(
                    (
                        row for row in periods
                        if entry.primary_id in row.challenge_ids
                    ),
                    None,
                ),
            )
            setup = (
                self._controller.feast_setup(period.period_id, entry.primary_id)
                if period is not None else None
            )
            if setup is not None:
                self._open_feast_stage(setup)
            return
        cards = self._monster_cards(detail, entry)
        if cards:
            self._show_state(BrowseState(
                detail.entry.title, detail.entry.subtitle,
                (BrowseSection("몬스터", "몬스터를 선택하면 현재 속성 프로필을 확인할 수 있습니다.", cards),),
            ), push=True)
        else:
            self.open_detail(detail)

    def _monster_cards(
        self, detail: CatalogDetail, entry: CatalogEntry,
    ) -> tuple[BrowseCard, ...]:
        slot_sections = tuple(section for section in detail.sections if any(
            section.title.startswith(prefix)
            for prefix in ("스폰 슬롯", "몬스터 풀 구성원", "템플릿 바인딩")
        ) and "프로필" not in section.title)
        if not slot_sections and entry.play_mode == "feast":
            slot_sections = tuple(section for section in detail.sections if section.title == "正式玩法配置")
        cards = []
        for section in slot_sections:
            fields = {value.label: value.value for value in section.values}
            path = fields.get("怪物类路径") or fields.get("类路径") or fields.get("模板路径") or ""
            formal_id = fields.get("模板 ID") or fields.get("Boss 模板 ID") or _object_name(path)
            target = self._match_member_relation(detail, section.title)
            if not target:
                target = self._match_profile_relation(detail, formal_id)
            target_detail = self._controller.detail(target) if target else None
            localized_name = fields.get("怪物中文名") or fields.get("Boss 中文名")
            if localized_name in {None, "", "不可用", "名称暂未提供"}:
                localized_name = (
                    target_detail.entry.title
                    if target_detail and target_detail.entry.localization_available
                    else "名称暂未提供"
                )
            monster_level = fields.get("等级") or fields.get("配置等级")
            encounter_difficulty = self._controller.value(detail, "난이도")
            layer = self._controller.value(detail, "층")
            if monster_level:
                level_label = f"레벨 {monster_level}"
            elif encounter_difficulty:
                level_label = f"난이도 {encounter_difficulty}"
            elif layer:
                level_label = f"{layer}층"
            else:
                level_label = ""
            context = MonsterContext(
                play=_PLAY_LABELS.get(entry.play_mode, entry.play_mode),
                scene=entry.title,
                level=level_label,
                half=entry.secondary_label if entry.play_mode == "outer_realm" else "",
                slot=(
                    section.title
                    if section.title.startswith(("스폰 슬롯", "몬스터 풀 구성원"))
                    else ""
                ),
                world_level_selector=entry.play_mode in {
                    "official_illustrated", "world_boss",
                },
            )
            cards.append(BrowseCard(
                localized_name, f"{section.title} · 수량 {fields.get('数量', '暂无数据')}",
                _PLAY_LABELS.get(entry.play_mode, entry.play_mode),
                self._formal_icon(target_detail),
                (lambda checked=False, value=target_detail, ctx=context: self.open_detail(value, ctx))
                if target_detail else None,
                formal_id=formal_id or "unavailable",
                unavailable=target_detail is None,
                difficulty=context.level,
                region=context.play,
                period=entry.primary_id if entry.play_mode == "outer_realm" else "",
            ))
        return tuple(cards)

    def _entry_category(self, entry: CatalogEntry) -> str:
        detail = self._controller.detail(entry.key)
        if detail is None:
            return ""
        return (
            self._controller.value(detail, "분류")
            or _PLAY_LABELS.get(entry.play_mode, "")
        )

    def _entry_region(self, entry: CatalogEntry) -> str:
        detail = self._controller.detail(entry.key)
        if detail is None:
            return ""
        return self._controller.value(detail, "지역 / 위치") or ""

    @staticmethod
    def _match_member_relation(detail: CatalogDetail, section_title: str) -> str:
        label = f"{section_title} 적 정보"
        for relation in detail.relations:
            if relation.label == label and relation.target_key.startswith("outer_member|"):
                return relation.target_key
        return ""

    @staticmethod
    def _match_profile_relation(detail: CatalogDetail, formal_id: str) -> str:
        normalized = formal_id.casefold()
        for relation in detail.relations:
            parsed = _profile_parts(relation.target_key)
            if parsed and parsed[1].casefold() == normalized:
                return relation.target_key
        return ""

    def open_detail(
        self, detail_or_entry: CatalogDetail | CatalogEntry | None,
        context: MonsterContext | None = None,
    ) -> None:
        if detail_or_entry is None:
            return
        detail = (
            detail_or_entry if isinstance(detail_or_entry, CatalogDetail)
            else self._controller.detail(detail_or_entry.key)
        )
        if detail is None:
            return
        self.detail_view.set_detail(detail, icon=self._formal_icon(detail), context=context)
        self.stack.setCurrentWidget(self.detail_view)
        self._catalog_navigation_listener()

    def formal_icon_candidates(self, detail: CatalogDetail) -> tuple[str, ...]:
        return self._icon_resolver.candidates(detail)

    def _formal_icon(self, detail: CatalogDetail | None) -> Path | None:
        return self._icon_resolver.resolve(detail)

    def _first_icon(self, entries: Iterable[CatalogEntry]) -> Path | None:
        return self._icon_resolver.first(entries)

    def _mode_icon(self, mode: str) -> Path | None:
        return self._first_icon(self._controller.entries_for(mode))

    def _show_state(self, state: BrowseState, *, push: bool) -> None:
        if push:
            self._history.append(state)
            self._expanded_sections.clear()
            self._category_filter = ""
            self.browser_search.clear()
            self.more_filters_button.setChecked(False)
        self._active_state = state
        self.browser_title.setText(state.title)
        self.browser_subtitle.setText(state.subtitle)
        self._configure_filters(state)
        self._render_browser_state()
        self.stack.setCurrentWidget(self.browser)
        self._catalog_navigation_listener()

    def _configure_filters(self, state: BrowseState) -> None:
        self._updating_filters = True
        clear_layout(self.category_layout)
        self._category_group = QButtonGroup(self.category_host)
        self._category_group.setExclusive(True)
        categories = ("", *tuple(dict.fromkeys(
            card.category
            for section in state.sections
            for card in section.cards
            if card.category
        )))
        for index, category in enumerate(categories):
            button = QPushButton("전체" if not category else category, self.category_host)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(
                lambda _checked=False, value=category: self._set_category(value)
            )
            self._category_group.addButton(button)
            self.category_layout.addWidget(button)
        self.category_layout.addStretch(1)
        cards = tuple(card for section in state.sections for card in section.cards)
        self._fill_combo(self.difficulty_filter, (card.difficulty for card in cards))
        self._fill_combo(self.region_filter, (card.region for card in cards))
        self._fill_combo(self.period_filter, (card.period for card in cards))
        self._updating_filters = False

    @staticmethod
    def _fill_combo(combo: QComboBox, values: Iterable[str]) -> None:
        default_text = str(combo.property("defaultText"))
        combo.clear()
        combo.addItem(default_text, "")
        for value in sorted({str(value) for value in values if value}):
            combo.addItem(value, value)

    def _set_category(self, category: str) -> None:
        self._category_filter = category
        self._render_browser_state()

    def _render_browser_state(self) -> None:
        if self._updating_filters or self._active_state is None:
            return
        clear_layout(self.browser_body)
        query = self.browser_search.text().strip().casefold()
        difficulty = str(self.difficulty_filter.currentData() or "")
        region = str(self.region_filter.currentData() or "")
        period = str(self.period_filter.currentData() or "")
        for section in self._active_state.sections:
            cards = tuple(
                card for card in section.cards
                if (not query or _ko_contains(" ".join((card.title, card.subtitle, card.formal_id)), query))
                and (not self._category_filter or card.category == self._category_filter)
                and (not difficulty or card.difficulty == difficulty)
                and (not region or card.region == region)
                and (not period or card.period == period)
            )
            if not cards:
                continue
            limited = (
                cards[:section.initial_limit]
                if section.initial_limit
                and section.title not in self._expanded_sections
                else cards
            )
            self.browser_body.addWidget(section_title(section.title, section.note))
            host = QWidget(self.browser_host)
            grid = QGridLayout(host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(11)
            grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            columns = 1 if self.width() < 900 else min(4, len(limited))
            columns = max(1, columns)
            for column in range(columns):
                grid.setColumnStretch(column, 1)
            for index, model in enumerate(limited):
                grid.addWidget(
                    ArchiveCard(model, host), index // columns, index % columns,
                )
            self.browser_body.addWidget(host)
            if len(limited) < len(cards):
                expand = QPushButton(f"지난 기 전체 펼치기 ({len(cards)})", self.browser_host)
                expand.clicked.connect(
                    lambda _checked=False, value=section.title: self._expand_section(value)
                )
                self.browser_body.addWidget(expand, 0, Qt.AlignLeft)
        self.browser_body.addStretch(1)
        self._browser_layout_bucket = "narrow" if self.width() < 900 else "wide"

    def _layout_home(self, *, force: bool = False) -> None:
        width = self.width()
        if width < 520:
            bucket, columns = "compact", 1
        elif width < 900:
            bucket, columns = "narrow", 2
        else:
            bucket, columns = "wide", 3
        if not force and bucket == self._home_layout_bucket:
            return
        while self.home_grid.count():
            self.home_grid.takeAt(0)
        for column in range(3):
            self.home_grid.setColumnStretch(column, 0)
        for column in range(columns):
            self.home_grid.setColumnStretch(column, 1)
        for index, card in enumerate(self.play_group_cards):
            self.home_grid.addWidget(card, index // columns, index % columns)
        self._home_layout_bucket = bucket

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._layout_home()
        bucket = "narrow" if event.size().width() < 900 else "wide"
        if (
            bucket != self._browser_layout_bucket
            and self._active_state is not None
            and self.stack.currentWidget() is self.browser
        ):
            self._render_browser_state()

    def _expand_section(self, section_title_value: str) -> None:
        self._expanded_sections.add(section_title_value)
        self._render_browser_state()

    def _browser_back(self) -> None:
        if len(self._history) <= 1:
            self._history.clear()
            self.stack.setCurrentWidget(self.home)
            self._catalog_navigation_listener()
            return
        self._history.pop()
        self._show_state(self._history[-1], push=False)

    def _back_from_detail(self) -> None:
        if self._history:
            self._show_state(self._history[-1], push=False)
        else:
            self.stack.setCurrentWidget(self.home)
            self._catalog_navigation_listener()

    def set_catalog_navigation_listener(
        self,
        listener: Callable[[], None],
    ) -> None:
        self._catalog_navigation_listener = listener

    def catalog_back_label(self) -> str | None:
        current = self.stack.currentWidget()
        if current in {self.detail_view, self.feast_view}:
            return self._active_state.title if self._active_state is not None else "콘텐츠 목록"
        if current is self.browser:
            return self._history[-2].title if len(self._history) > 1 else "콘텐츠 분류"
        return None

    def catalog_go_back(self) -> bool:
        current = self.stack.currentWidget()
        if current in {self.detail_view, self.feast_view}:
            self._back_from_detail()
            return True
        if current is self.browser:
            self._browser_back()
            return True
        return False

    def _home_badge(self, mode: str) -> str:
        if mode == "feast":
            return f"이벤트 {len(self._controller.feast_periods())}기"
        return _home_badge(mode, self._controller.entries_for(mode))

def build_monster_catalog_page(
    *,
    service: StaticCatalogMonsterService,
    terminology_service: StaticCatalogTerminologyService,
    game_ui_asset_root: str | Path,
    parent: QWidget | None = None,
) -> MonsterCatalogPage:
    """Public factory for the shared catalog composition root."""

    if service.terminology_service is not terminology_service:
        raise ValueError("몬스터 페이지와 Service는 동일한 정식 용어 의존성을 공유해야 합니다")
    page = MonsterCatalogPage(
        controller=MonsterCatalogPageController(service),
        asset_catalog=GameUiAssetCatalog(game_ui_asset_root),
        parent=parent,
    )
    return page
