# 倒带执行选项与十二种驱动槽位选择弹窗。
"""UI-only dialogs for configuring a custom rewind run."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.domain.rewind_shape_recommendation import RewindShapeRecommendation
from src.features.inventory.warehouse import warehouse_shape_pixmap


@dataclass(frozen=True, slots=True)
class RewindExecutionOptions:
    """Player-selected draw qualities and custom-pool behavior."""

    qualities: tuple[str, ...] = ("gold",)
    drive_customization: str = "none"


class RewindExecutionDialog(QDialog):
    """Collect multi-quality and custom-pool choices as recommendation-style tiles."""

    def __init__(self, parent=None, *, initial: RewindExecutionOptions | None = None) -> None:
        super().__init__(parent)
        options = initial or RewindExecutionOptions()
        self.setWindowTitle("되감기 진행")
        self.setMinimumWidth(560)
        root = QVBoxLayout(self)
        root.setSpacing(12)
        prerequisite = QLabel(
            "사용 전에 게임 내 되감기 페이지를 미리 열어 두세요. 이 기능은 아직 실험 단계이며,"
            "현재 실제 기기 테스트 조건이 없어 동작을 보장하지 않습니다. 실행 중에는 설정의 전역 중지 키"
            f"({self._stop_hotkey_label()})로 중지할 수 있습니다."
        )
        prerequisite.setObjectName("rewindExperimentalNotice")
        prerequisite.setWordWrap(True)
        prerequisite.setStyleSheet(themed_style(
            "background:#1f6feb33;color:#58a6ff;border:1px solid #58a6ff;"
            "border-radius:8px;padding:9px;font-weight:700"
        ))
        root.addWidget(prerequisite)

        root.addWidget(QLabel("뽑기 품질 (다중 선택 가능)"))
        quality_grid = QGridLayout()
        self._quality_buttons: dict[str, QPushButton] = {}
        for index, (label, value, tone) in enumerate((
            ("파란색 품질", "blue", "#58a6ff"),
            ("보라색 품질", "purple", "#bc8cff"),
            ("금색 품질", "gold", "#f2cc60"),
        )):
            button = self._tile(label, tone, checked=value in options.qualities)
            button.setObjectName("rewindQualityTile")
            button.setProperty("rewindValue", value)
            self._quality_buttons[value] = button
            button.toggled.connect(self._sync_customization_availability)
            quality_grid.addWidget(button, 0, index)
        root.addLayout(quality_grid)

        customization_header = QHBoxLayout()
        customization_header.addWidget(QLabel("드라이브 맞춤"))
        customization_help = QPushButton("?")
        customization_help.setObjectName("rewindCustomizationHelp")
        customization_help.setToolTip("세 가지 드라이브 맞춤 방식의 차이 보기")
        customization_help.setStyleSheet(themed_style(
            "QPushButton{background:transparent;border:1px solid #30363d;border-radius:10px;"
            "color:#8b949e;font-size:11px;font-weight:700;padding:2px 7px;"
            "min-width:20px;max-width:20px;min-height:20px;max-height:20px;}"
            "QPushButton:hover{background:#1f6feb33;color:#58a6ff;border-color:#58a6ff;}"
        ))
        customization_help.clicked.connect(self._show_customization_help)
        customization_header.addWidget(customization_help)
        customization_header.addStretch(1)
        root.addLayout(customization_header)
        customization_grid = QGridLayout()
        self._customization = QButtonGroup(self)
        self._customization_buttons: dict[str, QPushButton] = {}
        for index, (label, value, tone) in enumerate((
            ("아니요", "none", "#8b949e"),
            ("예, 변경 없음", "enabled", "#58a6ff"),
            ("예, 방안 적용", "apply_plan", "#3fb950"),
        )):
            button = self._tile(label, tone, checked=value == options.drive_customization)
            button.setObjectName("rewindCustomizationTile")
            button.setProperty("rewindValue", value)
            self._customization.addButton(button)
            self._customization_buttons[value] = button
            customization_grid.addWidget(button, 0, index)
        root.addLayout(customization_grid)

        buttons = QDialogButtonBox()
        start = buttons.addButton("시작", QDialogButtonBox.AcceptRole)
        cancel = buttons.addButton("취소", QDialogButtonBox.RejectRole)
        start.setObjectName("rewindStart")
        cancel.setObjectName("rewindCancel")
        start.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        root.addWidget(buttons)
        self._sync_customization_availability()

    def _stop_hotkey_label(self) -> str:
        parent = self.parentWidget()
        while parent is not None:
            manager = getattr(parent, "global_hotkey_manager", None)
            configuration = getattr(manager, "configuration", None)
            if configuration is not None:
                return str(getattr(configuration, "stop", "전역 중지 키"))
            parent = parent.parentWidget()
        return "전역 중지 키"

    @staticmethod
    def _tile(label: str, tone: str, *, checked: bool) -> QPushButton:
        """Match the compact strategy and grade buttons in the recommendation panel."""

        button = QPushButton(label)
        button.setCheckable(True)
        button.setChecked(checked)
        button.setMinimumHeight(42)
        button.setStyleSheet(themed_style(
            "QPushButton{background:#21262d;color:#8b949e;border:1px solid #30363d;border-radius:8px;"
            "font-size:13px;font-weight:700;padding:7px 13px;}"
            f"QPushButton:hover{{border-color:{tone};background:#30363d;color:#c9d1d9;}}"
            f"QPushButton:checked{{border:1px solid {tone};background:#1f6feb33;color:{tone};}}"
            "QPushButton:disabled{background:#161b22;color:#484f58;border-color:#21262d;}"
        ))
        return button

    def options(self) -> RewindExecutionOptions:
        qualities = tuple(value for value, button in self._quality_buttons.items() if button.isChecked())
        if not qualities:
            qualities = ("gold",)
        selected = self._customization.checkedButton()
        customization = str(selected.property("rewindValue")) if selected is not None else "none"
        return RewindExecutionOptions(qualities=qualities, drive_customization=customization)

    def _sync_customization_availability(self, _checked: bool | None = None) -> None:
        custom_quality_selected = any(
            self._quality_buttons[quality].isChecked() for quality in ("purple", "gold")
        )
        for value in ("enabled", "apply_plan"):
            self._customization_buttons[value].setEnabled(custom_quality_selected)
        if not custom_quality_selected:
            self._customization_buttons["none"].setChecked(True)

    def _show_customization_help(self) -> None:
        QMessageBox.information(
            self,
            "드라이브 맞춤 설명",
            "· 아니요: 무작위 드라이브를 사용하며 10연은 고정 600 소모.\n"
            "· 예, 변경 없음: 게임 내 현재 맞춤 후보를 그대로 사용.\n"
            "· 예, 방안 적용: 저장된 8슬롯 추천을 먼저 게임에 기록한 뒤 인식한 가격으로 10연.\n\n"
            "초급 난이도에는 드라이브 맞춤이 없어 파란색 품질만 선택하면 “아니요”만 선택할 수 있습니다.",
        )


class RewindShapeReplacementDialog(QDialog):
    """Choose one of the 12 drive shapes for the clicked candidate slot."""

    def __init__(
        self,
        parent,
        *,
        candidates: tuple[RewindShapeRecommendation, ...],
        current_shape_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("드라이브 후보 추가")
        self.setFixedWidth(760)
        self._candidates = candidates
        self._selected_shape_id = current_shape_id or (candidates[0].shape.shape_id if candidates else "")

        root = QVBoxLayout(self)
        root.addWidget(QLabel("12종 드라이브 중 선택하고 확인하면 현재 후보 슬롯에 들어갑니다:"))
        grid = QGridLayout()
        grid.setContentsMargins(0, 8, 0, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        by_size = {
            size: [candidate for candidate in candidates if candidate.shape.cell_count == size]
            for size in (2, 3, 4)
        }
        for row, size in enumerate((2, 3, 4)):
            label = QLabel(f"{size}형 드라이브")
            label.setObjectName("rewindShapeGroupLabel")
            label.setFixedWidth(88)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setStyleSheet(themed_style("color:#8b949e;font-weight:800;padding:3px 6px"))
            grid.addWidget(label, row, 0)
            for column, candidate in enumerate(by_size[size], start=1):
                option_box = QWidget()
                option_layout = QVBoxLayout(option_box)
                option_layout.setContentsMargins(0, 0, 0, 0)
                option_layout.setSpacing(2)
                button = QToolButton()
                button.setObjectName("rewindShapeReplacementOption")
                button.setCheckable(True)
                button.setChecked(candidate.shape.shape_id == self._selected_shape_id)
                button.setText("")
                button.setToolTip(
                    f"{candidate.shape.cell_count}형 드라이브 · 보유 {candidate.owned_count}"
                )
                button.setToolButtonStyle(Qt.ToolButtonIconOnly)
                pixmap = warehouse_shape_pixmap(candidate.shape.shape_id, "Gold")
                if not pixmap.isNull():
                    button.setIcon(QIcon(pixmap))
                    button.setIconSize(QSize(42, 42))
                button.setProperty("shapeId", candidate.shape.shape_id)
                button.setFixedSize(96, 58)
                button.setStyleSheet(themed_style(
                    "QToolButton{background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:8px;}"
                    "QToolButton:hover{border-color:#58a6ff;background:#1f6feb33;}"
                    "QToolButton:checked{border:2px solid #58a6ff;background:#0d1f35;color:#f0f6fc;}"
                ))
                button.clicked.connect(
                    lambda _checked=False, shape_id=candidate.shape.shape_id: self._set_selected(shape_id)
                )
                option_layout.addWidget(button, 0, Qt.AlignHCenter)
                stock_label = QLabel(f"보유 {candidate.owned_count}")
                stock_label.setObjectName("rewindShapeReplacementStock")
                stock_label.setProperty("shapeId", candidate.shape.shape_id)
                stock_label.setAlignment(Qt.AlignCenter)
                stock_label.setWordWrap(False)
                stock_label.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
                option_layout.addWidget(stock_label)
                grid.addWidget(option_box, row, column)
        root.addLayout(grid)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected(self) -> RewindShapeRecommendation | None:
        return next((candidate for candidate in self._candidates if candidate.shape.shape_id == self._selected_shape_id), None)

    def _set_selected(self, shape_id: str) -> None:
        self._selected_shape_id = shape_id
        for button in self.findChildren(QToolButton, "rewindShapeReplacementOption"):
            button.setChecked(str(button.property("shapeId")) == shape_id)
