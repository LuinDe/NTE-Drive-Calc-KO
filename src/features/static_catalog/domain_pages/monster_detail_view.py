# 怪物与玩法页面的紧凑详情视图。
"""Compact, progressive-disclosure detail view for monster catalog records."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.monster_widgets import (
    BuffCard,
    DropCard,
    MetricCard,
    ResistanceCard,
    clear_layout,
    section_title,
    set_art,
    source_color,
)
from src.services.static_catalog_monster_service import CatalogDetail, CatalogSection
from src.ui.widgets import NoWheelComboBox


_SUMMARY_FIELDS = {
    "중국어 이름",
    "지역 / 위치",
    "도전 이름",
    "Boss 중국어 이름",
    "난이도",
    "몬스터 레벨",
    "기본 점수",
    "점수 배율",
    "특수 고난도",
    "층",
    "전반/후반",
    "수량",
    "레벨",
    "추천 캐릭터 레벨",
    "난이도 등급",
    "팀 레벨",
    "체력",
    "처치 제한 시간",
}


@dataclass(frozen=True, slots=True)
class MonsterContext:
    play: str
    scene: str
    level: str = ""
    half: str = ""
    slot: str = ""
    world_level_selector: bool = False


class MonsterDetailView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stat_cards: list[MetricCard] = []
        self.resistance_cards: list[ResistanceCard] = []
        self.buff_cards: list[BuffCard] = []
        self.drop_cards: list[DropCard] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.crumb = QLabel("몬스터 및 콘텐츠", self)
        self.crumb.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
        top.addWidget(self.crumb)
        top.addStretch(1)
        root.addLayout(top)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(themed_style(
            "QScrollArea{background:#0d1117;border:0;}"
            "QScrollArea>QWidget>QWidget{background:#0d1117;}"
        ))
        self.host = QWidget(scroll)
        self.body = QVBoxLayout(self.host)
        self.body.setContentsMargins(4, 4, 12, 24)
        self.body.setSpacing(10)
        scroll.setWidget(self.host)
        root.addWidget(scroll, 1)

    def set_detail(
        self,
        detail: CatalogDetail,
        *,
        icon: Path | None,
        context: MonsterContext | None = None,
    ) -> None:
        clear_layout(self.body)
        self.stat_cards = []
        self.resistance_cards = []
        self.buff_cards = []
        self.drop_cards = []
        self.crumb.setText(
            "몬스터 및 콘텐츠 / " + (context.play if context else _clean_subtitle(detail))
        )
        self.body.addWidget(self._hero(detail, icon, context))
        profile_sections = tuple(
            section for section in detail.sections if "画像" in section.title
        )
        if profile_sections:
            self.body.addWidget(section_title("전투 프로필", "HP, 방어, 브레이크, 저항"))
            world_sections = tuple(
                section for section in profile_sections
                if _profile_kind(section) == "world_level"
            )
            self._profile_sections = (
                world_sections
                if context and context.world_level_selector and world_sections
                else profile_sections
            )
            self.profile_host = QWidget(self.host)
            self.profile_layout = QVBoxLayout(self.profile_host)
            self.profile_layout.setContentsMargins(0, 0, 0, 0)
            if context and context.world_level_selector and len(self._profile_sections) > 1:
                selector = QFrame(self.host)
                selector.setObjectName("monsterWorldLevelSelector")
                selector.setStyleSheet(themed_style(
                    "QFrame#monsterWorldLevelSelector{background:#161b22;border:0;"
                    "border-radius:10px;}"
                ))
                selector_layout = QHBoxLayout(selector)
                selector_layout.setContentsMargins(10, 8, 10, 8)
                label = QLabel("오픈 월드 레벨", selector)
                label.setStyleSheet(themed_style(
                    "color:#c9d1d9;font-size:10px;font-weight:800"
                ))
                self.world_level_combo = NoWheelComboBox(selector)
                self.world_level_combo.setObjectName("monsterWorldLevelCombo")
                ordered = sorted(
                    enumerate(self._profile_sections),
                    key=lambda item: _profile_level(item[1]),
                )
                for section_index, section in ordered:
                    level = _profile_level(section)
                    self.world_level_combo.addItem(
                        f"레벨 {level:g}", section_index,
                    )
                self.world_level_combo.currentIndexChanged.connect(
                    self._show_selected_profile
                )
                selector_layout.addWidget(label)
                selector_layout.addWidget(self.world_level_combo, 1)
                self.body.addWidget(selector)
                self.world_level_combo.setCurrentIndex(
                    self.world_level_combo.count() - 1
                )
            else:
                profile_index = _matching_profile_index(
                    self._profile_sections,
                    context.level if context else "",
                )
                self.profile_layout.addWidget(
                    self._profile(self._profile_sections[profile_index])
                )
            self.body.addWidget(self.profile_host)
        self._add_scene_options(detail)
        self._add_drop_projection(detail)
        self._add_summary(detail)
        self.body.addStretch(1)

    def _add_scene_options(self, detail: CatalogDetail) -> None:
        options = next(
            (
                section for section in detail.sections
                if section.title in {
                    "선택한 도전 조건", "마녀의 축복", "궤외 시즌 Buff",
                }
            ),
            None,
        )
        values = tuple(
            value for value in (options.values if options else ())
            if "경로" not in value.label and "리소스" not in value.label
        )
        if not values:
            return
        heading = {
            "魔女赐福": "战前赐福选择",
            "轨外赛季 Buff": "本期赛季规则",
        }.get(options.title, "场景增益 / 限制")
        self.body.addWidget(section_title(
            heading, f"규칙 {len(values)}개, 효과 설명은 현재 페이지에서 바로 펼쳐집니다",
        ))
        if options.note:
            description = QLabel(options.note, self.host)
            description.setWordWrap(True)
            description.setStyleSheet(themed_style(
                "background:#161b22;color:#c9d1d9;border:0;"
                "border-radius:10px;padding:9px;font-size:10px"
            ))
            self.body.addWidget(description)
        preview = QWidget(self.host)
        preview_grid = QGridLayout(preview)
        preview_grid.setContentsMargins(0, 0, 0, 0)
        for index, value in enumerate(values[:4]):
            card = BuffCard(
                value.display_label or "규칙 이름 미제공",
                value.display_value or "규칙 설명 미제공",
                preview,
            )
            self.buff_cards.append(card)
            preview_grid.addWidget(card, index // 2, index % 2)
        self.body.addWidget(preview)
        if len(values) > 4:
            remainder = QWidget(self.host)
            grid = QGridLayout(remainder)
            grid.setContentsMargins(0, 0, 0, 0)
            for index, value in enumerate(values[4:]):
                card = BuffCard(
                    value.display_label or "규칙 이름 미제공",
                    value.display_value or "규칙 설명 미제공",
                    remainder,
                )
                self.buff_cards.append(card)
                grid.addWidget(card, index // 2, index % 2)
            self.body.addWidget(_disclosure(
                f"나머지 {len(values) - 4}개 펼치기", remainder, self.host,
            ))

    def _add_drop_projection(self, detail: CatalogDetail) -> None:
        drops = next(
            (section for section in detail.sections if section.title == "正式掉落"),
            None,
        )
        if drops is None:
            return
        self.body.addWidget(section_title(
            "정식 드롭", drops.note or "확인된 드롭 아이템과 수량만 표시",
        ))
        values = tuple(drops.values)
        preview = QWidget(self.host)
        grid = QGridLayout(preview)
        grid.setContentsMargins(0, 0, 0, 0)
        columns = 1 if self.width() < 700 else 2
        initial = values[:8]
        for index, value in enumerate(initial):
            card = DropCard(
                value.display_label or "名称暂未提供",
                value.display_value or "확인 가능한 수량 없음",
                preview,
                warning=value.provenance == "unavailable",
            )
            self.drop_cards.append(card)
            grid.addWidget(card, index // columns, index % columns)
        self.body.addWidget(preview)
        if len(values) > len(initial):
            remainder = QWidget(self.host)
            remainder_grid = QGridLayout(remainder)
            remainder_grid.setContentsMargins(0, 0, 0, 0)
            for index, value in enumerate(values[len(initial):]):
                card = DropCard(
                    value.display_label or "名称暂未提供",
                    value.display_value or "확인 가능한 수량 없음",
                    remainder,
                    warning=value.provenance == "unavailable",
                )
                self.drop_cards.append(card)
                remainder_grid.addWidget(card, index // columns, index % columns)
            self.body.addWidget(_disclosure(
                f"나머지 드롭 펼치기 ({len(values) - len(initial)})",
                remainder,
                self.host,
            ))

    def _add_summary(self, detail: CatalogDetail) -> None:
        values = [
            value
            for section in detail.sections
            if "画像" not in section.title and section.title != "来源追溯"
            for value in section.values
            if value.label in _SUMMARY_FIELDS
        ]
        if not values:
            return
        self.body.addWidget(section_title("콘텐츠 요약", "주요 정보"))
        host = QWidget(self.host)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        for index, value in enumerate(values[:9]):
            grid.addWidget(
                MetricCard(
                    value.display_label or value.label,
                    _friendly_value(value.display_value or value.value),
                    source_color(value.provenance),
                    host,
                ),
                index // 3,
                index % 3,
            )
        self.body.addWidget(host)

    def _hero(
        self, detail: CatalogDetail, icon: Path | None, context: MonsterContext | None,
    ) -> QFrame:
        hero = QFrame(self.host)
        hero.setObjectName("monsterDetailHero")
        hero.setFixedHeight(156)
        hero.setStyleSheet(themed_style(
            "QFrame#monsterDetailHero{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #10243f,stop:.72 #161b22,stop:1 #0d1117);"
            "border:0;border-radius:16px;}"
        ))
        layout = QHBoxLayout(hero)
        layout.setContentsMargins(14, 10, 16, 10)
        art = QLabel(hero)
        art.setFixedSize(132, 132)
        art.setAlignment(Qt.AlignCenter)
        set_art(art, icon, 126, unavailable=icon is None)
        layout.addWidget(art)
        copy = QVBoxLayout()
        label = QLabel(
            "콘텐츠 규칙" if detail.entry.key.startswith(("witch_buff|", "outer_buff|"))
            else "적 정보",
            hero,
        )
        label.setStyleSheet(themed_style("color:#58a6ff;font-size:10px;font-weight:900"))
        title = QLabel(_display_title(detail, context), hero)
        title.setWordWrap(True)
        title.setStyleSheet(themed_style("color:#f0f6fc;font-size:23px;font-weight:900"))
        copy.addWidget(label)
        copy.addWidget(title)
        values = (
            (context.scene, context.level, context.half, context.slot)
            if context else (_clean_subtitle(detail),)
        )
        summary = QLabel("  •  ".join(value for value in values if value), hero)
        summary.setWordWrap(True)
        summary.setStyleSheet(themed_style("color:#e3b341;font-size:10px;font-weight:800"))
        copy.addWidget(summary)
        copy.addStretch(1)
        layout.addLayout(copy, 1)
        return hero

    def _profile(self, section: CatalogSection) -> QFrame:
        frame = QFrame(self.host)
        frame.setStyleSheet(themed_style(
            "QFrame{background:#161b22;border:0;border-radius:14px;}"
        ))
        layout = QVBoxLayout(frame)
        title = QLabel(_profile_title(section.title), frame)
        title.setStyleSheet(themed_style("color:#f0f6fc;font-size:12px;font-weight:900"))
        layout.addWidget(title)
        values = {value.label: value for value in section.values}
        stats = (
            ("生命", _join_profile(values, "HP 기본", "HP 보너스", "HP 고정값"), "#39d0d8"),
            ("防御", _join_profile(values, "방어 기본", "방어 보너스", "방어 고정값", "방어 무시"), "#58a6ff"),
            ("倾陷", _join_profile(values, "브레이크 상한", "브레이크 회복"), "#e3b341"),
            ("等级 / 难度", _value_text(values.get("怪物等级")), "#a371f7"),
        )
        grid = QGridLayout()
        for index, (label, value, accent) in enumerate(stats):
            card = MetricCard(label, value, accent, frame)
            self.stat_cards.append(card)
            grid.addWidget(card, index // 2, index % 2)
        layout.addLayout(grid)
        resistance_grid = QGridLayout()
        resistances = [
            (value.display_label or "저항 이름 미제공", _value_text(value))
            for label, value in values.items()
            if label.startswith("抗性 ")
        ]
        columns = 2 if self.width() < 900 else 4
        for index, (label, value) in enumerate(resistances):
            card = ResistanceCard(label, value, frame)
            self.resistance_cards.append(card)
            resistance_grid.addWidget(card, index // columns, index % columns)
        layout.addLayout(resistance_grid)
        penetration = QLabel(
            f"방어 무시 {_value_text(values.get('防御忽略'))}  ·  "
            f"공격 티어 {_value_text(values.get('攻击档'))}",
            frame,
        )
        penetration.setWordWrap(True)
        penetration.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
        layout.addWidget(penetration)
        return frame

    def _show_selected_profile(self) -> None:
        if not hasattr(self, "world_level_combo"):
            return
        section_index = self.world_level_combo.currentData()
        if section_index is None:
            return
        clear_layout(self.profile_layout)
        self.stat_cards = []
        self.resistance_cards = []
        self.profile_layout.addWidget(
            self._profile(self._profile_sections[int(section_index)])
        )


def _disclosure(title: str, content: QWidget, parent: QWidget) -> QWidget:
    host = QWidget(parent)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    toggle = QToolButton(host)
    toggle.setText(title)
    toggle.setCheckable(True)
    toggle.setChecked(False)
    toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
    toggle.setStyleSheet(themed_style(
        "QToolButton{background:#161b22;color:#8b949e;border:0;"
        "border-radius:8px;padding:7px 10px;text-align:left;font-weight:700;}"
        "QToolButton:checked{color:#58a6ff;}"
    ))
    content.setVisible(False)
    toggle.toggled.connect(content.setVisible)
    layout.addWidget(toggle)
    layout.addWidget(content)
    return host


def _clean_subtitle(detail: CatalogDetail) -> str:
    parts = [part.strip() for part in detail.entry.subtitle.split("·")]
    readable = [part for part in parts if part and "ID" not in part and "_" not in part]
    return " · ".join(readable) or "정식 전투 프로필"


def _display_title(detail: CatalogDetail, context: MonsterContext | None) -> str:
    if detail.entry.title != detail.entry.primary_id:
        return detail.entry.title
    if context and context.scene:
        return context.scene
    return "몬스터 프로필"


def _profile_title(title: str) -> str:
    return title.replace("等价公式", "").replace("公式", "").strip(" ·") or "战斗画像"


def _profile_level(section: CatalogSection) -> float:
    value = next(
        (item for item in section.values if item.label == "怪物等级"),
        None,
    )
    try:
        return float(getattr(value, "value", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _profile_kind(section: CatalogSection) -> str:
    return next(
        (
            value.value for value in section.values
            if value.label == "画像档位类型"
        ),
        "",
    )


def _matching_profile_index(
    sections: tuple[CatalogSection, ...], context_level: str,
) -> int:
    match = re.search(r"(?:等级|Lv\.?)[^0-9]*(\d+(?:\.\d+)?)", context_level)
    if match is None:
        return 0
    wanted = float(match.group(1))
    return next(
        (
            index for index, section in enumerate(sections)
            if _profile_level(section) == wanted
        ),
        0,
    )


def _join_profile(values: dict[str, object], *labels: str) -> str:
    return "  ·  ".join(
        f"{label.removeprefix('生命').removeprefix('防御').removeprefix('倾陷') or label} "
        f"{_value_text(values.get(label))}" for label in labels
    )


def _value_text(value: object | None) -> str:
    if value is None:
        return "데이터 없음"
    display_value = str(getattr(value, "display_value", "") or "")
    raw_value = str(getattr(value, "value", "") or "")
    return _friendly_value(display_value or raw_value)


def _friendly_value(value: str) -> str:
    text = str(value)
    if text.startswith("不可用"):
        return "데이터 없음"
    try:
        number = float(text)
    except ValueError:
        return text
    if "e" in text.casefold() or abs(number) >= 10_000:
        return f"{number:,.3f}".rstrip("0").rstrip(".")
    return text
