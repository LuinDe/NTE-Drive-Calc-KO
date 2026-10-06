# 在设置中展示性能开关、服务耗时曲线和排错记录状态。
from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QDesktopServices, QPainter, QPen
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QTabWidget, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.settings.performance_trace_view import PerformanceTraceView


STATE_TEXT = {
    "off": "꺼짐", "waiting": "성능 데이터 대기 중; 네이티브 컴포넌트를 기다리며 동기화나 전투 리포트를 자동으로 시작하지 않습니다",
    "unsupported": "현재 컴포넌트가 서비스 소요 시간 집계를 제공하지 않습니다",
    "unavailable": "현재 모드 또는 일시 중지 상태에서는 네이티브 성능 관측을 할 수 없습니다",
    "collecting": "성능 관측 중",
    "fault": "성능 읽기 실패; 다음 샘플링 때 재시도",
}
SERVICE_NAMES = {"snapshot_pulse": "스냅샷 주기", "snapshot_read": "스냅샷 분할 읽기"}
SERVICE_NAMES.update({
    "snapshot.clock_roots_before": "전투 시계 · 조회 전 식별 검증",
    "snapshot.clock_function": "전투 시계 · 함수 검증",
    "snapshot.clock_queries": "전투 시계 · 단일 일시 중지 조회",
    "snapshot.clock_roots_after": "전투 시계 · 조회 후 식별 재확인",
})
_READ_STAGES = {
    "job_step": "읽기 배치", "step_precheck": "읽기 전 검증", "reader_step": "데이터 읽기",
    "step_postcheck": "읽기 후 검증", "character_init": "캐릭터 초기화", "character_validate": "캐릭터 검증",
    "forks": "아크", "equipment_index": "장비 인덱스", "character_rows": "캐릭터 항목",
    "character_base": "캐릭터 기본", "skills": "스킬", "skill_query": "스킬 레벨 조회",
    "awakening": "각성", "awakening_definitions": "각성 정의", "slots": "장비 슬롯",
    "other_fields": "기타 육성", "related_equipment": "연관 장비", "row_finalize": "항목 마무리",
    "verify": "무결성 재검증", "domain_roots": "데이터 루트 읽기", "domain_identity": "데이터 식별 정보", "domain_clone": "캐릭터 저장 데이터",
}
SERVICE_NAMES.update({prefix + key: owner + label for prefix, owner in
                      (("snapshot.", "수집 · "), ("user.snapshot.", "계정 읽기 · "))
                      for key, label in _READ_STAGES.items()})


class CostPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = []
        self.setMinimumHeight(130)
        self.setAccessibleName("서비스 평균 1회 소요 시간 곡선, 단위 밀리초")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(self.palette().text().color())
        values = [value for _, value in self.points if value is not None]
        if not values:
            painter.drawText(self.rect(), Qt.AlignCenter, "새 호출 대기 중; 샘플이 없으면 0으로 기록하지 않음")
            return
        maximum = max(max(values), 0.001)
        painter.drawText(8, 18, f"최근 120회 관측 · 평균 1회 소요 시간 · 세로축 상한 {maximum:.3f} ms")
        area = self.rect().adjusted(12, 30, -12, -16)
        first, last = self.points[0][0], self.points[-1][0]
        painter.setPen(QPen(self.palette().highlight().color(), 2))
        previous = None
        for timestamp, value in self.points:
            if value is None:
                previous = None
                continue
            point = QPointF(area.left() + area.width() * (timestamp-first) / max(last-first, 1),
                            area.bottom() - area.height() * value / maximum)
            if previous is not None:
                painter.drawLine(previous, point)
            painter.drawEllipse(point, 2, 2)
            previous = point


