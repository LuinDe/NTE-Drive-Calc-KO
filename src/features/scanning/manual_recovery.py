# 扫描解析待补录装备的人工修正。
"""Dialog helpers for completing partially parsed scan items."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.domain.stat_catalog import StatCatalog
def _stat_pool(config_dir: Path) -> list[str]:
    catalog = StatCatalog.from_config_dir(config_dir)
    pool = set(catalog.gold_base_values.keys())
    pool.update(catalog.tape_stat_values.keys())
    return sorted(stat for stat in pool if stat)


class ManualRecoveryDialog(QDialog):
    def __init__(self, records: list[dict], stat_names: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("미인식 스탯 보완 입력")
        self.resize(760, 520)
        self.records = list(records or [])
        self.stat_names = list(stat_names or [])
        self.rows = []

        root = QVBoxLayout(self)
        root.addWidget(QLabel("다음 장비는 유효한 서브 스탯 3개를 인식했습니다. 누락된 1개를 보완 입력한 뒤 등록하세요."))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(10)

        for index, record in enumerate(self.records, 1):
            content_layout.addWidget(self._build_record_group(index, record))
        content_layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        if ok_button:
            ok_button.setText("보완 입력 후 등록")
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        if cancel_button:
            cancel_button.setText("건너뛰기")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_record_group(self, index: int, record: dict) -> QGroupBox:
        item = dict(record.get("item") or {})
        item_type = "드라이브" if item.get("item_type") == "drive" else "카트리지"
        title = f"{index}. {record.get('filename', '')} · {item_type}"
        group = QGroupBox(title)
        layout = QVBoxLayout(group)

        meta = []
        if item.get("item_type") == "drive":
            meta.append(f"형태: {item.get('shape_id', '未知')}")
        else:
            meta.append(f"세트: {item.get('set_name', '未知')}")
            meta.append(f"메인 스탯: {item.get('main_stats', '未知')}")
        meta.append(f"품질: {item.get('quality', '未知')}")
        layout.addWidget(QLabel("；".join(meta)))

        stats = item.get("sub_stats") or {}
        stat_text = "；".join(f"{name} {value:g}" for name, value in stats.items()) or "없음"
        label = QLabel(f"인식됨: {stat_text}")
        label.setWordWrap(True)
        layout.addWidget(label)

        form = QFormLayout()
        combo = QComboBox()
        combo.setEditable(True)
        existing = set(stats.keys())
        for stat_name in self.stat_names:
            if stat_name not in existing:
                combo.addItem(stat_name)
        value = QDoubleSpinBox()
        value.setRange(-99999.0, 99999.0)
        value.setDecimals(3)
        value.setSingleStep(0.1)
        value.setValue(0.0)
        form.addRow("누락 스탯", combo)
        form.addRow("수치", value)
        layout.addLayout(form)

        preview_row = QHBoxLayout()
        preview_row.addStretch()
        path = str(record.get("image_path") or "")
        open_btn = QPushButton("스크린샷 열기")
        open_btn.setEnabled(bool(path))
        open_btn.clicked.connect(lambda _checked=False, p=path: self._open_path(p))
        preview_row.addWidget(open_btn)
        layout.addLayout(preview_row)

        self.rows.append((record, combo, value))
        return group

    def _open_path(self, path: str) -> None:
        if not path:
            return
        try:
            import os

            os.startfile(path)
        except Exception as exc:
            QMessageBox.warning(self, "스크린샷 열기 실패", str(exc))

    def completed_items(self) -> list[dict] | None:
        items = []
        for record, combo, value in self.rows:
            item = dict(record.get("item") or {})
            stats = dict(item.get("sub_stats") or {})
            stat_name = combo.currentText().strip()
            if not stat_name:
                QMessageBox.warning(self, "보완 입력 미완료", "누락된 스탯을 선택하세요.")
                return None
            if stat_name in stats:
                QMessageBox.warning(self, "보완 입력 중복", f"{stat_name}은(는) 이미 있습니다. 실제로 누락된 스탯을 선택하세요.")
                return None
            stats[stat_name] = float(value.value())
            item["sub_stats"] = stats
            items.append(item)
        return items

    def accept(self) -> None:
        if self.completed_items() is None:
            return
        super().accept()


def complete_pending_manual_items(parent, stats: dict, config_dir: Path) -> list[dict] | None:
    records = list((stats or {}).get("pending_manual_items") or [])
    if not records:
        return []
    dialog = ManualRecoveryDialog(records, _stat_pool(Path(config_dir)), parent)
    if dialog.exec() != QDialog.Accepted:
        return None
    items = dialog.completed_items() or []
    return items
