# 展示本次 DLL 与抓包采集的独立结果，明确计时口径和差值分母。
from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout

from src.domain.battle_capture_comparison import BattleCaptureComparisonState
from src.domain.battle_report import BattleCaptureState


_CLOCK_NAMES = {"wall_clock": "실제 경과 시간", "subtract_time_stop": "정지 시간 제외"}
_PHASE_NAMES = {
    "idle": "시작 전", "starting": "시작 중", "running": "수집 중",
    "stopping": "종료 중", "stopped": "종료됨", "error": "수집 이상",
}


def _number(value: float, decimals: int = 0) -> str:
    return f"{value:,.{decimals}f}" if math.isfinite(value) else "—"


class BattleCaptureComparisonPanel(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        title = QLabel("이번 이중 경로 수집 대조", self)
        title.setObjectName("cardTitle")
        root.addWidget(title)
        self.notice = self._label()
        root.addWidget(self.notice)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        grid.addWidget(QLabel("DLL", self), 0, 1)
        grid.addWidget(QLabel("패킷 캡처", self), 0, 2)
        self.values: dict[str, dict[str, QLabel]] = {"native": {}, "packet": {}}
        for row, (key, name) in enumerate((
            ("damage", "총 피해"), ("records", "레코드 수"), ("duration", "지속 시간"),
            ("clock", "시간 측정 방식"), ("dps", "DPS"), ("status", "状态"),
            ("record_id", "전투 리포트 번호"),
        ), 1):
            grid.addWidget(QLabel(name, self), row, 0)
            for column, source in enumerate(("native", "packet"), 1):
                value = self._label()
                value.setObjectName(f"comparison_{source}_{key}")
                self.values[source][key] = value
                grid.addWidget(value, row, column)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        root.addLayout(grid)
        self.damage_difference = self._label()
        self.damage_ratio = self._label()
        self.dps_difference = self._label()
        for label in (self.damage_difference, self.damage_ratio, self.dps_difference):
            root.addWidget(label)
        self.set_snapshot(None)

    def _label(self) -> QLabel:
        label = QLabel(self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        return label

    def _render_source(self, source: str, state: BattleCaptureState) -> None:
        labels = self.values[source]
        summary = state.summary
        labels["damage"].setText(_number(summary.total_damage) if summary else "—")
        labels["records"].setText(f"{summary.total_hits:,}" if summary else "—")
        labels["duration"].setText(f"{_number(summary.duration_seconds, 2)}초" if summary else "—")
        labels["clock"].setText(
            _CLOCK_NAMES.get(summary.dps_time_mode, f"인식되지 않음({summary.dps_time_mode})")
            if summary else "샘플링 대기 중"
        )
        labels["dps"].setText(_number(summary.total_dps, 2) if summary else "—")
        status = _PHASE_NAMES.get(state.phase, state.phase)
        details = state.error or state.message
        labels["status"].setText(f"{status} · {details}" if details else status)
        labels["record_id"].setText(
            str(state.battle_record_id) if state.battle_record_id is not None else "아직 저장되지 않음"
        )

    def set_snapshot(self, snapshot: BattleCaptureComparisonState | None) -> None:
        if snapshot is None:
            for labels in self.values.values():
                for label in labels.values():
                    label.clear()
            for label in (self.notice, self.damage_difference, self.damage_ratio, self.dps_difference):
                label.clear()
            self.hide()
            return
        self.show()
        self._render_source("native", snapshot.native)
        self._render_source("packet", snapshot.packet)
        prefix = ("이번 대조가 중단됨" if snapshot.interrupted else
                  "이번 대조가 종료됨" if snapshot.finished else
                  "이번 대조를 종료하는 중" if snapshot.stop_requested else "이번 대조 진행 중")
        self.notice.setText(
            prefix + "; 두 경로는 독립적으로 기록되며 각각 자체 샘플링 구간을 사용합니다. 종료되었다고 소스가 완전히 커버되었다는 뜻은 아닙니다."
            "기록 수에는 피격 기록이 포함될 수 있으며, 피해를 입힌 히트 수와 같지 않습니다."
        )
        native, packet = snapshot.native.summary, snapshot.packet.summary
        if native is None or packet is None:
            self.damage_difference.setText("총 피해 차: 양측 샘플링 대기 중")
            self.damage_ratio.setText("DLL / 패킷 캡처 총 피해 비율: 양쪽 샘플링 대기 중")
            self.dps_difference.setText("DPS 차이: 양쪽 샘플링 대기 중")
            return
        difference = native.total_damage - packet.total_damage
        self.damage_difference.setText(f"수신된 총 피해 차이(DLL − 패킷 캡처): {_number(difference)}")
        ratio = native.total_damage / packet.total_damage if packet.total_damage > 0 else None
        self.damage_ratio.setText(
            f"DLL / 패킷 캡처 총 피해 비율: {_number(ratio * 100, 2)}%"
            if ratio is not None else "DLL / 패킷 캡처 총 피해 비율: 정의되지 않음 (패킷 캡처 총 피해가 0)"
        )
        if snapshot.interrupted or snapshot.native.error or snapshot.packet.error:
            self.dps_difference.setText("DPS 차이: 대조가 중단되었거나 이상이 있어 양쪽 실제 값 유지")
        elif any(state.phase not in {"running", "stopping", "stopped"} for state in (snapshot.native, snapshot.packet)):
            self.dps_difference.setText("DPS 차이: 양쪽 수집 준비 대기 중")
        elif native.dps_time_mode != packet.dps_time_mode:
            self.dps_difference.setText("DPS 차이: 시간 측정 방식이 달라 직접 비교하지 않음")
        elif native.dps_time_mode not in _CLOCK_NAMES or min(native.duration_seconds, packet.duration_seconds) <= 0:
            self.dps_difference.setText("DPS 차이: 양쪽 유효 시간 측정 대기 중")
        else:
            self.dps_difference.setText(
                f"DPS 차이 (DLL − 패킷 캡처, 각자 샘플링 구간): {_number(native.total_dps - packet.total_dps, 2)}"
            )
