# 在战报页面视口底部固定展示按需分析状态。
"""Pinned full-width progress footer for lazy battle analysis."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QWidget,
)

from src.app.theme import themed_style
from src.services.battle_analysis_progress import BattleAnalysisProgress


_DETAIL_COPY = {
    "hit": "현재 범위의 버프 구간, 피해 곱연산 구간, 히트별 공식을 다시 구성하는 중…",
    "buff": "버프 구간을 추론하고 항목별 제거 반사실을 계산하는 중…",
    "marginal": "현재 적용 중인 기준선을 구체화하고 고정 축 대조를 생성하는 중…",
    "composition": "브레이크 캐릭터별 공식을 리플레이하고 피해 구성을 갱신하는 중…",
}


class BattleAnalysisProgressBar(QFrame):
    """Stay below the page stack instead of moving with scroll contents."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("battleAnalysisProgress")
        self.setStyleSheet(themed_style(
            "QFrame#battleAnalysisProgress{"
            "background:#161b22;border-top:1px solid #30363d;}"
            "QLabel{color:#c9d1d9;font-size:12px;font-weight:600;}"
        ))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 8, 18, 8)
        layout.setSpacing(14)
        self.message_label = QLabel()
        self.message_label.setMinimumWidth(360)
        layout.addWidget(self.message_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress, 1)
        self.setFixedHeight(48)
        self.hide()

    def show_for(self, kind: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(1)
        self.progress.setFormat('%p%')
        self.progress.setTextVisible(True)
        self.message_label.setText(
            _DETAIL_COPY.get(kind, "현재 범위의 전투 리포트 분석을 다시 구성하는 중…")
        )
        self.show()

    def update_progress(self, progress: BattleAnalysisProgress) -> None:
        message = progress.message
        if progress.overall_percent is not None:
            self.progress.setRange(0, 100)
            self.progress.setValue(max(1, min(100, progress.overall_percent)))
            self.progress.setFormat('%p%')
            self.progress.setTextVisible(True)
            if progress.determinate:
                message = f'{message} ({progress.completed}/{progress.total})'
            self.progress.setToolTip('전체 진행률은 단계별 작업량으로 추정합니다. 실제로 완료된 뒤에 진행되며, 전부 완료되어야 100%가 됩니다.')
        elif progress.determinate:
            assert progress.completed is not None
            assert progress.total is not None
            completed = max(0, min(progress.completed, progress.total))
            self.progress.setRange(0, progress.total)
            self.progress.setValue(completed)
            self.progress.setFormat("현재 단계 %v / %m")
            self.progress.setTextVisible(True)
            message = f"{message} ({completed}/{progress.total})"
        else:
            self.progress.setRange(0, 0)
            self.progress.setTextVisible(False)
        self.message_label.setText(message)
        self.show()

    def finish(self) -> None:
        self.hide()
