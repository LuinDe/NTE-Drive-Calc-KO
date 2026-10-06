# 展示简洁的旧版组件升级步骤，技术证据按需展开。
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from src.app.theme import theme_color
from src.app.window_geometry import fit_dialog_to_available_screen


_STEP_LABELS = ("기존 컴포넌트 확인", "기존 배포 정리", "작업 모드 확인", "현재 컴포넌트 배포 (필요 시)")
_STAGES = {
    "cleanup": (1, "이전 버전 컴포넌트 발견", "이전 배포가 현재 버전과 일치하지 않습니다.", "게임을 완전히 종료하세요; Loader 사용자는 런처도 종료해야 합니다. 그다음 이전 컴포넌트를 정리하세요.", "기존 컴포넌트 정리"),
    "path": (0, "게임 위치 확인 필요", "이전 배포의 게임 경로가 아직 확인되지 않았습니다.", "환경 설정으로 이동해 현재 게임의 HTGame.exe를 선택한 뒤 다시 확인하세요.", "환경 설정으로 이동"),
    "mode": (2, "구버전 컴포넌트 정리 완료", "정리하면 동기화가 일시 중지되며, 기존 계정 데이터는 그대로 유지됩니다.", "작업 모드 설정으로 이동해 필요한 모드를 다시 선택하고 확인하세요. 기존 모드를 계속 사용하더라도 확인이 필요합니다.", "작업 모드 확인"),
    "deploy": (3, "현재 컴포넌트 배포 준비", "작업 모드가 확인되었습니다. 컴포넌트는 아직 배포 대기 중입니다.", "환경 설정으로 이동해 선택한 D3D 또는 Loader 방식으로 배포하세요. 배포 전에 게임을 종료하고, Loader는 런처도 종료해야 합니다. 배포 후 검사 상세에서 동기화 상태를 확인할 수 있습니다.", "배포로 이동"),
    "done": (2, "정리 완료", "현재 모드에서는 네이티브 컴포넌트를 배포할 필요가 없습니다.", "동기화가 필요하면 작업 공간의 안내에 따라 동기화를 켜세요.", "가이드 완료"),
}


class ComponentUpgradeDialog(QDialog):
    def __init__(self, parent, *, stage: str, detail: str, method: str,
                 on_action):
        super().__init__(parent)
        self.setObjectName("componentUpgradeDialog")
        self.setWindowTitle("구버전 플러그인 업그레이드 안내")
        self.setWindowModality(Qt.WindowModal)
        self._on_action = on_action
        self._stage = stage
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        self.steps = QLabel(self)
        self.steps.setObjectName("componentUpgradeSteps")
        self.steps.setWordWrap(True)
        layout.addWidget(self.steps)
        self.title = QLabel(self)
        self.title.setObjectName("componentUpgradeTitle")
        self.title.setStyleSheet(f"color:{theme_color('#f0f6fc')};font-size:17px;font-weight:700")
        layout.addWidget(self.title)
        self.reason = QLabel(self)
        self.reason.setWordWrap(True)
        layout.addWidget(self.reason)
        self.next_step = QLabel(self)
        self.next_step.setWordWrap(True)
        layout.addWidget(self.next_step)
        self.technical_toggle = QPushButton("확인 근거 보기 ▾", self)
        self.technical_toggle.setObjectName("componentUpgradeTechnicalToggle")
        self.technical_toggle.setFlat(True)
        self.technical_toggle.setFocusPolicy(Qt.StrongFocus)
        layout.addWidget(self.technical_toggle)
        self.technical = QLabel(detail or "이번에는 추가 진단 정보가 없습니다.", self)
        self.technical.setObjectName("componentUpgradeTechnical")
        self.technical.setTextFormat(Qt.PlainText)
        self.technical.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.technical.setWordWrap(True)
        self.technical.hide()
        layout.addWidget(self.technical)
        self.technical_toggle.clicked.connect(self._toggle_technical)
        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel = QPushButton("나중에 처리", self)
        self.cancel.setDefault(True)
        self.cancel.clicked.connect(self.reject)
        actions.addWidget(self.cancel)
        self.action = QPushButton(self)
        self.action.setObjectName("btnNew")
        self.action.clicked.connect(self._act)
        actions.addWidget(self.action)
        layout.addLayout(actions)
        self.show_stage(stage, method=method)
        fit_dialog_to_available_screen(self, QSize(600, 310))

    def _toggle_technical(self):
        visible = not self.technical.isVisible()
        self.technical.setVisible(visible)
        self.technical_toggle.setText("확인 근거 접기 ▴" if visible else "확인 근거 보기 ▾")
        fit_dialog_to_available_screen(self, QSize(600, 400 if visible else 310))

    def show_stage(self, stage: str, *, method: str) -> None:
        self._stage = stage
        index, title, reason, next_step, action = _STAGES[stage]
        self.steps.setText("  ›  ".join(
            (f"● {label}" if position == index else f"○ {label}")
            for position, label in enumerate(_STEP_LABELS)
        ))
        self.title.setText(title)
        self.reason.setText("원인: " + reason)
        if stage == "deploy":
            next_step = next_step.replace("D3D 또는 Loader", "Loader" if method == "loader" else "D3D")
        self.next_step.setText("다음 단계: " + next_step)
        self.action.setText(action)

    def _act(self) -> None:
        stage = self._stage
        self.accept()
        self._on_action(stage)
