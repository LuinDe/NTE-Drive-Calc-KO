# 提供工具页养成计算器的入口卡片。
"""Entry card for the toolbox cultivation calculator page."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
def build_cultivation_calculator_entry(
    parent: QWidget,
    *,
    open_calculator: Callable[[], None],
) -> QWidget:
    """Build the small toolbox card without coupling the page to dialog details."""

    row = QFrame(parent)
    row.setObjectName("toolboxCultivationCalculatorRow")
    row.setMinimumHeight(94)
    row.setStyleSheet(themed_style(
        "QFrame#toolboxCultivationCalculatorRow{background:#161b22;border:1px solid #30363d;"
        "border-radius:10px;}QFrame#toolboxCultivationCalculatorRow:hover{background:#1c2128;border-color:#58a6ff;}"
    ))
    layout = QHBoxLayout(row)
    layout.setContentsMargins(18, 12, 16, 12)
    layout.setSpacing(15)
    copy = QVBoxLayout()
    copy.setSpacing(4)
    title = QLabel("육성 계산기", row)
    title.setStyleSheet(themed_style("font-size:16px;font-weight:800;color:#58a6ff"))
    copy.addWidget(title)
    description = QLabel(
        "전체 페이지로 이동해 캐릭터, 스킬, 아크 육성 재료를 계산합니다; 현재는 가방을 차감하지 않고 스태미나도 추산하지 않습니다.",
        row,
    )
    description.setWordWrap(True)
    description.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    copy.addWidget(description)
    layout.addLayout(copy, 1)
    button = QPushButton("진입", row)
    button.setObjectName("toolboxCultivationCalculator")
    button.setCursor(Qt.PointingHandCursor)
    button.setMinimumSize(76, 38)
    button.setStyleSheet(themed_style(
        "QPushButton{background:#d6f0ff;color:#0b3150;border:1px solid #79c0ff;border-radius:7px;"
        "font-size:13px;font-weight:800;padding:6px 16px;}"
        "QPushButton:hover{background:#b6e3ff;border-color:#a5d6ff;}"
        "QPushButton:pressed{background:#9ed5f5;}"
    ))
    button.clicked.connect(open_calculator)
    layout.addWidget(button, 0, Qt.AlignVCenter)
    return row


__all__ = ["build_cultivation_calculator_entry"]
