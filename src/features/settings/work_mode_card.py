# 构建统一工作模式入口、逐功能检查结果与必要处理操作。
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QScrollArea, QVBoxLayout,
)
from src.app.theme import theme_color
from src.app.window_geometry import fit_dialog_to_available_screen
from src.app.version import __version__
from src.ui.widgets import NoWheelComboBox

MODE_LABELS = {"offline": "오프라인", "low": "저위험", "medium": "중위험", "developer": "개발"}
MODE_DESCRIPTIONS = {
    "offline": "로컬 계산, 장비 세팅, 저장된 데이터 및 과거 전투 리포트 분석.",
    "low": "위 기능 + 기본 동기화/패킷 캡처 + 마우스 또는 게임패드 스캔; 게임 구성 요소는 사용하지 않습니다.",
    "medium": "위 기능 + 네이티브 동기화, 네이티브 전투 리포트, 고속 장착, 잠금/폐기 및 플러그인.",
    "developer": "패킷 캡처와 네이티브 이중 경로 비교, 개발자 전용입니다.",
}
MODE_CONFIRMATIONS = {
    "offline": {
        "warning_title": "전환 후 게임 연결이 끊어집니다",
        "warning_detail": "수집과 동기화를 중지하고, 두 플러그인 스위치를 끕니다.",
        "available": "로컬 계산, 장비 세팅, 저장된 데이터, 과거 전투 리포트 분석",
        "unavailable": "데이터 동기화, 전투 리포트 수집, 게임 조작, 플러그인",
        "after": "백그라운드 연결을 중지합니다. 게임 종료 후 배포된 구성 요소를 정리합니다.",
        "note": "오프라인 모드는 게임에 연결하지 않습니다. 확인하면 이 모드가 저장됩니다.",
    },
    "low": {
        "warning_title": "이 모드에는 위험이 있습니다",
        "warning_detail": "패킷 캡처와 입력 시뮬레이션은 게임 보호 또는 호환성 문제를 유발할 수 있습니다.",
        "available": "기본 동기화/패킷 캡처, 마우스 또는 게임패드 스캔",
        "unavailable": "캐릭터 상태 동기화, 네이티브 동기화, 네이티브 전투 리포트, 고속 장착, 플러그인",
        "after": "네이티브 연결을 중지합니다. 게임 종료 후 배포된 구성 요소를 정리합니다.",
        "note": "자동 동기화는 여전히 작업대 스위치로 제어됩니다. 확인하면 이 모드가 저장되며, 이후 버전 업데이트에서도 계속 사용됩니다.",
    },
    "medium": {
        "warning_title": "이 모드에는 위험이 있습니다",
        "warning_detail": "게임 컴포넌트를 로드하고 게임 내 작업을 실행하므로, 게임 보호나 호환성 문제가 발생할 수 있습니다.",
        "available": "네이티브 동기화, 네이티브 전투 리포트, 고속 장착, 잠금/폐기, 플러그인",
        "unavailable": "기본 동기화/패킷 캡처, 이중 경로 전투 리포트 비교",
        "after": "동기화를 켜고 준비를 확인하면, 게임 종료 시 필요한 컴포넌트를 배포하거나 업데이트합니다.",
        "note": "모드 확인이 자동 동기화를 켜는 것은 아닙니다; 작업대를 처음 열면 먼저 환경 검사가 표시됩니다. 모드 선택은 저장됩니다.",
    },
    "developer": {
        "warning_title": "개발자 전용",
        "warning_detail": "패킷 캡처와 네이티브 이중 경로를 동시에 실행하면 게임 보호 또는 호환성 문제를 유발할 수 있습니다.",
        "available": "중위험 전체 기능, 패킷 캡처와 네이티브 양쪽 전투 리포트 비교",
        "unavailable": "일상적인 사용에는 권장하지 않음",
        "after": "동기화를 켜고 준비를 확인하면, 게임 종료 시 필요한 컴포넌트를 배포하거나 업데이트합니다.",
        "note": "모드 확인이 자동 동기화를 켜는 것은 아닙니다; 작업대를 처음 열면 먼저 환경 검사가 표시됩니다. 모드 선택은 저장됩니다.",
    },
}
STATE_LABELS = {
    "available": "사용 가능", "waiting": "정상 대기", "waiting_login": "로그인 대기",
    "warning": "경고", "missing": "조건 부족", "fault": "장애",
    "cleanup_pending": "정리 대기 중",
}
ISSUE_STATES = frozenset({"fault", "missing", "cleanup_pending"})
WARNING_STATES = frozenset({"waiting_login", "warning"})


