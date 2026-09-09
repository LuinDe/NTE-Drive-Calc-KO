# 构建执行页的扫描、解析和分配控件。
"""Execute page builder.

The execute page owns controls for scan mode, priority roles, allocation
strategy, and run/result actions. Business behavior stays on MainWindow; this
builder only wires UI widgets to existing callbacks.
"""

from __future__ import annotations

from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style

def _build_scan_mode_card(window, layout, scan_help, drone_help, offline_help, show_help):
    scan_card = window._card("1단계 · 스캔 모드")
    window.scan_group = QButtonGroup()
    _add_scan_mode_options(window, scan_card, scan_help, show_help)
    _build_offline_frame(window, scan_card, offline_help, show_help)
    _build_total_count_frame(window, scan_card)
    _build_full_scan_driver_frame(window, scan_card)
    build_scan_processing_options(window, scan_card, show_help)
    _build_drone_frame(window, scan_card, drone_help, show_help)
    window.scan_group.idToggled.connect(window._on_scan_change)
    layout.addWidget(scan_card)


def _add_scan_mode_options(window, scan_card, scan_help, show_help):
    scan_options = [
        ("4", "인벤토리 바로 읽기 — 스캔 없이 바로 재배치"),
        ("3", "오프라인 분석 — 기존 스크린샷을 분석해 인벤토리 생성"),
        ("2", "증분 스캔 — 새로 획득한 장비만 등록"),
        ("1", "전체 스캔 — 가방을 다시 스캔해 인벤토리 재구축"),
    ]
    for value, text in scan_options:
        row = QHBoxLayout()
        row.setSpacing(6)
        rb = QRadioButton(text)
        rb.setChecked(value == "4")
        window.scan_group.addButton(rb, int(value))
        row.addWidget(rb)
        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.clicked.connect(
            lambda checked, v=value, parent=help_btn: show_help(
                parent, "스캔 모드 설명", scan_help.get(v, ""),
            )
        )
        row.addWidget(help_btn)
        row.addStretch()
        scan_card.layout().addLayout(row)


def _build_offline_frame(window, scan_card, offline_help, show_help):
    window.offline_frame = QWidget()
    window.offline_frame.setVisible(False)
    offline_layout = QHBoxLayout(window.offline_frame)
    offline_layout.setContentsMargins(28, 4, 0, 4)
    offline_layout.setSpacing(10)
    offline_layout.addWidget(QLabel("오프라인 분석 유형:"))
    window.offline_group = QButtonGroup()
    for key, text in [("full", "전체 분석"), ("incremental", "증분 분석"), ("all", "전체 스크린샷 분석")]:
        sub_row = QHBoxLayout()
        sub_row.setSpacing(6)
        rb = QRadioButton(text)
        rb.setChecked(key == "incremental")
        rb.setProperty("offline_key", key)
        window.offline_group.addButton(rb)
        sub_row.addWidget(rb)
        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.clicked.connect(
            lambda checked, k=key, parent=help_btn: show_help(
                parent, "오프라인 분석 설명", offline_help.get(k, ""),
            )
        )
        sub_row.addWidget(help_btn)
        offline_layout.addLayout(sub_row)
    offline_layout.addStretch()
    scan_card.layout().addWidget(window.offline_frame)


def _build_total_count_frame(window, scan_card):
    window.total_count_frame = QWidget()
    window.total_count_frame.setVisible(False)
    total_count_layout = QHBoxLayout(window.total_count_frame)
    total_count_layout.setContentsMargins(28, 4, 0, 4)
    total_count_layout.setSpacing(8)
    total_count_layout.addWidget(QLabel("인벤토리 수량:"))
    window.total_count_edit = QLineEdit()
    window.total_count_edit.setPlaceholderText("현재 인벤토리 수량을 입력하세요")
    window.total_count_edit.setValidator(QIntValidator(1, 2000, window.total_count_edit))
    window.total_count_edit.setMaximumWidth(180)
    total_count_layout.addWidget(window.total_count_edit)
    window.scan_post_action_btn = QPushButton("관리")
    window.scan_post_action_btn.setObjectName("btnPrimary")
    window.scan_post_action_btn.setMaximumWidth(82)
    if hasattr(window, "_open_scan_post_action_manager"):
        window.scan_post_action_btn.clicked.connect(window._open_scan_post_action_manager)
    total_count_layout.addWidget(window.scan_post_action_btn)
    total_count_layout.addStretch()
    scan_card.layout().addWidget(window.total_count_frame)


