# 提供战报包多选、账号命名及导入导出弹窗。

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_report_transfer import BattleReportTransferEntry


def _local_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


class BattleReportTransferDialog(QDialog):
    account_name_save_requested = Signal(str)
    export_requested = Signal(object)
    import_requested = Signal()

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selectors: dict[int, QCheckBox] = {}
        self.setWindowTitle("전투 리포트 팩 내보내기 / 불러오기")
        self.setMinimumSize(900, 520)
        self.resize(1080, 680)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("전투 리포트 팩")
        title.setStyleSheet(themed_style(
            "font-size:18px;font-weight:700;color:#f0f6fc"
        ))
        title_row.addWidget(title)
        title_row.addStretch()
        self.count_label = QLabel("내보내기 가능 0건")
        self.count_label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        title_row.addWidget(self.count_label)
        layout.addLayout(title_row)

        description = QLabel(
            "앱 전용의 무결성 검증이 포함된 압축 .ntebr 파일로 내보냅니다. 불러올 때는 검증·압축 해제 후"
            "하나의 트랜잭션으로 현재 계정의 전투 리포트 데이터베이스에 가져옵니다."
        )
        description.setWordWrap(True)
        description.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(description)

        account_row = QHBoxLayout()
        account_row.addWidget(QLabel("현재 계정 닉네임"))
        self.account_name_edit = QLineEdit()
        self.account_name_edit.setPlaceholderText("계정 닉네임은 비워 둘 수 없습니다")
        self.account_name_edit.setClearButtonEnabled(True)
        account_row.addWidget(self.account_name_edit, 1)
        self.save_name_button = QPushButton("닉네임 저장")
        self.save_name_button.setObjectName("btnAction")
        self.save_name_button.clicked.connect(
            lambda: self.account_name_save_requested.emit(
                self.account_name_edit.text()
            )
        )
        account_row.addWidget(self.save_name_button)
        layout.addLayout(account_row)

        selection_row = QHBoxLayout()
        self.select_all_button = QPushButton("전체 선택")
        self.clear_selection_button = QPushButton("선택 해제")
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.clear_selection_button.clicked.connect(
            lambda: self._set_all_checked(False)
        )
        selection_row.addWidget(self.select_all_button)
        selection_row.addWidget(self.clear_selection_button)
        selection_row.addStretch()
        layout.addLayout(selection_row)

        self.empty_label = QLabel("현재 계정에는 아직 내보낼 수 있는 전투 리포트 기록이 없습니다.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(themed_style(
            "color:#8b949e;font-size:13px;padding:28px"
        ))
        layout.addWidget(self.empty_label)

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(
            ("선택", "수집 시간", "게임 모드", "범위", "완전성 / cursor", "보존 상태", "전투 리포트 ID")
        )
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        for column in (0, 1, 2, 3, 5, 6):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 168)
        self.table.setColumnWidth(2, 170)
        self.table.setColumnWidth(3, 145)
        self.table.setColumnWidth(5, 90)
        self.table.setColumnWidth(6, 80)
        layout.addWidget(self.table, 1)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(themed_style(
            "color:#ff7b72;background:#da363322;border:1px solid #da3633;"
            "border-radius:6px;padding:7px"
        ))
        self.error_label.hide()
        layout.addWidget(self.error_label)

        actions = QHBoxLayout()
        self.import_button = QPushButton("전투 리포트 패키지 읽기")
        self.import_button.clicked.connect(self.import_requested)
        actions.addWidget(self.import_button)
        actions.addStretch()
        self.export_button = QPushButton("선택한 전투 리포트 내보내기")
        self.export_button.setObjectName("btnPrimary")
        self.export_button.clicked.connect(
            lambda: self.export_requested.emit(self.selected_report_ids())
        )
        actions.addWidget(self.export_button)
        close_button = QPushButton("닫기")
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        layout.addLayout(actions)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        fit_dialog_to_available_screen(self, QSize(1080, 680))

    def set_account_name(self, value: str) -> None:
        self.account_name_edit.setText(value)
        self.account_name_edit.setModified(False)

    def set_entries(self, entries: tuple[BattleReportTransferEntry, ...]) -> None:
        self._selectors.clear()
        self.table.setRowCount(len(entries))
        self.table.setVisible(bool(entries))
        self.empty_label.setVisible(not entries)
        self.count_label.setText(f"내보내기 가능 {len(entries)}건")
        self.export_button.setEnabled(bool(entries))
        self.select_all_button.setEnabled(bool(entries))
        self.clear_selection_button.setEnabled(bool(entries))
        for row, entry in enumerate(entries):
            selector_cell, selector = self._selector_widget()
            self._selectors[entry.battle_record_id] = selector
            self.table.setCellWidget(row, 0, selector_cell)
            self.table.setItem(row, 1, self._item(_local_time(entry.captured_at_utc)))
            self.table.setItem(row, 2, self._item(entry.gameplay_label))
            self.table.setItem(row, 3, self._item(entry.scope_label))
            completeness = (
                f"{entry.completeness_label} · {entry.cursor_label} · "
                f"요약 {entry.total_hits} 히트"
            )
            self.table.setItem(row, 4, self._item(completeness))
            self.table.setItem(row, 5, self._item(entry.retention_label))
            self.table.setItem(row, 6, self._item(str(entry.battle_record_id)))
            self.table.setRowHeight(row, 42)

    def selected_report_ids(self) -> tuple[int, ...]:
        return tuple(
            report_id
            for report_id, selector in self._selectors.items()
            if selector.isChecked()
        )

    def has_unsaved_account_name(self) -> bool:
        return self.account_name_edit.isModified()

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def clear_error(self) -> None:
        self.error_label.clear()
        self.error_label.hide()

    def set_busy(self, busy: bool) -> None:
        for widget in (
            self.save_name_button,
            self.import_button,
            self.account_name_edit,
            self.table,
        ):
            widget.setEnabled(not busy)
        has_entries = self.table.rowCount() > 0
        self.select_all_button.setEnabled(not busy and has_entries)
        self.clear_selection_button.setEnabled(not busy and has_entries)
        self.export_button.setEnabled(not busy and has_entries)

    @staticmethod
    def _item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return item

    @staticmethod
    def _selector_widget() -> tuple[QWidget, QCheckBox]:
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        selector = QCheckBox(cell)
        layout.addWidget(selector, 0, Qt.AlignCenter)
        return cell, selector

    def _set_all_checked(self, checked: bool) -> None:
        for selector in self._selectors.values():
            selector.setChecked(checked)


__all__ = ["BattleReportTransferDialog"]
