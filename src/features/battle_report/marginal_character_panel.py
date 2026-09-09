# 展示战报冻结静态面板与按公式关联伤害加权的逐击动态面板。
"""Wide character-panel comparison for fixed-axis marginal analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QFrame,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.app.theme import themed_style
from src.domain.battle_report import (
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
)
from src.features.battle_report.marginal_result_table_view import (
    render_attribute_results,
)
from src.services.battle_marginal_panel_service import (
    BattleMarginalPanelResult,
    CHARACTER_PANEL_DYNAMIC_PROPERTIES as _DYNAMIC_PROPERTIES,
)


@dataclass(frozen=True, slots=True)
class _PanelProperty:
    property_id: str
    label: str
    is_percent: bool = False


_ELEMENT_PROPERTIES = (
    _PanelProperty("DamageUpChaosBase", "暗属性伤害", True),
    _PanelProperty("DamageUpCosmosBase", "光属性伤害", True),
    _PanelProperty("DamageUpIncantationBase", "咒属性伤害", True),
    _PanelProperty("DamageUpLakshanaBase", "相属性伤害", True),
    _PanelProperty("DamageUpNatureBase", "灵属性伤害", True),
    _PanelProperty("DamageUpPsycheBase", "魂属性伤害", True),
    _PanelProperty("DamageUpPsychicallyBase", "心灵伤害", True),
)
_ELEMENT_PROPERTY_BY_ATTRIBUTE = {
    "chaos": "DamageUpChaosBase",
    "cosmos": "DamageUpCosmosBase",
    "incantation": "DamageUpIncantationBase",
    "lakshana": "DamageUpLakshanaBase",
    "nature": "DamageUpNatureBase",
    "psyche": "DamageUpPsycheBase",
    "psychically": "DamageUpPsychicallyBase",
}
_PENETRATION_PROPERTIES = (
    _PanelProperty("DamagePenetrateChaos", "암속성 관통", True),
    _PanelProperty("DamagePenetrateCosmos", "빛속성 관통", True),
    _PanelProperty("DamagePenetrateIncantation", "주속성 관통", True),
    _PanelProperty("DamagePenetrateLakshana", "상속성 관통", True),
    _PanelProperty("DamagePenetrateNature", "령속성 관통", True),
    _PanelProperty("DamagePenetratePsyche", "혼속성 관통", True),
    _PanelProperty("DamagePenetratePsychically", "정신 관통", True),
)
_PANEL_PROPERTIES = (
    _PanelProperty("CritBase", "暴击率", True),
    _PanelProperty("CritDamageBase", "暴击伤害", True),
    _PanelProperty("DamageUpGeneralBase", "通用伤害增强", True),
    *_ELEMENT_PROPERTIES,
    _PanelProperty("MagBase", "环合强度"),
    _PanelProperty("AtkBase", "基础攻击力"),
    _PanelProperty("AtkAdd", "추가 공격력"),
    _PanelProperty("AtkUp", "攻击力%", True),
    _PanelProperty("PanelAtk", "总攻击力"),
    _PanelProperty("HPMaxBase", "기본 HP"),
    _PanelProperty("HPMaxAdd", "추가 HP"),
    _PanelProperty("HPMaxUp", "生命值%", True),
    _PanelProperty("PanelHP", "总生命值"),
    _PanelProperty("DefBase", "기본 방어력"),
    _PanelProperty("DefAdd", "추가 방어력"),
    _PanelProperty("DefUp", "防御力%", True),
    _PanelProperty("PanelDef", "总防御力"),
    _PanelProperty("DefIgnore", "방어 무시", True),
    *_PENETRATION_PROPERTIES,
    _PanelProperty("UnbalIntensityBase", "倾陷强度"),
    _PanelProperty("UnbalIntensityUp", "倾陷强度%", True),
    _PanelProperty("UnbalIntensityAdd", "추가 브레이크 강도"),
    _PanelProperty("UnbalDamageUp", "브레이크 피해 증강", True),
    _PanelProperty("HealUp", "治疗加成", True),
    _PanelProperty("HealBeUp", "受治疗加成", True),
    _PanelProperty("ShieldUp", "보호막 보너스", True),
    _PanelProperty("ChargeGetEfficiencyBase", "充能效率", True),
    _PanelProperty("UltraEnergyAdd", "추가 종결 스킬 에너지"),
)
_DERIVED_PROPERTIES = frozenset({"PanelAtk", "PanelHP", "PanelDef"})
def render_character_panel_and_margins(
    panel: "BattleMarginalCharacterPanel",
    attribute_table: QTableWidget,
    *,
    analysis: BattleAnalysisSnapshot | None,
    baseline: BattleCharacterBaseline | None,
    marginal_panel: BattleMarginalPanelResult | None,
) -> None:
    """Render both views from one shared fixed-axis marginal calculation."""

    if baseline is None or analysis is None:
        panel.clear()
        attribute_table.setRowCount(0)
        return
    current = marginal_panel if marginal_panel is not None and marginal_panel.character_id == baseline.character_id else None
    results = () if current is None else current.results
    drive_property_ids = () if current is None else current.drive_property_ids
    panel.render(
        baseline,
        results,
        current_element_property=_current_element_property(analysis, baseline),
    )
    render_attribute_results(
        attribute_table,
        tuple(row for row in results if row.property_id in drive_property_ids),
    )


def _current_element_property(
    analysis: BattleAnalysisSnapshot,
    baseline: BattleCharacterBaseline,
) -> str | None:
    """Infer the role element from its own non-reaction, non-weave damage."""

    replays = {row.event_id: row for row in analysis.hit_replays}
    damage_by_property: dict[str, float] = {}
    for hit in analysis.hits:
        classification = str(hit.classification or "").casefold()
        if (
            hit.direction != "outgoing"
            or hit.character_id != baseline.character_id
            or classification == "weave"
            or classification.startswith("reaction")
        ):
            continue
        replay = replays.get(hit.event_id)
        attribute = str(
            getattr(replay, "formula_damage_attribute", "")
            or hit.damage_attribute
            or ""
        ).casefold()
        property_id = _ELEMENT_PROPERTY_BY_ATTRIBUTE.get(attribute)
        if property_id is not None:
            damage_by_property[property_id] = (
                damage_by_property.get(property_id, 0.0)
                + max(0.0, float(hit.damage))
            )
    if not damage_by_property:
        return None
    return max(damage_by_property, key=damage_by_property.__getitem__)


def _format_value(value: float | None, *, percent: bool) -> str:
    if value is None:
        return "—"
    if percent:
        return f"{float(value) * 100:.2f}%"
    return f"{float(value):,.2f}"


def _derived_value(
    values: Mapping[str, float | None],
    base_id: str,
    up_id: str,
    add_id: str,
) -> float | None:
    base = values.get(base_id)
    up = values.get(up_id)
    addition = values.get(add_id)
    if base is None or up is None or addition is None:
        return None
    return float(base) * (1.0 + float(up)) + float(addition)


class BattleMarginalCharacterPanel(QFrame):
    """Render two wide rows without inventing unsupported dynamic values."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        title = QLabel("캐릭터 패널")
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        note = QLabel(
            "정적 패널은 이번 전투의 고정된 캐릭터 스냅샷에서 가져옵니다. 동적 패널은 해당 속성이 실제로 연결된 공식 패널 피해를 기준으로, "
            "피해 발생 시점의 Buff 적용 후 속성으로 가중합니다. 공식 연결이 없거나 동적 근거가 부족하면 “—”를 표시합니다."
        )
        note.setObjectName("battleMarginalCharacterPanelNote")
        note.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 0)
        self.table.setObjectName("battleMarginalCharacterPanelTable")
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(90)
        self.table.setMinimumHeight(132)
        self.table.setMaximumHeight(132)
        layout.addWidget(self.table)

    def clear(self) -> None:
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)

    def render(
        self,
        baseline: BattleCharacterBaseline,
        results: Sequence[object],
        *,
        current_element_property: str | None = None,
    ) -> None:
        static = {row.property_id: float(row.value) for row in baseline.stats}
        result_by_property = {
            str(row.property_id): row for row in results
            if str(getattr(row, "property_id", ""))
        }
        dynamic: dict[str, float | None] = {
            property_id: getattr(
                result_by_property.get(property_id),
                "weighted_effective_value",
                None,
            )
            for property_id in _DYNAMIC_PROPERTIES
        }
        for base_id, up_id, add_id, total_id in (
            ("AtkBase", "AtkUp", "AtkAdd", "PanelAtk"),
            ("HPMaxBase", "HPMaxUp", "HPMaxAdd", "PanelHP"),
            ("DefBase", "DefUp", "DefAdd", "PanelDef"),
        ):
            if dynamic.get(up_id) is not None or dynamic.get(add_id) is not None:
                dynamic[base_id] = static.get(base_id, 0.0)
            dynamic[total_id] = _derived_value(dynamic, base_id, up_id, add_id)
        properties = self._properties(baseline)
        self.table.clear()
        self.table.setRowCount(2)
        self.table.setColumnCount(len(properties) + 1)
        self.table.setHorizontalHeaderLabels(("属性", *tuple(
            f"{row.label} (본속성)"
            if row.property_id == current_element_property else row.label
            for row in properties
        )))
        self.table.setColumnWidth(0, 100)
        for row_index, label in enumerate(("정적 패널", "동적 패널")):
            item = QTableWidgetItem(label)
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row_index, 0, item)
        for column, row in enumerate(properties, start=1):
            static_value = static.get(row.property_id, 0.0)
            if row.property_id in _DERIVED_PROPERTIES and row.property_id not in static:
                prefixes = {
                    "PanelAtk": ("AtkBase", "AtkUp", "AtkAdd"),
                    "PanelHP": ("HPMaxBase", "HPMaxUp", "HPMaxAdd"),
                    "PanelDef": ("DefBase", "DefUp", "DefAdd"),
                }[row.property_id]
                static_value = _derived_value(static, *prefixes) or 0.0
            for row_index, value in enumerate((
                static_value,
                dynamic.get(row.property_id),
            )):
                item = QTableWidgetItem(
                    _format_value(value, percent=row.is_percent)
                )
                item.setTextAlignment(Qt.AlignCenter)
                if row_index == 1 and value is None:
                    item.setToolTip("이 속성에는 안전하게 귀속할 수 있는 공식 패널 피해나 동적 투영이 없습니다.")
                self.table.setItem(row_index, column, item)
            self.table.setColumnWidth(column, max(112, len(row.label) * 18 + 24))

    @staticmethod
    def _properties(baseline: BattleCharacterBaseline) -> tuple[_PanelProperty, ...]:
        known = {row.property_id for row in _PANEL_PROPERTIES}
        extras = tuple(
            _PanelProperty(row.property_id, row.label, bool(row.is_percent))
            for row in baseline.stats
            if row.property_id not in known
        )
        return tuple((*_PANEL_PROPERTIES, *extras))


__all__ = [
    "BattleMarginalCharacterPanel",
    "render_character_panel_and_margins",
]
