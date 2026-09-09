# 游戏资料库来源追溯组件；发行 payload 省略时只展示保留元数据。
"""Source provenance detail widget for the static game catalog."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.services.static_catalog_misc_service import (
    CatalogRelationPage,
    SourceTrace,
)


class SourceTraceDetail(QWidget):
    """Show retained provenance and page source-row metadata on demand."""

    load_more_requested = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_file_id: int | None = None
        self._next_offset = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.title_label = QLabel("출처 추적")
        self.title_label.setObjectName("cardTitle")
        layout.addWidget(self.title_label)
        self.explanation_label = QLabel("출처 식별자가 있는 자료를 선택하세요.")
        self.explanation_label.setWordWrap(True)
        layout.addWidget(self.explanation_label)
        self.metadata_group = QGroupBox("보존된 출처 메타데이터")
        self.metadata_form = QFormLayout(self.metadata_group)
        self.metadata_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        layout.addWidget(self.metadata_group)
        self.rows_group = QGroupBox("출처 행")
        self.rows_layout = QVBoxLayout(self.rows_group)
        self.rows_placeholder = QLabel("“출처 행 불러오기”를 클릭하면 페이지 단위로 읽습니다.")
        self.rows_layout.addWidget(self.rows_placeholder)
        layout.addWidget(self.rows_group)
        self.load_more_button = QPushButton("출처 행 불러오기")
        self.load_more_button.setObjectName("btnAction")
        self.load_more_button.clicked.connect(self._request_more)
        layout.addWidget(self.load_more_button)
        layout.addStretch(1)

    def render_trace(self, trace: SourceTrace) -> None:
        self._clear_form()
        self._clear_rows()
        self._source_file_id = trace.source_file_id
        self._next_offset = 0
        self.explanation_label.setText(trace.explanation)
        fields = (
            ("출처 경로", trace.relative_path, True),
            ("파일 SHA-256", trace.source_file_sha256, True),
            ("선언된 행 수", str(trace.declared_row_count), False),
            ("출처 행 ID", self._optional(trace.source_row_id), False),
            ("출처 행 key", self._optional(trace.row_key), True),
            ("콘텐츠 SHA-256", self._optional(trace.content_sha256), True),
            ("원본 payload", "배포 패키지에서 생략됨" if trace.payloads_omitted else "이 페이지에 표시되지 않음", False),
        )
        for label, value, copyable in fields:
            self.metadata_form.addRow(label, self._value_widget(value, copyable))
        self.load_more_button.setEnabled(trace.declared_row_count > 0)
        self.load_more_button.setText("출처 행 불러오기")

    def append_rows(self, page: CatalogRelationPage) -> None:
        if page.offset == 0:
            self._clear_rows()
        for section in page.rows:
            group = QGroupBox(section.title)
            form = QFormLayout(group)
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            for field in section.fields:
                form.addRow(
                    field.label,
                    self._value_widget(field.value, field.copy_kind is not None),
                )
            self.rows_layout.addWidget(group)
        self._next_offset = page.offset + len(page.rows)
        self.load_more_button.setEnabled(page.has_more)
        self.load_more_button.setText("다음 페이지 불러오기" if page.has_more else "모든 출처 행을 불러왔습니다")

    def _request_more(self) -> None:
        if self._source_file_id is not None:
            self.load_more_requested.emit(self._source_file_id, self._next_offset)

    def _clear_form(self) -> None:
        while self.metadata_form.rowCount():
            self.metadata_form.removeRow(0)

    def _clear_rows(self) -> None:
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _optional(value: object | None) -> str:
        return "—" if value is None else str(value)

    @staticmethod
    def _value_widget(value: str, copyable: bool) -> QWidget:
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(value)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label, 1)
        marker = QLabel("출처 메타데이터")
        marker.setObjectName("statusBadge")
        marker.setProperty("tone", "neutral")
        layout.addWidget(marker, 0, Qt.AlignTop)
        if copyable and value != "—":
            button = QPushButton("복사")
            button.setObjectName("btnSm")
            button.clicked.connect(
                lambda _checked=False, text=value: QApplication.clipboard().setText(text)
            )
            layout.addWidget(button, 0, Qt.AlignTop)
        return host
