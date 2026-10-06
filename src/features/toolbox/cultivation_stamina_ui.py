# 提供养成计算器的猎人等级输入与体力结果展示。
"""Stamina controls and compact result formatting for cultivation planning."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QWidget,
)

from src.app.theme import themed_style
from src.domain.progression_stamina import (
    ProgressionStaminaResult,
    project_identification_level,
)
from src.features.toolbox.cultivation_controls import disable_numeric_input_method


class CultivationStaminaControls(QFrame):
    """Collect the account-independent level boundary used for farming stages."""

    values_changed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("cultivationStaminaControls")
        self.setStyleSheet(themed_style(
            "QFrame#cultivationStaminaControls{background:#161b22;"
            "border:1px solid #30363d;border-radius:9px;}"
        ))
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(9)
        title = QLabel("스태미나 계산", self)
        title.setStyleSheet(themed_style(
            "color:#c9d1d9;font-size:14px;font-weight:800"
        ))
        row.addWidget(title)
        row.addWidget(QLabel("헌터 레벨", self))
        self.hunter_level = QSpinBox(self)
        disable_numeric_input_method(self.hunter_level)
        self.hunter_level.setObjectName("cultivationHunterLevel")
        self.hunter_level.setRange(1, 60)
        self.hunter_level.setValue(60)
        row.addWidget(self.hunter_level)
        row.addWidget(QLabel("적용 감별 레벨", self))
        self.identification_level = QComboBox(self)
        self.identification_level.setObjectName("cultivationIdentificationLevel")
        row.addWidget(self.identification_level)
        hint = QLabel("정식 던전 확정 산출 기준으로 계산; 몹 사냥/Boss 드롭은 던전 스태미나에 계산하지 않음", self)
        hint.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        row.addWidget(hint, 1)
        self.hunter_level.valueChanged.connect(self._refresh_identification)
        self.identification_level.currentIndexChanged.connect(
            lambda _index: self.values_changed.emit()
        )
        self._refresh_identification()

    def values(self) -> tuple[int, int]:
        return self.hunter_level.value(), int(self.identification_level.currentData())

    def restore_values(self, hunter_level: int, identification_level: int | None) -> None:
        projection = project_identification_level(hunter_level, effective_level=identification_level)
        with QSignalBlocker(self), QSignalBlocker(self.hunter_level):
            self.hunter_level.setValue(hunter_level)
            self._refresh_identification()
            with QSignalBlocker(self.identification_level):
                index = self.identification_level.findData(projection.effective_level)
                if index < 0:
                    raise ValueError("기록의 감별 레벨이 현재 범위와 일치하지 않습니다")
                self.identification_level.setCurrentIndex(index)

    def _refresh_identification(self) -> None:
        previous = self.identification_level.currentData()
        projection = project_identification_level(self.hunter_level.value())
        levels = [projection.native_level]
        if projection.native_level >= 3:
            levels.append(projection.native_level - 1)
        self.identification_level.blockSignals(True)
        self.identification_level.clear()
        for level in levels:
            suffix = "(현재)" if level == projection.native_level else "(하향)"
            self.identification_level.addItem(f"감별 {level} {suffix}", level)
        selected = previous if previous in levels else projection.native_level
        self.identification_level.setCurrentIndex(levels.index(selected))
        self.identification_level.blockSignals(False)
        self.values_changed.emit()


def stamina_summary_text(result: ProgressionStaminaResult | None) -> str:
    if result is None:
        return "스태미나 데이터를 아직 사용할 수 없음"
    if result.total_stamina is not None:
        return f"최소 스태미나 {result.total_stamina:,}"
    if result.known_stamina:
        return f"알려진 스태미나 {result.known_stamina:,} · 일부 재료는 계산 불가"
    return "스태미나를 아직 계산할 수 없음"


def stamina_runs_text(result: ProgressionStaminaResult | None) -> str:
    if result is None:
        return ""
    if result.runs:
        return "던전 추천:" + "；".join(
            f"{run.label} × {run.runs}회 ({run.total_stamina:,} 스태미나)"
            for run in result.runs
        )
    if result.total_stamina == 0:
        return "이 항목은 재료 던전 스태미나를 집계할 필요가 없습니다"
    if result.unresolved_item_ids:
        return "확정 산출 재료 부족:" + "、".join(result.unresolved_item_ids)
    return ""


def style_stamina_badge(label: QLabel) -> None:
    label.setObjectName("cultivationStaminaBadge")
    label.setStyleSheet(themed_style(
        "QLabel#cultivationStaminaBadge{color:#3fb950;background:#0d1117;"
        "border:1px solid #238636;border-radius:6px;padding:4px 8px;font-weight:800;}"
    ))


__all__ = [
    "CultivationStaminaControls",
    "stamina_runs_text",
    "stamina_summary_text",
    "style_stamina_badge",
]