class PerformanceDetails(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.setWindowTitle("성능 상세")
        self.controller = controller
        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs)
        overview = QWidget()
        tabs.addTab(overview, '실시간 개요')
        self.trace_view = PerformanceTraceView(controller)
        tabs.addTab(self.trace_view, '세부 샘플링')
        layout = QVBoxLayout(overview)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.metrics = QLabel()
        self.metrics.setWordWrap(True)
        layout.addWidget(self.metrics)
        note = QLabel("FPS/프레임 시간은 게임이 화면 표시를 제출한 간격으로 측정합니다. 1% Low는 최근 30초 동안 느린 프레임의 평균입니다. "
                      "Calc 소요 시간은 현재 네이티브 디스패치와 HUD만 포함하며, 전체 컴포넌트 비용을 나타내지 않습니다. "
                      "아래 곡선은 서비스별 평균 1회 소요 시간이며, 중첩된 서비스는 합산할 수 없습니다.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.service = QComboBox()
        self.service.setAccessibleName("곡선 서비스")
        self.service.setPlaceholderText("사용 가능한 서비스 계측 대기 중")
        layout.addWidget(self.service)
        self.plot = CostPlot()
        layout.addWidget(self.plot)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["서비스", "구간 호출 수", "평균 1회 ms", "출처 누적 최대 ms"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self.path = QLabel()
        self.path.setWordWrap(True)
        self.path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.path)
        row = QHBoxLayout()
        folder = QPushButton("성능 로그 디렉터리 열기")
        folder.clicked.connect(self.open_logs)
        row.addWidget(folder)
        close = QPushButton("닫기")
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.service.currentIndexChanged.connect(lambda: self.render(controller.snapshot()))

    def open_logs(self):
        directory = self.controller.log_dir / "performance"
        if directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        else:
            self.path.setText("아직 성능 로그가 생성되지 않았습니다. 수집 문제 해결을 켜고 “성능도 함께 기록”을 선택한 뒤 저장하세요.")

    def showEvent(self, event):
        super().showEvent(event)
        fit_dialog_to_available_screen(self, QSize(820, 570))

    def render(self, value):
        self.trace_view.render(value.get('trace', {}))
        overlay = {"off": "오버레이 꺼짐", "closing": "표시 끄는 중", "visible": "오버레이 렌더링됨", "waiting": "표시 요청됨, 게임 렌더링 대기 중",
                   "unsupported": "현재 컴포넌트는 성능 오버레이를 지원하지 않습니다. 호환 컴포넌트 업데이트가 필요합니다", "rejected": "현재 게임 버전은 성능 렌더링을 지원하지 않습니다",
                   "unconfirmed": "오버레이 상태 미확인, 연결 복구 대기 중"}.get(value.get("overlay_state"), "컴포넌트 대기 중")
        summary = "4개 항목 오버레이와 해당 로그가 꺼졌습니다." if not value["enabled"] and value.get("overlay_state") == "off" else overlay + "; " + status_text(value)
        frame_error = value.get("frames", {}).get("frame_error")
        if value["enabled"] and frame_error:
            summary += "; " + frame_error
        self.status.setText(value.get("preference_error") or summary + trace_status_suffix(value))
        metrics = value.get("frames", {})
        def number(key):
            v = metrics.get(key)
            return f"{v / 1000:.2f}" if type(v) is int else "--"
        self.metrics.setText(f"FPS  {number('fps_milli')}    프레임 시간  {number('frame_us')} ms    "
                             f"1% Low  {number('low_milli')} FPS    Calc 소요 시간(측정 범위)  {number('cost_us')} ms/프레임")
        rows = value["rows"]
        selected = self.service.currentData()
        names = list(rows)
        if names != [self.service.itemData(i) for i in range(self.service.count())]:
            self.service.blockSignals(True)
            self.service.clear()
            for name in names:
                self.service.addItem(SERVICE_NAMES.get(name, name), name)
            self.service.setCurrentIndex(max(0, self.service.findData(selected)))
            self.service.blockSignals(False)
        self.service.setEnabled(bool(names))
        selected = self.service.currentData()
        self.plot.points = [(t, data.get(selected)) for t, data in value["history"]]
        self.plot.update()
        self.table.setRowCount(len(rows))
        for index, (name, row) in enumerate(rows.items()):
            cells = (SERVICE_NAMES.get(name, name),
                     str(row["interval_calls"]) if row["interval_calls"] is not None else "—",
                     f'{row["mean_ms"]:.3f}' if row["mean_ms"] is not None else "—",
                     f'{row["max_us"]/1000:.3f}' if row["calls"] else "—")
            for col, cell in enumerate(cells):
                self.table.setItem(index, col, QTableWidgetItem(cell))
        self.path.setText(value["log_error"] or value["log_path"] or "현재 기록하지 않음(실시간 보기는 파일로 자동 저장되지 않음). 기존 파일은 디렉터리에서 확인할 수 있습니다.")


def trace_status_suffix(value):
    trace = value.get('trace', {})
    if trace.get('running'):
        return '; 세부 샘플링 실행 중(별도 기록)'
    if trace.get('state') == 'saved':
        return '; 세부 샘플링 중지 및 저장됨'
    if trace.get('state') in {'failed', 'unconfirmed'}:
        return '; 세부 샘플링이 완전히 확인되지 않았습니다. 세부 샘플링 탭을 확인하세요'
    return ''


def status_text(value):
    text = STATE_TEXT[value["state"]]
    if value["automatic"]:
        text += " · 수집 문제 해결로 켜짐"
    if value["log_error"]:
        text += " · " + value["log_error"]
    return text


class PerformanceCard(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.dialog = None
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.toggle = QCheckBox("게임 내 성능 표시")
        self.toggle.setToolTip("FPS, 프레임 시간, 1% Low와 Calc 컴포넌트 소요 시간을 표시합니다. 동기화나 전투 리포트는 시작하지 않습니다.")
        self.toggle.clicked.connect(controller.set_enabled)
        row.addWidget(self.toggle)
        details = QPushButton("성능 상세…")
        details.clicked.connect(self.show_details)
        row.addWidget(details)
        row.addStretch()
        layout.addLayout(row)
        controller.changed.connect(self.render)
        self.render()

    def render(self):
        value = self.controller.snapshot()
        self.toggle.setChecked(value.get("overlay", False))
        if self.dialog and self.dialog.isVisible():
            self.dialog.render(value)

    def show_details(self):
        if self.dialog is None:
            self.dialog = PerformanceDetails(self.controller, self)
        self.dialog.render(self.controller.snapshot())
        self.dialog.show()
        self.dialog.raise_()
