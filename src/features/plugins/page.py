# 展示紧凑的游戏内显示卡片，并直接编辑各插件的显示选项。
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from src.app.theme import theme_color


class _ToggleSwitch(QCheckBox):
    """Paint a compact switch while retaining checkbox keyboard semantics."""

    def sizeHint(self) -> QSize:
        return QSize(112, 30)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(1.0 if self.isEnabled() else 0.5)
        track = QRectF(0, 4, 46, 22)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme_color("#1f6feb" if self.isChecked() else "#30363d")))
        painter.drawRoundedRect(track, 11, 11)
        knob_x = 26 if self.isChecked() else 4
        painter.setBrush(QColor(theme_color("#f0f6fc")))
        painter.drawEllipse(QRectF(knob_x, 7, 16, 16))
        painter.setPen(QColor(theme_color("#c9d1d9")))
        painter.drawText(QRectF(56, 0, self.width() - 56, self.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, self.text())


class PluginsPage(QWidget):
    def __init__(self, *, service, request_apply, open_settings, parent=None):
        super().__init__(parent)
        self.service, self.request_apply, self.open_settings = service, request_apply, open_settings
        self.cards = {}
        self.option_boxes = {}
        self._refresh_pending = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        hint = QLabel("게임 내에 보조 정보를 표시하며, 전투 리포트에는 영향을 주지 않습니다. 설정은 모든 계정에 적용됩니다.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{theme_color('#c9d1d9')}")
        layout.addWidget(hint)

        self._add_card(
            layout, "cooldown", "스킬 재사용 대기시간", "파티 스킬 카운트다운과 재사용 대기시간 오버레이를 표시합니다.",
            (("ready_cue", "E/Q 준비 알림"),),
        )
        self._add_card(
            layout, "enemy_bars", "적 상태", "적의 전투 상태를 표시합니다.",
            (("hp", "HP"), ("unbalance", "倾陷")),
        )

        self.notice = QLabel("Calc 종료 후 표시가 중지됩니다; 다음 시작 시 저장된 설정이 복원됩니다.")
        self.notice.setObjectName("pluginPageNotice")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:12px")
        layout.addWidget(self.notice)

        actions = QHBoxLayout()
        self.refresh_button = QPushButton("상태 새로고침")
        self.refresh_button.clicked.connect(self._request_refresh)
        self.environment_button = QPushButton("검사 및 배포")
        self.environment_button.clicked.connect(self._open_environment)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.environment_button)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        self.refresh()

    def _add_card(self, layout, key: str, title: str, description: str, fields) -> None:
        frame = QFrame()
        frame.setObjectName("card")
        card = QVBoxLayout(frame)
        card.setContentsMargins(20, 14, 20, 14)
        card.setSpacing(9)

        row = QHBoxLayout()
        row.setSpacing(10)
        heading = QLabel(title)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        row.addWidget(heading)
        status = QLabel()
        status.setObjectName("statusBadge")
        row.addWidget(status)
        row.addStretch()
        toggle = _ToggleSwitch()
        toggle.setObjectName(f"plugin_{key}")
        toggle.toggled.connect(lambda enabled, key=key: self._update(**{key: enabled}))
        row.addWidget(toggle)
        card.addLayout(row)

        label = QLabel(description)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{theme_color('#8b949e')}")
        card.addWidget(label)
        options = QHBoxLayout()
        options.setSpacing(24)
        boxes = {}
        for field, text in fields:
            box = QCheckBox(text)
            box.toggled.connect(lambda enabled, field=field: self._update(**{field: enabled}))
            options.addWidget(box)
            boxes[field] = box
        options.addStretch()
        card.addLayout(options)
        self.cards[key] = toggle, status
        self.option_boxes[key] = boxes
        layout.addWidget(frame)

    @staticmethod
    def _status_presentation(raw: str, checked: bool) -> tuple[str, str]:
        if not checked:
            if raw == "표시 끄기 확인 대기":
                return "닫는 중", "active"
            if raw == "표시 끄기 미확인, 연결 복구 대기 중":
                return "끄기 확인 대기", "warning"
            return "꺼짐", "neutral"
        if raw == "게임 대기 중":
            return "게임 시작 대기 중", "warning"
        if raw == "실행 중":
            return "표시 중", "success"
        if raw == "조작 가능한 장면 대기 중":
            return "조작 가능한 장면 진입 대기 중", "active"
        if raw == "설정 저장됨, 적용 대기 중":
            return "적용하는 중", "active"
        if raw == "미적용":
            return "연결 대기 중", "active"
        if raw in {"현재 모드에서 활성화 불가", "연결 일시 중지됨"}:
            return raw, "warning"
        if "미지원" in raw or "구성 패키지 업데이트" in raw or "업데이트 필요" in raw:
            return "컴포넌트 업데이트 필요", "error"
        if "실패" in raw or raw:
            return "연결 이상", "error"
        return "연결 대기 중", "active"

    @staticmethod
    def _set_badge(status: QLabel, text: str, tone: str, detail: str) -> None:
        status.setText(text)
        status.setToolTip(detail if detail and detail != text else "")
        status.setProperty("tone", tone)
        status.style().unpolish(status)
        status.style().polish(status)

    def refresh(self, result=None):
        allowed = self.service.policy.allowed("native_load")
        settings = self.service.settings
        for key, (toggle, status) in self.cards.items():
            checked = getattr(settings, key)
            raw_status = self.service.status_for(key)
            toggle.blockSignals(True)
            toggle.setChecked(checked)
            toggle.setText("켜짐" if checked else "꺼짐")
            toggle.setEnabled(allowed or checked)
            toggle.blockSignals(False)
            text, tone = self._status_presentation(raw_status, checked)
            self._set_badge(status, text, tone, raw_status)
            for field, box in self.option_boxes[key].items():
                box.blockSignals(True)
                box.setChecked(getattr(settings, field))
                box.setEnabled(allowed)
                box.blockSignals(False)

        active_statuses = [
            self.service.status_for(key) for key in self.cards
            if getattr(settings, key)
        ]
        known_statuses = {
            "", "미적용", "게임 대기 중", "실행 중", "조작 가능한 장면 대기 중",
            "설정 저장됨, 적용 대기 중", "현재 모드에서 활성화 불가", "연결 일시 중지됨",
        }
        self.notice.setText(
            self.service.load_error
            or next((status for status in active_statuses if status not in known_statuses), "")
            or ("Calc 종료 후 표시가 중지됩니다; 다음 시작 시 저장된 설정이 복원됩니다." if allowed else
                "현재 모드는 플러그인을 지원하지 않습니다; 중위험 또는 개발 모드로 전환해 주세요.")
        )
        self.environment_button.setText("검사 및 배포" if allowed else "작업 모드 설정")
        if result is not None and self._refresh_pending:
            self._refresh_pending = False
            self.refresh_button.setText("상태 새로고침")
            self.refresh_button.setEnabled(True)

    def _request_refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.refresh_button.setText("새로고침 중…")
        self.refresh_button.setEnabled(False)
        self.request_apply()

    def _open_environment(self) -> None:
        target = "deployment" if self.service.policy.allowed("native_load") else "mode"
        self.open_settings(target)

    def _update(self, **changes):
        projected_hp = changes.get("hp", self.service.settings.hp)
        projected_unbalance = changes.get("unbalance", self.service.settings.unbalance)
        if changes.get("enemy_bars") is True and not (projected_hp or projected_unbalance):
            changes["hp"] = True
            projected_hp = True
        if (self.service.settings.enemy_bars and not (projected_hp or projected_unbalance)
                and ("hp" in changes or "unbalance" in changes)):
            changes["enemy_bars"] = False
        try:
            self.service.update(**changes)
        except (PermissionError, OSError, ValueError) as error:
            QMessageBox.warning(self, "플러그인 설정", str(error))
        else:
            self.request_apply()
        self.refresh()
