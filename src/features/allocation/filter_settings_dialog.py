# 编辑账号级分配候选过滤设置。
"""Multi-select allocation filter dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.app.dialogs import show_help
from src.app.theme import themed_style
from src.services.allocation_filter_settings import (
    AllocationFilterSettings,
    AllocationFilterValidationError,
)


_TILE_STYLE = (
    "QPushButton#allocationFilterTile{background:#21262d;color:#8b949e;"
    "border:1px solid #30363d;border-radius:7px;padding:7px 12px;font-weight:600;}"
    "QPushButton#allocationFilterTile:hover{background:#30363d;color:#c9d1d9;}"
    "QPushButton#allocationFilterTile:checked{background:#1f6feb33;color:#58a6ff;"
    "border-color:#58a6ff;}"
)

class AllocationFilterSettingsDialog(QDialog):
    """Edit a draft and publish it only when the user confirms a valid value."""

    def __init__(
        self,
        initial: AllocationFilterSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("분배 설정")
        self.setMinimumWidth(320)
        current = initial or AllocationFilterSettings()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(6)
        root.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        module = QGroupBox("필터 설정")
        module.setObjectName("allocationFilterModule")
        module.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        module_layout = QVBoxLayout(module)
        module_layout.setContentsMargins(8, 5, 8, 5)
        module_layout.setSpacing(3)

        module_help = QPushButton("?", module)
        module_help.setObjectName("allocationFilterHelp")
        module_help.setFixedSize(20, 20)
        module_help.setToolTip("필터 설정 설명 보기")
        module_help.clicked.connect(
            lambda _checked=False, parent=module_help: show_help(
                parent,
                "필터 설정 설명",
                "선택한 유형: 선택한 품질만 캐릭터 관리 필터에 포함되고 나머지 품질은 걸러집니다. 선택하지 않은 유형은 기본 규칙으로 처리됩니다.",
            )
        )
        module_help.move(
            36 + module.fontMetrics().horizontalAdvance(module.title()),
            0,
        )
        module_help.raise_()

        self.type_buttons = self._selection_row(
            module_layout,
            "분배 유형",
            (("카트리지", "tape"), ("드라이브", "drive")),
            current.item_types,
        )
        self.quality_buttons = self._selection_row(
            module_layout,
            "분배 품질",
            (("蓝色", "Blue"), ("紫色", "Purple"), ("금색", "Gold")),
            current.qualities,
        )
        root.addWidget(module)

        other_module = QGroupBox("기타 설정")
        other_module.setObjectName("allocationOtherModule")
        other_module.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        other_layout = QHBoxLayout(other_module)
        other_layout.setContentsMargins(8, 5, 8, 5)
        other_layout.setSpacing(6)
        other_layout.addWidget(QLabel("조합 수 상한"))
        self.combo_limit_edit = QLineEdit(str(current.blueprint_combo_limit))
        self.combo_limit_edit.setPlaceholderText("기본값 500")
        self.combo_limit_edit.setFixedHeight(36)
        other_layout.addWidget(self.combo_limit_edit, 1)
        other_help = QPushButton("?", other_module)
        other_help.setObjectName("btnHelp")
        other_help.setFixedSize(24, 24)
        other_help.setToolTip("조합 수 상한 설명 보기")
        other_help.clicked.connect(
            lambda _checked=False, parent=other_help: show_help(
                parent,
                "조합 수 상한 설명",
                "각 우선순위 그룹에서 최대로 평가할 청사진 조합 수입니다. 값이 클수록 계산은 충실해지지만 시간이 더 오래 걸립니다. 기본값은 500입니다.",
            )
        )
        other_layout.addWidget(other_help)
        root.addWidget(other_module)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_valid_settings)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.adjustSize()

    @staticmethod
    def _selection_row(
        parent_layout: QVBoxLayout,
        title: str,
        options: tuple[tuple[str, str], ...],
        selected: frozenset[str],
    ) -> dict[str, QPushButton]:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(title)
        label.setMinimumWidth(72)
        row.addWidget(label)
        buttons: dict[str, QPushButton] = {}
        for text, value in options:
            button = QPushButton(text)
            button.setObjectName("allocationFilterTile")
            button.setStyleSheet(themed_style(_TILE_STYLE))
            button.setCheckable(True)
            button.setChecked(value in selected)
            button.setProperty("filterValue", value)
            buttons[value] = button
            row.addWidget(button)
        row.addStretch(1)
        parent_layout.addLayout(row)
        return buttons

    def settings(self) -> AllocationFilterSettings:
        try:
            combo_limit = int(self.combo_limit_edit.text().strip())
        except ValueError as exc:
            raise AllocationFilterValidationError("조합 수 상한은 양의 정수여야 합니다.") from exc
        settings = AllocationFilterSettings(
            qualities=frozenset(
                value for value, button in self.quality_buttons.items() if button.isChecked()
            ),
            item_types=frozenset(
                value for value, button in self.type_buttons.items() if button.isChecked()
            ),
            blueprint_combo_limit=combo_limit,
        )
        settings.validate()
        return settings

    def _accept_valid_settings(self) -> None:
        try:
            self.settings()
        except AllocationFilterValidationError as exc:
            QMessageBox.warning(self, "분배 설정이 유효하지 않음", str(exc))
            return
        self.accept()