def _build_full_scan_driver_frame(window, scan_card):
    window.full_scan_driver_frame = QWidget()
    window.full_scan_driver_frame.setVisible(False)
    driver_layout = QHBoxLayout(window.full_scan_driver_frame)
    driver_layout.setContentsMargins(28, 4, 0, 4)
    driver_layout.setSpacing(10)
    driver_layout.addWidget(QLabel("조작 방식:"))
    window.full_scan_driver_group = QButtonGroup()
    prefs = getattr(window, "_ui_preferences", {}) or {}
    preferred = str(prefs.get("full_scan_capture_driver", "mouse"))
    if preferred not in {"mouse", "gamepad"}:
        preferred = "mouse"
    for button_id, key, text in (
        (1, "mouse", "마우스 스캔 (기본)"),
        (2, "gamepad", "가상 게임패드 스캔 (대체)"),
    ):
        rb = QRadioButton(text)
        rb.setProperty("capture_driver", key)
        rb.setChecked(key == preferred)
        window.full_scan_driver_group.addButton(rb, button_id)
        driver_layout.addWidget(rb)

    def _save_capture_driver(button_id, checked):
        if not checked:
            return
        key = "gamepad" if button_id == 2 else "mouse"
        preferences = getattr(window, "_ui_preferences", None)
        if isinstance(preferences, dict):
            preferences["full_scan_capture_driver"] = key
            if hasattr(window, "_save_ui_preferences"):
                window._save_ui_preferences()
        if hasattr(window, "scan_post_action_btn"):
            window.scan_post_action_btn.setVisible(key in {"mouse", "gamepad"})

    window.full_scan_driver_group.idToggled.connect(_save_capture_driver)
    if hasattr(window, "scan_post_action_btn"):
        window.scan_post_action_btn.setVisible(preferred in {"mouse", "gamepad"})
    driver_layout.addStretch()
    scan_card.layout().addWidget(window.full_scan_driver_frame)


def build_scan_processing_options(window, scan_card, show_help):
    window.scan_dual_thread_frame = QWidget()
    window.scan_dual_thread_frame.setVisible(False)
    dual_thread_layout = QHBoxLayout(window.scan_dual_thread_frame)
    dual_thread_layout.setContentsMargins(28, 0, 0, 4)
    dual_thread_layout.setSpacing(8)
    window.scan_dual_thread_check = QCheckBox("듀얼 스레드 처리")
    prefs = getattr(window, "_ui_preferences", {}) or {}
    window.scan_dual_thread_check.setChecked(bool(prefs.get("full_scan_dual_thread_processing", True)))
    window.scan_amd_compat_check = QCheckBox("이상 호환 모드")
    window.scan_amd_compat_check.setChecked(bool(prefs.get("full_scan_amd_compatibility", False)))

    def _save_scan_dual_thread_preference(enabled):
        preferences = getattr(window, "_ui_preferences", None)
        if isinstance(preferences, dict):
            preferences["full_scan_dual_thread_processing"] = bool(enabled)
            if enabled and hasattr(window, "scan_amd_compat_check") and window.scan_amd_compat_check.isChecked():
                window.scan_amd_compat_check.blockSignals(True)
                window.scan_amd_compat_check.setChecked(False)
                window.scan_amd_compat_check.blockSignals(False)
                preferences["full_scan_amd_compatibility"] = False
            if hasattr(window, "_save_ui_preferences"):
                window._save_ui_preferences()

    window.scan_dual_thread_check.toggled.connect(_save_scan_dual_thread_preference)
    dual_thread_layout.addWidget(window.scan_dual_thread_check)
    dual_help_btn = QPushButton("?")
    dual_help_btn.setObjectName("btnHelp")
    dual_help_btn.clicked.connect(
        lambda _checked=False, parent=dual_help_btn: show_help(
            parent,
            "듀얼 스레드 처리 설명",
            "· 마우스·가상 게임패드 모두 스캔과 동시에 분석해 더 빠름\n"
            "· 표준 분석 스레드 사용; 끊김·재시작·이상이 발생하면 꺼 주세요",
        )
    )
    dual_thread_layout.addWidget(dual_help_btn)
    dual_thread_layout.addSpacing(16)
    def _save_scan_amd_compat_preference(enabled):
        preferences = getattr(window, "_ui_preferences", None)
        if isinstance(preferences, dict):
            preferences["full_scan_amd_compatibility"] = bool(enabled)
            if enabled:
                if hasattr(window, "scan_dual_thread_check"):
                    window.scan_dual_thread_check.blockSignals(True)
                    window.scan_dual_thread_check.setChecked(False)
                    window.scan_dual_thread_check.blockSignals(False)
                    preferences["full_scan_dual_thread_processing"] = False
            if hasattr(window, "_save_ui_preferences"):
                window._save_ui_preferences()

    window.scan_amd_compat_check.toggled.connect(_save_scan_amd_compat_preference)
    dual_thread_layout.addWidget(window.scan_amd_compat_check)
    amd_help_btn = QPushButton("?")
    amd_help_btn.setObjectName("btnHelp")
    amd_help_btn.clicked.connect(
        lambda _checked=False, parent=amd_help_btn: show_help(
            parent,
            "이상 호환 모드 설명",
            "· 이 모드는 저부하 직렬 분석을 사용\n"
            "· 이 모드는 클릭·휠·분석 속도를 더 늦춤\n"
            "· 켜면 듀얼 스레드 처리가 꺼짐\n"
            "· 끊김·되돌아감·누락 스캔이 있을 때 사용",
        )
    )
    dual_thread_layout.addWidget(amd_help_btn)
    if window.scan_amd_compat_check.isChecked():
        window.scan_dual_thread_check.blockSignals(True)
        window.scan_dual_thread_check.setChecked(False)
        window.scan_dual_thread_check.blockSignals(False)
    dual_thread_layout.addStretch()
    scan_card.layout().addWidget(window.scan_dual_thread_frame)