def _check_state_label(item) -> str:
    if dict(item.facts).get("inspection_incomplete") is True:
        return "검사 미완료"
    return STATE_LABELS[item.state.value]


def _fact_value(value) -> str:
    if value is True:
        return "예"
    if value is False:
        return "아니요"
    if value is None:
        return "미확인"
    return str(value)


def report_text(report) -> str:
    rows = []
    # Put failures first while retaining the original order within each group.
    for item in sorted(report.features, key=lambda item: item.state.value != "fault"):
        row = f"{item.label}：{_check_state_label(item)}\n{item.detail}"
        facts = dict(item.facts)
        labels = (
            ("game_running", "게임 프로세스"), ("core_available", "매칭 Core"),
            ("npcap", "Npcap"), ("listening", "패킷 캡처 수신 대기"),
            ("files", "파일 확인"), ("pipe", "파이프"), ("handshake", "핸드셰이크"),
            ("supported", "능력 선언"), ("snapshot", "스냅샷"), ("ready", "업무 준비 완료"),
            ("complete", "완전성"), ("source_coverage", "출처 오버라이드"),
            ("projection_complete", "필드 투영 완전"),
            ("profile_projection_supported", "캐릭터 필드 투영 능력"),
        )
        values = [f"{label}：{_fact_value(facts[key])}" for key, label in labels if key in facts]
        if values:
            row += "\n검사 사실:" + " · ".join(values)
        rows.append(row)
    return "\n\n".join(rows)


def report_summary(report) -> str:
    counts = {}
    for item in report.features:
        label = _check_state_label(item)
        counts[label] = counts.get(label, 0) + 1
    return " · ".join(f"{label} {count}개" for label, count in counts.items()) or "검사 결과 없음"


def _report_groups(report):
    """Keep shared causes together, without discarding any per-feature diagnostic fact."""
    sections = ([], [], [], [])
    for item in report.features:
        section = 0 if item.state.value in ISSUE_STATES else (
            1 if item.state.value in WARNING_STATES else
            2 if item.state.value == "waiting" else 3
        )
        key = (item.state.value, _check_state_label(item), item.detail)
        group = next((group for group in sections[section] if group[0] == key), None)
        if group is None:
            group = (key, [])
            sections[section].append(group)
        group[1].append(item.label)
    sections[0].sort(key=lambda group: group[0][0] != "fault")
    return sections


def prompt_offline_sync_mode(parent) -> bool:
    """Show only the decision needed when sync is requested in offline mode."""
    dialog = QDialog(parent)
    dialog.setObjectName("offlineSyncModeDialog")
    dialog.setWindowTitle("자동 동기화 켜기")
    dialog.setWindowModality(Qt.WindowModal)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 16)
    layout.setSpacing(18)
    guidance = QLabel(
        "상태: 자동 동기화가 켜져 있지 않음\n"
        "원인: 현재 오프라인 모드로, 게임에 연결하지 않습니다.\n"
        "다음 단계: 설정으로 이동해 동기화 가능한 작업 모드를 선택하고 확인한 뒤, 돌아와서 동기화를 켜세요.",
        dialog,
    )
    guidance.setObjectName("offlineSyncModeGuidance")
    guidance.setTextFormat(Qt.PlainText)
    guidance.setWordWrap(True)
    guidance.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    layout.addWidget(guidance)
    actions = QHBoxLayout()
    actions.addStretch()
    settings_button = QPushButton("설정으로 이동", dialog)
    settings_button.clicked.connect(dialog.accept)
    actions.addWidget(settings_button)
    cancel_button = QPushButton("취소", dialog)
    cancel_button.setDefault(True)
    cancel_button.setFocus()
    cancel_button.clicked.connect(dialog.reject)
    actions.addWidget(cancel_button)
    layout.addLayout(actions)
    fit_dialog_to_available_screen(dialog, QSize(520, 180))
    return dialog.exec() == QDialog.Accepted


