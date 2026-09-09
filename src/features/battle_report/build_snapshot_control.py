# 展示战报原始角色配置与单一修改副本的切换状态。
"""Battle build edit control card."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

class BattleBuildSnapshotControl(QWidget):
    edit_requested = Signal()
    activation_requested = Signal(bool)
    role_page_import_requested = Signal()
    environment_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel("전투 리포트 캐릭터 설정 대기 중")
        self.status.hide()
        self.edit_button = QPushButton("캐릭터 편집")
        self.edit_button.clicked.connect(self.edit_requested)
        self.edit_button.setEnabled(False)
        layout.addWidget(self.edit_button)
        self.import_button = QPushButton("캐릭터 페이지에서 동기화")
        self.import_button.setToolTip(
            "현재 캐릭터 페이지의 육성으로 이번 전투의 수정 사본을 덮어씁니다. 사본에서 선택한 한계 이득용 콘솔/드라이브는 유지됩니다."
        )
        self.import_button.clicked.connect(self.role_page_import_requested)
        self.import_button.setEnabled(False)
        self.import_button.hide()
        self.environment_button = QPushButton("환경 설정 · 미설정")
        self.environment_button.setToolTip(
            "전투 모드, 대상, 난이도, 쟁봉 보너스, 마녀의 축복을 확인하세요."
        )
        self.environment_button.clicked.connect(self.environment_requested)
        self.environment_button.setEnabled(False)
        self.environment_button.hide()
        self.activation_button = QPushButton("원본 스냅샷 복원")
        self.activation_button.clicked.connect(self._request_activation)
        self.activation_button.setEnabled(False)
        self.activation_button.hide()

    def set_state(
        self,
        *,
        has_edit: bool,
        active: bool,
        available: bool = True,
    ) -> None:
        self.edit_button.setEnabled(available)
        self.import_button.setEnabled(available)
        self.activation_button.setEnabled(available and has_edit)
        if not available:
            text = "현재 기록에는 편집할 수 있는 캐릭터 설정 스냅샷이 없습니다."
        elif not has_edit:
            text = "현재 원본 스냅샷을 사용 중입니다. 처음 편집하면 이번 전투의 원본 고정 설정이 복사됩니다."
        elif active:
            text = "현재 수정 사본을 사용 중입니다. 수정 사본은 히트별 리플레이와 한계 이득 계산에만 사용되며 메인 페이지의 실측 데이터는 바뀌지 않습니다."
        else:
            text = "현재 원본 스냅샷으로 복원했습니다. 수정 사본은 그대로 남아 있어 계속 편집하거나 다시 활성화할 수 있습니다."
        self.status.setText(text)
        self.edit_button.setToolTip(text)
        self.activation_button.setText(
            "원본 스냅샷 복원" if active else "수정 사본 사용"
        )

    def set_environment_state(self, *, status: str, summary: str = "") -> None:
        labels = {
            "configured": "설정됨",
            "inferred": "추론됨",
            "unconfigured": "미설정",
        }
        label = labels.get(status, "미설정")
        self.environment_button.setText(f"환경 설정 · {label}")
        self.environment_button.setToolTip(
            summary or "전투 모드, 대상, 난이도, 쟁봉 보너스, 마녀의 축복을 확인하세요."
        )

    def _request_activation(self) -> None:
        self.activation_requested.emit(
            self.activation_button.text() == "수정 사본 사용"
        )