def _build_drone_frame(window, scan_card, drone_help, show_help):
    window.drone_frame = QWidget()
    window.drone_frame.setVisible(False)
    drone_layout = QHBoxLayout(window.drone_frame)
    drone_layout.setContentsMargins(28, 4, 0, 4)
    drone_layout.addWidget(QLabel("드론 모드:"))
    window.drone_group = QButtonGroup()
    for value, text in [("2", "반자동 모드 (권장)"), ("1", "완전 자동 모드")]:
        sub_row = QHBoxLayout()
        sub_row.setSpacing(6)
        rb = QRadioButton(text)
        rb.setChecked(value == "2")
        window.drone_group.addButton(rb, int(value))
        sub_row.addWidget(rb)
        help_btn = QPushButton("?")
        help_btn.setObjectName("btnHelp")
        help_btn.clicked.connect(
            lambda checked, v=value, parent=help_btn: show_help(
                parent, "증분 모드 설명", drone_help.get(v, ""),
            )
        )
        sub_row.addWidget(help_btn)
        drone_layout.addLayout(sub_row)
    drone_layout.addStretch()
    scan_card.layout().addWidget(window.drone_frame)


def _build_priority_card(window, layout, role_selector_cls):
    priority_card = window._card("2단계 · 캐릭터 우선순위 설정")
    window.role_selector = role_selector_cls()
    window.role_selector.orderChanged.connect(window._on_priority_changed)
    priority_card.layout().addWidget(window.role_selector)
    layout.addWidget(priority_card)


def _build_strategy_card(window, layout):
    strategy_card = window._card("3단계 · 분배 전략")
    title_item = strategy_card.layout().takeAt(0)
    title_label = title_item.widget() if title_item is not None else None
    if title_label is None:
        title_label = QLabel("3단계 · 분배 전략")
        title_label.setObjectName("cardTitle")
    header = QHBoxLayout()
    header.setSpacing(0)
    header.addWidget(title_label)
    header.addSpacing(max(1, title_label.fontMetrics().horizontalAdvance("　") // 2))
    window.allocation_filter_settings_button = QPushButton("설정")
    window.allocation_filter_settings_button.setObjectName("allocationFilterSettingsButton")
    window.allocation_filter_settings_button.setFixedSize(54, 28)
    window.allocation_filter_settings_button.clicked.connect(
        window._open_allocation_filter_settings
    )
    header.addWidget(window.allocation_filter_settings_button)
    header.addStretch(1)
    strategy_card.layout().addLayout(header)

    window.strategy_group = QButtonGroup()
    strategy_options = [
        "캐릭터 우선 — 캐릭터 순서대로 세팅, 앞순위 캐릭터를 우선 배려",
        "증분 갱신 — 착용 중인 장비는 유지하고 여유 장비로만 보충",
    ]
    for index, text in enumerate(strategy_options):
        rb = QRadioButton(text)
        rb.setChecked(index == 0)
        window.strategy_group.addButton(rb, index)
        strategy_card.layout().addWidget(rb)
    layout.addWidget(strategy_card)


def _build_run_button(window, layout):
    window.btn_run = QPushButton("⚡  계산 시작")
    window.btn_run.setObjectName("btnPrimary")
    window.btn_run.setFixedHeight(46)
    window.btn_run.setStyleSheet("#btnPrimary{font-size:15px;font-weight:700;border-radius:10px}")
    window.btn_run.clicked.connect(window._do_exec)
    layout.addWidget(window.btn_run)


def _build_result_card(window, layout):
    window.result_card = QWidget()
    window.result_card.setVisible(False)
    window.result_card.setStyleSheet(
        themed_style("QWidget{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:18px}")
    )
    result_layout = QVBoxLayout(window.result_card)
    result_header = QHBoxLayout()
    result_header.addWidget(QLabel("계산 결과"))
    result_header.addStretch()
    window.btn_save = QPushButton("장비 잠금 저장")
    window.btn_save.setObjectName("btnAction")
    window.btn_save.clicked.connect(lambda _checked=False: window._save_alloc())
    result_header.addWidget(window.btn_save)
    result_layout.addLayout(result_header)
    window.result_content = QWidget()
    window.result_content_layout = QVBoxLayout(window.result_content)
    result_layout.addWidget(window.result_content)
    layout.addWidget(window.result_card)


def build_execute_page(window, role_selector_cls, scan_help, drone_help, offline_help, show_help):
    page = QWidget()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(12)

    _build_scan_mode_card(window, layout, scan_help, drone_help, offline_help, show_help)
    _build_priority_card(window, layout, role_selector_cls)
    _build_strategy_card(window, layout)
    _build_run_button(window, layout)
    _build_result_card(window, layout)
    layout.addStretch()
    return scroll