class ModeReportDialog(QDialog):
    """Keep the explicit check visible from queued work through its final report."""

    def __init__(self, parent, controller, *, sync_action_provider=None):
        super().__init__(parent)
        self._controller = controller
        self._sync_action_provider = sync_action_provider
        self._settings_target = "deployment"
        self._preview = False
        self.setWindowModality(Qt.WindowModal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        header = QHBoxLayout()
        self.overview = QLabel("감지하는 중…", self)
        self.overview.setObjectName("modeReportOverview")
        self.overview.setWordWrap(True)
        self.overview.setStyleSheet("font-size:16px;font-weight:700")
        header.addWidget(self.overview, 1)
        self.copy_button = QPushButton("검사 결과 복사", self)
        self.copy_button.setObjectName("modeReportCopy")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_result)
        header.addWidget(self.copy_button)
        layout.addLayout(header)
        self.metadata = QLabel("", self)
        self.metadata.setObjectName("modeReportMetadata")
        self.metadata.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:11px")
        layout.addWidget(self.metadata)
        self.status_hint = QLabel(
            "빨간색은 처리가 필요한 항목, 노란색은 로그인 대기 또는 경고, 파란색은 정상 대기 또는 점검 대기입니다. 자세한 내용은 각 항목의 설명을 확인하세요.", self,
        )
        self.status_hint.setObjectName("modeReportStatusHint")
        self.status_hint.setWordWrap(True)
        self.status_hint.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:12px")
        self.status_hint.hide()
        layout.addWidget(self.status_hint)
        self.label = QLabel()
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignTop)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self._copy_text = ""
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.preflight_summary = QLabel()
        self.preflight_summary.setWordWrap(True)
        self.preflight_summary.setObjectName("syncPreflightSummary")
        self.preflight_summary.setStyleSheet(
            f"background:{theme_color('#161b22')};border:1px solid {theme_color('#30363d')};"
            "border-radius:6px;padding:10px"
        )
        self.preflight_summary.hide()
        layout.addWidget(self.preflight_summary)
        layout.addWidget(self.progress)
        scroll = QScrollArea(self)
        scroll.setObjectName("modeReportScroll")
        scroll.setWidgetResizable(True)
        content = QFrame(scroll)
        content.setObjectName("modeReportContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 4, 4)
        content_layout.setSpacing(10)
        self.results = QFrame(content)
        self.results_layout = QVBoxLayout(self.results)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(10)
        content_layout.addWidget(self.results)
        self.diagnostic_toggle = QPushButton("진단 정보 펼치기 (개발/피드백용)", content)
        self.diagnostic_toggle.setObjectName("modeReportDiagnosticsToggle")
        self.diagnostic_toggle.setCheckable(True)
        self.diagnostic_toggle.toggled.connect(self._toggle_diagnostics)
        content_layout.addWidget(self.diagnostic_toggle)
        self.label.hide()
        content_layout.addWidget(self.label)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        footer = QHBoxLayout()
        footer.addStretch()
        self.settings_button = QPushButton("환경 설정으로 이동")
        self._primary_action = self._open_environment_settings
        self.settings_button.clicked.connect(lambda: self._primary_action())
        footer.addWidget(self.settings_button)
        self.close_button = QPushButton("닫기")
        self.close_button.setFixedWidth(72)
        self.close_button.clicked.connect(self.reject)
        self.retry_button = QPushButton("다시 검사")
        self.retry_button.setFixedWidth(88)
        self.retry_button.clicked.connect(self._retry)
        footer.addWidget(self.retry_button)
        footer.addWidget(self.close_button)
        self.footer = footer
        layout.addLayout(footer)
        self.actions = QVBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)
        fit_dialog_to_available_screen(self, QSize(760, 560))

    def _toggle_diagnostics(self, expanded):
        self.label.setVisible(expanded)
        self.diagnostic_toggle.setText(
            "문제 해결 정보 접기 (개발/피드백용)" if expanded else "진단 정보 펼치기 (개발/피드백용)"
        )

    def _clear_results(self):
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 延迟销毁前保持父窗口，避免短暂生成无主题的顶层窗口。
                widget.hide()
                widget.deleteLater()

    def _add_result_section(self, title, groups, tone):
        if not groups:
            return
        heading = QLabel(f"{title} ({sum(len(labels) for _key, labels in groups)}개)", self.results)
        heading.setStyleSheet(f"color:{theme_color(tone)};font-weight:700;font-size:13px")
        self.results_layout.addWidget(heading)
        for (state, state_label, detail), labels in groups:
            row = QFrame(self.results)
            row.setObjectName("modeReportFeatureRow")
            color = theme_color(
                "#f85149" if state in ISSUE_STATES else
                "#d29922" if state in WARNING_STATES else "#58a6ff"
            )
            row.setStyleSheet(
                f"QFrame#modeReportFeatureRow{{background:{theme_color('#161b22')};"
                f"border:1px solid {theme_color('#30363d')};border-left:3px solid {color};"
                "border-radius:6px}"
            )
            body = QVBoxLayout(row)
            body.setContentsMargins(12, 8, 12, 8)
            body.setSpacing(3)
            label = QLabel(f"{state_label}  ·  {'、'.join(labels)}", row)
            label.setWordWrap(True)
            label.setStyleSheet(f"color:{color};font-weight:700")
            body.addWidget(label)
            # Keep typed evidence in the copyable folded report, not in the
            # user-facing issue card.
            explanation = QLabel(str(detail).split("\n진단:", 1)[0], row)
            explanation.setWordWrap(True)
            body.addWidget(explanation)
            self.results_layout.addWidget(row)

    def _render_report(self, report):
        self._clear_results()
        issues, warnings, waiting, available = _report_groups(report)
        problem_count = sum(len(labels) for _key, labels in issues)
        warning_count = sum(len(labels) for _key, labels in warnings)
        self.status_hint.setVisible(bool(issues or warnings or waiting))
        waiting_count = sum(len(labels) for _key, labels in waiting)
        ready_count = sum(len(labels) for _key, labels in available)
        if problem_count:
            self.overview.setText(
                f"처리 필요 {problem_count}개 · 노란색 알림 {warning_count}개 · "
                f"대기 {waiting_count}개 · 준비 완료 {ready_count}개"
            )
        elif warning_count:
            self.overview.setText(
                f"노란색 알림 {warning_count}개 · 대기 {waiting_count}개 · 준비 완료 {ready_count}개"
            )
        elif waiting_count:
            self.overview.setText(f"대기 {waiting_count}개 · 준비 완료 {ready_count}개")
        else:
            self.overview.setText(f"전체 {ready_count}개 항목 준비 완료")
        self._add_result_section("처리 필요", issues, "#f85149")
        self._add_result_section("로그인 대기 또는 경고", warnings, "#d29922")
        self._add_result_section("대기 또는 점검 대기", waiting, "#58a6ff")
        if available:
            heading = QLabel(f"준비 완료 ({ready_count}개)", self.results)
            heading.setStyleSheet(f"color:{theme_color('#3fb950')};font-weight:700;font-size:13px")
            self.results_layout.addWidget(heading)
            names = QLabel("、".join(label for _key, labels in available for label in labels), self.results)
            names.setWordWrap(True)
            self.results_layout.addWidget(names)

    def _open_environment_settings(self):
        controller, target = self._controller, self._settings_target
        self.accept()
        QTimer.singleShot(0, lambda: controller.open_settings(target))

    def _set_primary_action(self, title, callback):
        self.settings_button.setText(title)
        self._primary_action = callback
        self.settings_button.show()

    def _retry(self):
        if self._preview:
            self._controller.check(show=True, preview=True)
        else:
            self._controller.check(show=True, retry_deployment=True)

    def _clear_actions(self):
        while self.actions.count():
            button = self.actions.takeAt(0).widget()
            button.hide()
            button.deleteLater()

    def begin(self, mode, *, preview=False):
        self.setWindowTitle(f"{MODE_LABELS[mode]} 모드 검사")
        self._settings_target = "deployment"
        self._preview = preview
        self._clear_results()
        self.overview.setText("동기화 조건을 확인하는 중…" if preview else "환경을 검사하는 중…")
        self.metadata.clear()
        self.status_hint.hide()
        self.diagnostic_toggle.setChecked(False)
        self.diagnostic_toggle.hide()
        self.label.clear()
        self.close_button.setDefault(preview)
        if preview:
            self.close_button.setFocus()
        self.preflight_summary.setVisible(preview)
        if preview:
            self.preflight_summary.setText("상태: 확인 중\n다음 단계: 확인 완료 후 처리를 확정하거나 환경 설정으로 이동하세요.")
        self._set_primary_action("환경 설정으로 이동", self._open_environment_settings)
        self._clear_actions()
        self.retry_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self._copy_text = ""
        detail = ("동기화 시작에 필요한 조건을 확인하는 중입니다; 이 단계는 컴포넌트를 정리하거나 배포하지 않습니다…" if preview else
                  "네이티브 연결을 종료하고 게임 컴포넌트를 정리하는 중…" if mode in {"offline", "low"} else
                  "환경을 검사하고 관련 컴포넌트를 처리하는 중…")
        closing = ("창을 닫으면 자동 동기화는 꺼진 상태로 유지됩니다." if preview else
                   "창을 닫아도 이미 확인된 모드 전환과 컴포넌트 처리는 취소되지 않습니다.")
        pending = QLabel(detail + "\n" + closing, self.results)
        pending.setWordWrap(True)
        self.results_layout.addWidget(pending)
        self.progress.show()
        self.show()
        self.raise_()

    def _add_action(self, title, callback, *, close=False):
        button = QPushButton(title)
        button.setObjectName("btnNew")
        button.setAutoDefault(False)
        def run():
            if close:
                self.accept()
            callback()
        button.clicked.connect(run)
        self.actions.addWidget(button)

    def set_report(self, report):
        self.progress.hide()
        self._clear_actions()
        self._set_primary_action("환경 설정으로 이동", self._open_environment_settings)
        self.retry_button.setEnabled(True)
        self._set_result(report_text(report))
        self._render_report(report)
        available = {action for item in report.features for action in item.actions}
        if "manual_deploy" in available and not self._preview:
            self._settings_target = "deployment"
            self._add_action("컴포넌트 배포로 이동", self._open_environment_settings)
            self.settings_button.hide()
        elif "download_npcap" in available:
            def download():
                self.parentWidget()._open_npcap_download()
            self._add_action("Npcap 다운로드", download, close=True)
            self.settings_button.hide()
        elif "detect_game_path" in available:
            def detect():
                self._controller.detect_path()
            self._add_action("경로 다시 검사", detect, close=True)
            self.settings_button.hide()
        if (not self._preview and self._sync_action_provider is not None
                and report.can_offer_sync_enable):
            action = self._sync_action_provider(report)
            if action is not None:
                self._add_action("자동 동기화 켜기", action)

    def set_error(self, detail):
        self.status_hint.hide()
        self.progress.hide()
        self._clear_actions()
        self._set_primary_action("환경 설정으로 이동", self._open_environment_settings)
        self.retry_button.setEnabled(True)
        self._set_result(detail)
        self._clear_results()
        self.overview.setText("검사 미완료 · 다시 검사해 주세요")
        self._add_result_section(
            "처리 필요", [(('fault', '장애', '환경 검사가 중단되었습니다. 문제 해결 정보를 확인하세요.'), ['환경 검사'])],
            "#f85149",
        )
        if self._preview:
            self.preflight_summary.setText(
                "상태: 검사 미완료\n원인: 환경 검사가 중단되었습니다.\n다음 단계: 다시 검사하세요; 계속 실패하면 검사 결과를 복사해 제보해 주세요."
            )

    def set_sync_preflight(self, decision):
        self._settings_target = decision.target
        self._clear_actions()
        self.preflight_summary.setText(
            ("상태: 동기화를 켤 수 있음" if decision.ready else "상태: 처리 대기 중") +
            "\n원인:" + decision.detail +
            ("\n다음 단계: 동기화가 자동으로 켜집니다." if decision.ready else
             "\n다음 단계:" + (decision.action_label or "처리 후 다시 검사해 주세요."))
        )
        if decision.action_label:
            self._add_action(decision.action_label, self._open_environment_settings)
            self.settings_button.hide()
        elif decision.target == "mode":
            self._set_primary_action("작업 모드 설정으로 이동", self._open_environment_settings)
        else:
            self._set_primary_action("환경 설정으로 이동", self._open_environment_settings)
        self._copy_text += "\n\n동기화 켜기:" + decision.detail
        self.label.setText(self._copy_text)
        if decision.ready:
            self.settings_button.hide()

    def set_activation_result(self, ready, detail):
        self.status_hint.hide()
        self.progress.hide()
        self._clear_actions()
        self.retry_button.setEnabled(True)
        self.preflight_summary.show()
        self.preflight_summary.setText(
            ("상태: 동기화 켜짐" if ready else "상태: 자동 동기화가 여전히 꺼져 있음") +
            "\n원인:" + detail +
            ("\n다음 단계: 게임을 시작해 게임 장면에 진입하고, 데이터가 준비될 때까지 기다리세요." if ready else
             "\n다음 단계: 검사 상세를 확인하거나 환경 설정으로 이동한 뒤 다시 시도하세요.")
        )
        self._clear_results()
        self._set_result(self.preflight_summary.text())
        self.overview.setText("동기화 켜짐" if ready else "동기화 여전히 꺼짐")
        self.settings_button.setVisible(not ready)

    def _set_result(self, detail):
        header = (f"NTE Drive Calc {__version__} · {self.windowTitle()}\n"
                  f"검사 시각: {datetime.now().astimezone().isoformat(timespec='seconds')}")
        self._copy_text = header + "\n\n" + detail
        self.metadata.setText(header.replace("\n", "  ·  "))
        self.label.setText(self._copy_text)
        self.diagnostic_toggle.show()
        self.copy_button.setEnabled(True)

    def _copy_result(self):
        if self._copy_text:
            QApplication.clipboard().setText(self._copy_text)


class CleanupResultDialog(QDialog):
    """Keep an explicit cleanup result separate from the environment report."""

    def __init__(self, parent, *, continue_upgrade: bool = False):
        super().__init__(parent)
        self._continue_upgrade = continue_upgrade
        self.setObjectName("gameDirectoryCleanupDialog")
        self.setWindowTitle("게임 디렉터리 정리")
        self.setWindowModality(Qt.WindowModal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(14)
        self.message = QLabel(self)
        self.message.setObjectName("gameDirectoryCleanupMessage")
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(self.message)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        actions = QHBoxLayout()
        actions.addStretch()
        self.continue_button = QPushButton("업그레이드 안내 계속", self)
        self.continue_button.setObjectName("btnNew")
        self.continue_button.setAutoDefault(False)
        self.continue_button.clicked.connect(self.accept)
        self.continue_button.hide()
        actions.addWidget(self.continue_button)
        close_button = QPushButton("닫기", self)
        close_button.setDefault(True)
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        fit_dialog_to_available_screen(self, QSize(560, 230))

    def begin(self):
        self.message.setText(
            "상태: 게임 폴더 정리 중\n"
            "원인: 관련 서비스를 종료하고 본 프로그램이 관리하는 컴포넌트를 확인하는 중입니다.\n"
            "다음 단계: 잠시 기다려 주세요; 이 창을 닫아도 이미 시작된 정리는 중단되지 않습니다."
        )
        self.progress.show()
        self.show()
        self.raise_()

    def set_result(self, *, pending: bool, state: str, detail: str):
        self.progress.hide()
        if state == "fault":
            status = "정리 미완료"
            next_step = "원인에 따라 처리한 뒤, 「게임 디렉터리 정리」를 다시 클릭하세요."
        elif not pending:
            status = "정리됨"
            next_step = (
                "「업그레이드 가이드 계속」을 클릭하면 가이드 창으로 돌아갑니다; 닫은 후에도 작업대에서 이어서 진행할 수 있습니다."
                if self._continue_upgrade else
                "다시 동기화해야 하면 작업 모드를 확인한 후 자동 동기화를 켜세요."
            )
        else:
            status = "정리 계속 대기"
            next_step = "원인에 따라 처리한 뒤 다시 시도하세요; 게임이 실행 중이면 먼저 게임을 종료하세요."
        self.message.setText(f"상태: {status}\n원인: {detail}\n다음 단계: {next_step}")
        self.continue_button.setVisible(self._continue_upgrade and not pending and state != "fault")


def build_work_mode_card(window):
    controller = window.work_mode_controller
    service = window.work_mode_service
    card = window._card("작업 모드")
    controls = QHBoxLayout()
    controls.setSpacing(10)
    current_label = QLabel("현재 모드:")
    controls.addWidget(current_label)
    combo = NoWheelComboBox()
    for key, label in MODE_LABELS.items():
        combo.addItem(label, key)
    combo.setCurrentIndex(combo.findData(service.settings.mode.value))
    combo.setFixedWidth(150)
    controls.addWidget(combo)
    check = QPushButton("검사·처리")
    check.setFixedWidth(96)
    check.clicked.connect(lambda: controller.check(show=True, retry_deployment=True))
    controls.addWidget(check)
    controls.addStretch()
    card.layout().addLayout(controls)

    mode_labels = {}
    descriptions = QVBoxLayout()
    descriptions.setSpacing(8)
    for key, title in MODE_LABELS.items():
        row = QHBoxLayout()
        row.setSpacing(8)
        mode_label = QLabel(f"{title}：")
        mode_label.setObjectName(f"workModeDescription_{key}")
        mode_label.setFixedWidth(58)
        detail = QLabel(MODE_DESCRIPTIONS[key])
        detail.setWordWrap(False)
        detail.setStyleSheet(f"color:{theme_color('#c9d1d9')}")
        row.addWidget(mode_label)
        row.addWidget(detail)
        row.addStretch()
        descriptions.addLayout(row)
        mode_labels[key] = mode_label
    card.layout().addLayout(descriptions)

    def refresh_mode_emphasis(_index=None):
        selected = service.settings.mode.value if combo.currentIndex() >= 0 else None
        for key, label in mode_labels.items():
            label.setProperty("confirmedMode", key == selected)
            color = theme_color("#58a6ff" if key == selected else "#f0f6fc")
            label.setStyleSheet(f"color:{color};font-weight:700")

    combo.currentIndexChanged.connect(refresh_mode_emphasis)
    refresh_mode_emphasis()

    # The detailed per-feature state now belongs to the explicit report dialog.
    # Retain a hidden projection target so the controller contract stays narrow.
    status = QLabel("현재 모드를 확인하는 중…", card)
    status.hide()
    controller.attach_controls(combo, status, check)

    def select_mode(_index):
        controller.select_mode(combo.currentData())
        refresh_mode_emphasis()

    combo.activated.connect(select_mode)
    return card


def _confirmation_row(title: str, detail: str, tone: str, parent) -> QFrame:
    frame = QFrame(parent)
    frame.setObjectName("workModeConfirmationRow")
    frame.setStyleSheet(
        f"QFrame#workModeConfirmationRow{{background:{theme_color('#161b22')};"
        f"border:1px solid {theme_color('#30363d')};border-radius:8px}}"
    )
    row = QHBoxLayout(frame)
    row.setContentsMargins(14, 11, 14, 11)
    row.setSpacing(12)
    heading = QLabel(title, frame)
    heading.setObjectName(f"workModeConfirmation_{tone}")
    heading.setFixedWidth(76)
    tone_color = {"available": "#3fb950", "unavailable": "#8b949e", "after": "#58a6ff"}[tone]
    heading.setStyleSheet(
        f"color:{theme_color(tone_color)};font-weight:700"
    )
    body = QLabel(detail, frame)
    body.setWordWrap(True)
    row.addWidget(heading)
    row.addWidget(body, 1)
    return frame


def confirm_mode(parent, mode: str) -> bool:
    copy = MODE_CONFIRMATIONS[mode]
    dialog = QDialog(parent)
    dialog.setObjectName("workModeConfirmationDialog")
    dialog.setWindowTitle("전환 확인: " + MODE_LABELS[mode] + "모드")
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 18)
    layout.setSpacing(12)

    warning = QFrame(dialog)
    warning.setObjectName("workModeWarning")
    warning.setStyleSheet(
        f"QFrame#workModeWarning{{background:{theme_color('#2d1117')};"
        f"border:1px solid {theme_color('#f85149')};border-radius:9px}}"
    )
    warning_row = QHBoxLayout(warning)
    warning_row.setContentsMargins(16, 14, 16, 14)
    warning_row.setSpacing(14)
    icon = QLabel("⚠", warning)
    icon.setObjectName("workModeWarningIcon")
    icon.setStyleSheet(f"color:{theme_color('#f85149')};font-size:30px;font-weight:700")
    warning_row.addWidget(icon)
    warning_text = QVBoxLayout()
    warning_text.setSpacing(3)
    warning_title = QLabel(copy["warning_title"], warning)
    warning_title.setObjectName("workModeWarningTitle")
    warning_title.setStyleSheet(f"color:{theme_color('#f85149')};font-size:16px;font-weight:700")
    warning_detail = QLabel(copy["warning_detail"], warning)
    warning_detail.setObjectName("workModeWarningDetail")
    warning_detail.setWordWrap(True)
    warning_text.addWidget(warning_title)
    warning_text.addWidget(warning_detail)
    warning_row.addLayout(warning_text, 1)
    layout.addWidget(warning)

    layout.addWidget(_confirmation_row("사용 가능", copy["available"], "available", dialog))
    layout.addWidget(_confirmation_row("사용 불가", copy["unavailable"], "unavailable", dialog))
    layout.addWidget(_confirmation_row("전환 후", copy["after"], "after", dialog))

    note = QLabel(copy["note"], dialog)
    note.setObjectName("workModeConfirmationNote")
    note.setWordWrap(True)
    note.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:12px")
    layout.addWidget(note)

    buttons = QDialogButtonBox(QDialogButtonBox.Cancel, parent=dialog)
    cancel = buttons.button(QDialogButtonBox.Cancel)
    cancel.setText("취소")
    cancel.setFixedSize(88, 38)
    consent = buttons.addButton(
        "전환 확인" if mode == "offline" else "위험 확인 후 전환",
        QDialogButtonBox.AcceptRole,
    )
    consent.setObjectName("workModeConfirm" if mode == "offline" else "workModeRiskConsent")
    consent.setMinimumWidth(148)
    consent.setFixedHeight(38)
    consent.setAutoDefault(False)
    consent.setDefault(False)
    if mode != "offline":
        consent.setStyleSheet(
            f"QPushButton{{background:{theme_color('#da3633')};color:#ffffff;"
            f"border:1px solid {theme_color('#f85149')};border-radius:6px;padding:7px 16px;font-weight:700}}"
            f"QPushButton:hover{{background:{theme_color('#f85149')}}}"
            f"QPushButton:pressed{{background:{theme_color('#b42318')}}}"
            f"QPushButton:focus{{border:2px solid {theme_color('#fda29b')};padding:6px 15px}}"
        )
    button_layout = buttons.layout()
    button_layout.removeWidget(cancel)
    button_layout.removeWidget(consent)
    button_layout.addStretch()
    button_layout.addWidget(cancel)
    button_layout.addWidget(consent)
    cancel.setDefault(True)
    cancel.setFocus()
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    fit_dialog_to_available_screen(dialog, QSize(720, 440))
    return dialog.exec() == QDialog.Accepted
