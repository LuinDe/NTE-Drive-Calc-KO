# 构建设置页的日志、热键、更新和文件管理区域。
"""Settings page builder.

The settings page shows hotkeys, updates, screenshot management, and quick-access
folders. MainWindow still owns all callbacks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QKeySequenceEdit,
)

from src.features.settings.work_mode_card import build_work_mode_card
from src.features.settings.performance_card import PerformanceCard
from src.app.constants import NETDISK_DOWNLOAD_LINKS
from src.app.context import AppContext
from src.app.theme import THEME_LABELS, themed_style
from src.ui.widgets import NoWheelComboBox


def _normalize_netdisk_links(netdisk_links=None):
    if netdisk_links is None:
        return tuple(NETDISK_DOWNLOAD_LINKS)
    if isinstance(netdisk_links, str):
        return (("Quark 클라우드", netdisk_links),) if netdisk_links else tuple()
    return tuple((str(name), str(url)) for name, url in netdisk_links if name and url)


def refresh_account_scoped_settings(window) -> None:
    """Refresh already-built settings controls after an account switch."""

    preferences = getattr(window, "_ui_preferences", {}) or {}
    for editor_name, value_name in (
        ("_hk_capture_edit", "_hk_capture"),
        ("_hk_finish_edit", "_hk_finish"),
        ("_hk_stop_edit", "_hk_stop"),
        ("_hk_battle_rerecord_edit", "_hk_battle_rerecord"),
    ):
        hotkey_editor = getattr(window, editor_name, None)
        if hotkey_editor is not None:
            hotkey_editor.blockSignals(True)
            hotkey_editor.setKeySequence(
                QKeySequence(str(getattr(window, value_name, "")))
            )
            hotkey_editor.blockSignals(False)
    edit = getattr(window, "_protagonist_game_name_edit", None)
    if edit is not None:
        edit.blockSignals(True)
        edit.setText(str(preferences.get("protagonist_game_name") or ""))
        edit.blockSignals(False)
    game_edit = getattr(window, "_equipment_plugin_game_executable_edit", None)
    if game_edit is not None:
        game_edit.blockSignals(True)
        game_edit.setText(
            window.work_mode_service.settings.game_executable
        )
        game_edit.blockSignals(False)
    method_combo = getattr(window, "_equipment_plugin_loading_method_combo", None)
    if method_combo is not None:
        method_combo.blockSignals(True)
        method_index = method_combo.findData(
            window.work_mode_service.deployment_record.get("loading_method") or "native-capture"
        )
        method_combo.setCurrentIndex(max(0, method_index))
        method_combo.blockSignals(False)
    consent = getattr(window, "_equipment_plugin_consent", None)
    if consent is not None:
        consent.blockSignals(True)
        consent.setChecked(
            bool(preferences.get("equipment_plugin_risk_acknowledged", False))
        )
        consent.blockSignals(False)
    refresh_plugin = getattr(window, "_refresh_equipment_plugin_status", None)
    if callable(refresh_plugin):
        refresh_plugin()


@dataclass(frozen=True)
class SettingsPaths:
    config_dir: Path
    accounts_dir: Path
    log_dir: Path
    screenshot_dir: Path


def _settings_paths(context: AppContext) -> SettingsPaths:
    return SettingsPaths(
        config_dir=context.paths.config_dir,
        accounts_dir=context.paths.accounts_dir,
        log_dir=context.account.log_dir,
        screenshot_dir=context.account.screenshot_dir,
    )


def _build_capture_diagnostics_card(window):
    card = window._card("수집 문제 해결")
    description = QLabel("수집 문제 해결에만 사용됩니다. 설정은 다음 연결 또는 동기화 시 적용됩니다.")
    description.setWordWrap(True)
    description.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    card.layout().addWidget(description)
    form = QFormLayout()
    form.setSpacing(10)

    settings_reader = getattr(window, "_get_sync_settings", None)
    settings = settings_reader() if callable(settings_reader) else {}
    if not settings:
        raise RuntimeError("정적 데이터베이스의 설정 기본값을 읽을 수 없습니다.")
    window._sync_capture_device_edit = QLineEdit()
    window._sync_capture_device_edit.setPlaceholderText("네트워크 어댑터 자동 선택이 실패했을 때만 입력하세요")
    window._sync_capture_device_edit.setText(settings.get("capture_device_id") or "")

    def resize_capture_device_edit(text: str) -> None:
        content = str(text or window._sync_capture_device_edit.placeholderText())
        text_width = window._sync_capture_device_edit.fontMetrics().horizontalAdvance(content)
        window._sync_capture_device_edit.setFixedWidth(
            max(360, min(680, text_width + 36))
        )

    resize_capture_device_edit(window._sync_capture_device_edit.text())
    window._sync_capture_device_edit.textChanged.connect(resize_capture_device_edit)
    form.addRow("캡처 네트워크 어댑터:", window._sync_capture_device_edit)
    save_handler = getattr(window, "_save_capture_diagnostics", None)

    def save_capture_diagnostics() -> None:
        if callable(save_handler):
            settings = save_handler()
            window.performance_controller.capture_changed(settings)
            if settings is None and callable(settings_reader):
                window._sync_raw_capture_toggle.setChecked(bool(settings_reader()["raw_capture_enabled"]))

    window._sync_capture_device_edit.editingFinished.connect(save_capture_diagnostics)

    window._sync_raw_capture_toggle = QCheckBox("원본 수집 데이터 저장 (문제 해결)")
    window._sync_raw_capture_toggle.setChecked(
        bool(settings["raw_capture_enabled"])
    )
    window._sync_raw_capture_toggle.setToolTip(
        "문제 해결 시에 켜세요; 데이터는 현재 계정 로그 디렉터리에 저장되고 자동으로 순환되며, 디스크 공간을 많이 차지할 수 있습니다."
    )
    raw_capture_row = QHBoxLayout()
    raw_capture_row.addWidget(window._sync_raw_capture_toggle)
    raw_capture_open_button = QPushButton("원시 데이터 디렉터리 열기")
    window._sync_raw_capture_open_button = raw_capture_open_button
    raw_capture_open_handler = getattr(window, "_open_raw_capture_directory", None)
    if callable(raw_capture_open_handler):
        raw_capture_open_button.clicked.connect(raw_capture_open_handler)
    else:
        raw_capture_open_button.setEnabled(False)
    raw_capture_row.addWidget(raw_capture_open_button)
    raw_capture_row.addStretch()
    def save_raw_capture_diagnostics(enabled: bool) -> None:
        window.work_mode_controller.set_raw_capture_draft(enabled)
        if window._sync_raw_capture_toggle.isChecked() == enabled:
            save_capture_diagnostics()

    window._sync_raw_capture_toggle.clicked.connect(save_raw_capture_diagnostics)
    form.addRow("수집 문제 해결:", raw_capture_row)
    performance_link = QCheckBox("성능도 함께 기록")
    performance_link.setToolTip("문제 해결이 켜져 있으면 서비스 소요 시간을 계정 로그 디렉터리에 저장합니다. 동기화, 전투 리포트, HUD를 따로 시작하지는 않습니다.")
    performance_link.clicked.connect(window.performance_controller.set_linked)
    def refresh_performance_link():
        performance_link.setChecked(window.performance_controller.snapshot()["linked"])
    window.performance_controller.changed.connect(refresh_performance_link)
    refresh_performance_link()
    form.addRow("성능 로그:", performance_link)
    card.layout().addLayout(form)
    return card


def _build_environment_card(window):
    card = window._card("환경 설정")
    window._environment_configuration_card = card
    npcap_title = QLabel("Npcap · 가방 동기화, 기본 전투 리포트")
    npcap_title.setStyleSheet(themed_style("font-weight:700;font-size:14px"))
    card.layout().addWidget(npcap_title)
    npcap_row = QHBoxLayout()
    npcap_install_button = QPushButton("Npcap 1.88 다운로드")
    window._npcap_install_button = npcap_install_button
    npcap_install_button.clicked.connect(window._open_npcap_download)
    npcap_row.addWidget(npcap_install_button)
    npcap_status_button = QPushButton("Npcap 상태 확인")
    npcap_status_button.clicked.connect(window._show_npcap_status)
    npcap_row.addWidget(npcap_status_button)
    window._nte_core_diagnostic_button = QPushButton("nte-core 진단")
    window._nte_core_diagnostic_button.clicked.connect(window._diagnose_nte_core)
    npcap_row.addWidget(window._nte_core_diagnostic_button)
    npcap_row.addStretch()
    card.layout().addLayout(npcap_row)

    equipment_title = QLabel("게임 내 컴포넌트 · 초고속 장착, 폐기 잠금, 플러그인, 기능 강화")
    equipment_title.setStyleSheet(themed_style("font-weight:700;font-size:14px"))
    card.layout().addWidget(equipment_title)
    equipment_description = QLabel(
        "<span style='color:#d29922'><b>위험 안내:</b> 구성 요소는 게임 안에 로드되며,"
        "게임 보호나 호환성 문제를 일으킬 수 있습니다.</span>"
    )
    equipment_description.setTextFormat(Qt.RichText)
    equipment_description.setWordWrap(True)
    equipment_description.setStyleSheet(
        themed_style("color:#8b949e;font-size:12px")
    )
    card.layout().addWidget(equipment_description)
    form = QFormLayout()
    window._equipment_plugin_loading_method_combo = NoWheelComboBox()
    window._equipment_plugin_loading_method_combo.addItem(
        "D3D 수집 프록시", "native-capture"
    )
    window._equipment_plugin_loading_method_combo.addItem(
        "네이티브 Loader (예비)", "loader"
    )
    loading_method = str(
        window.work_mode_service.deployment_record.get("loading_method")
        or "native-capture"
    )
    method_index = window._equipment_plugin_loading_method_combo.findData(
        loading_method
    )
    window._equipment_plugin_loading_method_combo.setCurrentIndex(
        max(0, method_index)
    )
    window._equipment_plugin_loading_method_combo.setFixedWidth(180)
    window._equipment_plugin_loading_method_combo.currentIndexChanged.connect(
        window._equipment_plugin_loading_method_changed
    )
    loading_row = QHBoxLayout()
    loading_row.addWidget(window._equipment_plugin_loading_method_combo)
    loading_hint = QLabel("사용할 수 없으면 예비 로드 방식을 선택하세요")
    loading_hint.setWordWrap(True)
    loading_hint.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    loading_row.addWidget(loading_hint, 1)
    form.addRow("로드 방식:", loading_row)
    window._equipment_plugin_game_executable_edit = QLineEdit()
    window._equipment_plugin_game_executable_edit.setPlaceholderText(
        "HTGame.exe의 전체 파일 경로를 직접 붙여넣을 수 있습니다"
    )
    window._equipment_plugin_game_executable_edit.setText(window.work_mode_service.settings.game_executable)
    window._equipment_plugin_game_executable_edit.editingFinished.connect(
        lambda: window.work_mode_service.set_game_executable(window._equipment_plugin_game_executable_edit.text().strip())
    )
    window._equipment_plugin_game_executable_edit.textChanged.connect(
        lambda _text: window._refresh_equipment_plugin_status()
    )
    game_picker = QPushButton("HTGame.exe 선택")
    game_picker.clicked.connect(window._select_equipment_plugin_game_executable)
    game_row = QHBoxLayout()
    game_row.addWidget(window._equipment_plugin_game_executable_edit, 1)
    game_row.addWidget(game_picker)
    window._equipment_plugin_detect_button = QPushButton("자동 감지")
    window._equipment_plugin_detect_button.clicked.connect(
        window._detect_equipment_plugin_game_executable
    )
    game_row.addWidget(window._equipment_plugin_detect_button)
    form.addRow("게임 실행 파일:", game_row)
    card.layout().addLayout(form)

    window._equipment_plugin_status_label = QLabel()
    window._equipment_plugin_status_label.setMinimumWidth(0)
    window._equipment_plugin_status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    window._equipment_plugin_status_label.setTextFormat(Qt.PlainText)
    window._equipment_plugin_status_label.setWordWrap(True)
    window._equipment_plugin_status_label.setStyleSheet(
        themed_style("color:#8b949e;font-size:12px")
    )
    card.layout().addWidget(window._equipment_plugin_status_label)

    actions = QHBoxLayout()
    window._equipment_plugin_primary_button = QPushButton("프록시 DLL 배포")
    window._equipment_plugin_primary_button.setObjectName("btnPrimary")
    window._equipment_plugin_primary_button.clicked.connect(
        window._activate_equipment_plugin_loading_method
    )
    actions.addWidget(window._equipment_plugin_primary_button)
    window._equipment_plugin_stop_button = QPushButton("게임 디렉터리 정리")
    window._equipment_plugin_stop_button.setObjectName("btnDanger")
    window._equipment_plugin_stop_button.clicked.connect(
        window._deactivate_equipment_plugin_loading_method
    )
    actions.addWidget(window._equipment_plugin_stop_button)
    actions.addStretch()
    card.layout().addLayout(actions)
    refresher = getattr(window, "_refresh_equipment_plugin_status", None)
    if callable(refresher):
        refresher()
    return card


def build_settings_page(
    window,
    app_version,
    app_context: AppContext,
    iter_image_files,
    netdisk_links=None,
):
    page = QWidget()
    page.setObjectName("settingsPage")
    scroll = QScrollArea()
    scroll.setObjectName("settingsScroll")
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    window._settings_scroll = scroll
    scroll.setStyleSheet(
        themed_style(
            "QScrollArea#settingsScroll{background:#0d1117;border:none}"
            "QWidget#settingsPage{background:#0d1117}"
        )
    )
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(16)

    mode_card = build_work_mode_card(window)
    layout.addWidget(mode_card)

    log_card = window._card("도구 설정")
    log_row = QHBoxLayout()
    log_row.addWidget(QLabel("실시간 로그 출력:"))
    log_toggle = QCheckBox("실행 로그 사용")
    log_toggle.setChecked(window._log_enabled)
    log_toggle.toggled.connect(window._toggle_log)
    window._log_toggle = log_toggle
    log_row.addWidget(log_toggle)
    window._log_session_status_label = QLabel()
    window._log_session_status_label.setStyleSheet(
        themed_style("color:#8b949e;font-size:12px")
    )
    log_row.addWidget(window._log_session_status_label)
    log_row.addStretch()
    log_card.layout().addLayout(log_row)
    window._refresh_log_session_status()

    protagonist_row = QHBoxLayout()
    protagonist_row.addWidget(QLabel("주인공 게임 이름:"))
    window._protagonist_game_name_edit = QLineEdit()
    window._protagonist_game_name_edit.setPlaceholderText("자동 장착 전용")
    protagonist_name_width = (
        window._protagonist_game_name_edit.fontMetrics().horizontalAdvance("零" * 8) + 36
    )
    window._protagonist_game_name_edit.setFixedWidth(protagonist_name_width)
    window._protagonist_game_name_edit.setText(
        str((getattr(window, "_ui_preferences", {}) or {}).get("protagonist_game_name") or "")
    )

    def save_protagonist_name() -> None:
        preferences = getattr(window, "_ui_preferences", None)
        if not isinstance(preferences, dict):
            return
        preferences["protagonist_game_name"] = window._protagonist_game_name_edit.text().strip()
        # A deliberate edit is an explicit answer, so later automatic
        # assembly should use it without asking again.
        preferences["skip_protagonist_name_prompt"] = bool(
            preferences["protagonist_game_name"]
        )
        window._save_ui_preferences()

    window._protagonist_game_name_edit.editingFinished.connect(save_protagonist_name)
    protagonist_row.addWidget(window._protagonist_game_name_edit)
    protagonist_row.addStretch()
    log_card.layout().addLayout(protagonist_row)

    theme_row = QHBoxLayout()
    theme_row.addWidget(QLabel("테마 색상:"))
    current_theme = getattr(window, "_theme_preference", "black")
    dark_radio = QRadioButton(THEME_LABELS["dark"])
    black_radio = QRadioButton(THEME_LABELS["black"])
    light_radio = QRadioButton(THEME_LABELS["light"])
    theme_radios = {"dark": dark_radio, "black": black_radio, "light": light_radio}
    current_radio = theme_radios.get(current_theme, theme_radios["black"])
    current_radio.setChecked(True)

    def select_theme(theme: str):
        if window._set_theme_preference(theme):
            return
        active_theme = getattr(window, "_theme_preference", "black")
        for value, radio in theme_radios.items():
            radio.blockSignals(True)
            radio.setChecked(value == active_theme)
            radio.blockSignals(False)

    dark_radio.toggled.connect(lambda checked: checked and select_theme("dark"))
    black_radio.toggled.connect(lambda checked: checked and select_theme("black"))
    light_radio.toggled.connect(lambda checked: checked and select_theme("light"))
    theme_row.addWidget(dark_radio)
    theme_row.addWidget(black_radio)
    theme_row.addWidget(light_radio)
    theme_row.addStretch()
    log_card.layout().addLayout(theme_row)
    layout.addWidget(log_card)

    sync_card = _build_capture_diagnostics_card(window)
    plugin_card = _build_environment_card(window)
    hotkey_card = window._card("단축키 설정")

    form = QFormLayout()
    form.setSpacing(10)

    cap_row = QHBoxLayout()
    cap_row.setSpacing(8)
    window._hk_capture_edit = QKeySequenceEdit(QKeySequence(window._hk_capture))
    window._hk_capture_edit.setMaximumWidth(160)
    cap_row.addWidget(QLabel("전역 스크린샷 키:"))
    cap_row.addWidget(window._hk_capture_edit)
    cap_row.addStretch()
    form.addRow(cap_row)

    finish_row = QHBoxLayout()
    finish_row.setSpacing(8)
    window._hk_finish_edit = QKeySequenceEdit(QKeySequence(window._hk_finish))
    window._hk_finish_edit.setMaximumWidth(160)
    finish_row.addWidget(QLabel("스크린샷 완료 키:"))
    finish_row.addWidget(window._hk_finish_edit)
    finish_row.addStretch()
    form.addRow(finish_row)

    stop_row = QHBoxLayout()
    stop_row.setSpacing(8)
    window._hk_stop_edit = QKeySequenceEdit(QKeySequence(window._hk_stop))
    window._hk_stop_edit.setMaximumWidth(160)
    stop_row.addWidget(QLabel("긴급 중지 키:"))
    stop_row.addWidget(window._hk_stop_edit)
    stop_row.addStretch()
    form.addRow(stop_row)

    rerecord_row = QHBoxLayout()
    rerecord_row.setSpacing(8)
    window._hk_battle_rerecord_edit = QKeySequenceEdit(
        QKeySequence(window._hk_battle_rerecord)
    )
    window._hk_battle_rerecord_edit.setMaximumWidth(160)
    window._hk_battle_rerecord_edit.setToolTip(
        "전투 리포트 수집 중에는 1.5초 안에 연속 두 번 눌러야 현재 전투 리포트를 버리고 다시 녹화합니다."
    )
    rerecord_row.addWidget(QLabel("전투 리포트 재녹화 키:"))
    rerecord_row.addWidget(window._hk_battle_rerecord_edit)
    rerecord_row.addStretch()
    form.addRow(rerecord_row)

    def save_hotkeys_when_complete(_sequence) -> None:
        # QKeySequenceEdit temporarily clears its value before it accepts a
        # replacement shortcut.  Do not persist that transient blank state.
        editors = (
            window._hk_capture_edit,
            window._hk_finish_edit,
            window._hk_stop_edit,
            window._hk_battle_rerecord_edit,
        )
        if all(editor.keySequence().toString().strip() for editor in editors):
            window._save_hotkeys()

    # Every completed edit is persisted immediately, removing a separate save
    # step without treating the editor's intermediate blank state as a value.
    for editor in (
        window._hk_capture_edit,
        window._hk_finish_edit,
        window._hk_stop_edit,
        window._hk_battle_rerecord_edit,
    ):
        editor.keySequenceChanged.connect(save_hotkeys_when_complete)

    hotkey_card.layout().addLayout(form)
    layout.addWidget(hotkey_card)

    update_card = window._card("소프트웨어 업데이트")
    window._update_status = QLabel(f"현재 버전: {app_version}")
    update_card.layout().addWidget(window._update_status)
    update_row = QHBoxLayout()
    update_row.setSpacing(10)
    window._check_update_btn = QPushButton("업데이트 확인")
    window._check_update_btn.setObjectName("btnPrimary")
    window._check_update_btn.clicked.connect(lambda: window._check_updates(manual=True))
    window._mirror_download_btn = QPushButton("Mirror 다운로드")
    window._mirror_download_btn.setObjectName("btnPrimary")
    window._mirror_download_btn.clicked.connect(window._start_mirror_download)
    home_btn = QPushButton("GitHub 다운로드")
    home_btn.clicked.connect(window._open_update_homepage)
    netdisk_btn = QPushButton("클라우드 드라이브 다운로드")
    netdisk_options = _normalize_netdisk_links(netdisk_links)
    netdisk_btn.clicked.connect(
        lambda: window._show_netdisk_download_dialog(netdisk_options)
        if hasattr(window, "_show_netdisk_download_dialog") and netdisk_options
        else None
    )
    update_row.addWidget(window._check_update_btn)
    update_row.addWidget(window._mirror_download_btn)
    update_row.addWidget(netdisk_btn)
    update_row.addWidget(home_btn)
    update_row.addStretch()
    update_card.layout().addLayout(update_row)
    mirror_cdk_row = QHBoxLayout()
    mirror_cdk_row.setSpacing(10)
    mirror_cdk_label = QLabel("Mirror CDK")
    window._mirror_cdk_edit = QLineEdit()
    window._mirror_cdk_edit.setPlaceholderText("Mirror CDK 입력 (다운로드 주소 요청에만 사용)")
    window._mirror_cdk_edit.setEchoMode(QLineEdit.Password)
    window._mirror_cdk_edit.setFixedWidth(360)
    window._mirror_cdk_edit.setText(str(window._update_config.get("mirror_cdk") or ""))
    window._mirror_cdk_edit.editingFinished.connect(window._save_mirror_cdk)
    mirror_cdk_row.addWidget(mirror_cdk_label)
    mirror_cdk_row.addWidget(window._mirror_cdk_edit)
    mirror_cdk_row.addStretch()
    update_card.layout().addLayout(mirror_cdk_row)
    layout.addWidget(update_card)

    about_card = window._card("정보")
    about_row = QHBoxLayout()
    about_row.setSpacing(10)
    author_bilibili_btn = QPushButton("제작자 Bilibili")
    author_bilibili_btn.clicked.connect(window._open_bilibili_homepage)
    project_btn = QPushButton("프로젝트 페이지")
    project_btn.clicked.connect(window._open_project_homepage)
    support_btn = QPushButton("후원하기")
    support_btn.clicked.connect(window._open_support_homepage)
    group_chat_btn = QPushButton("단체 채팅 참여")
    group_chat_btn.clicked.connect(window._show_group_chat_notice)
    about_row.addWidget(author_bilibili_btn)
    about_row.addWidget(project_btn)
    about_row.addWidget(support_btn)
    about_row.addWidget(group_chat_btn)
    about_row.addStretch()
    about_card.layout().addLayout(about_row)
    layout.addWidget(about_card)

    layout.addWidget(plugin_card)
    performance_card = window._card("성능 모니터링")
    performance_card.layout().addWidget(PerformanceCard(window.performance_controller))
    layout.addWidget(performance_card)
    layout.addWidget(sync_card)

    paths = _settings_paths(app_context)
    screenshot_dir = paths.screenshot_dir
    screenshot_files = iter_image_files(screenshot_dir)
    count = len(screenshot_files)
    size_mb = sum(f.stat().st_size for f in screenshot_files) / (1024 * 1024) if screenshot_files else 0

    screenshot_card = window._card("스크린샷 파일 관리")
    window._ss_info = QLabel(f"현재 스크린샷: {count}개 · {size_mb:.1f} MB")
    screenshot_card.layout().addWidget(window._ss_info)
    screenshot_row = QHBoxLayout()
    screenshot_row.setSpacing(10)
    actions = [
        ("모든 스크린샷 정리", window._clear_ss),
        (
            "폴더 열기",
            lambda: os.startfile(str(_settings_paths(app_context).screenshot_dir))
            if _settings_paths(app_context).screenshot_dir.exists()
            else None,
        ),
    ]
    for text, slot in actions:
        button = QPushButton(text)
        if "정리" in text:
            button.setObjectName("btnDanger")
        button.clicked.connect(slot)
        screenshot_row.addWidget(button)
    screenshot_row.addStretch()
    screenshot_card.layout().addLayout(screenshot_row)
    layout.addWidget(screenshot_card)

    quick_card = window._card("빠른 접근")
    quick_row = QHBoxLayout()
    quick_row.setSpacing(10)
    quick_paths = [
        ("config", lambda: _settings_paths(app_context).config_dir),
        ("accounts", lambda: _settings_paths(app_context).accounts_dir),
        ("logs", lambda: _settings_paths(app_context).log_dir),
    ]
    for label, path_factory in quick_paths:
        button = QPushButton(label)
        button.clicked.connect(lambda checked, pf=path_factory: os.startfile(str(pf())) if pf().exists() else None)
        quick_row.addWidget(button)
    quick_row.addStretch()
    quick_card.layout().addLayout(quick_row)
    layout.addWidget(quick_card)

    thanks_card = window._card("감사의 말")
    thanks_card.layout().setSpacing(12)
    thanks_row = QHBoxLayout()
    thanks_row.setSpacing(8)
    thanks_name = QLabel("이환 공방")
    thanks_name.setStyleSheet(
        themed_style(
            "color:#58a6ff;font-weight:700;background:#0d1f35;"
            "border:1px solid #1f6feb;border-radius:6px;padding:5px 10px"
        )
    )
    thanks_desc = QLabel("캐릭터 점수 기준과 스탯 가중치 참고 자료 제공")
    thanks_desc.setStyleSheet(
        themed_style(
            "color:#c9d1d9;background:#161b22;"
            "border:1px solid #30363d;border-radius:6px;padding:5px 10px"
        )
    )
    thanks_row.addWidget(thanks_name)
    thanks_row.addWidget(thanks_desc)
    thanks_row.addStretch()
    thanks_card.layout().addLayout(thanks_row)
    toolkit_row = QHBoxLayout()
    toolkit_row.setSpacing(8)
    toolkit_name = QLabel(
        '<a href="https://github.com/kongbaiz/nte-dps-toolkit" '
        'style="color:#58a6ff;text-decoration:none;">nte-dps-toolkit</a>'
    )
    toolkit_name.setTextFormat(Qt.RichText)
    toolkit_name.setTextInteractionFlags(Qt.TextBrowserInteraction)
    toolkit_name.setOpenExternalLinks(True)
    toolkit_name.setStyleSheet(
        themed_style(
            "font-weight:700;background:#0d1f35;border:1px solid #1f6feb;"
            "border-radius:6px;padding:5px 10px"
        )
    )
    toolkit_desc = QLabel("프로토콜 분석 코어 프로그램과 장착 플러그인 지원 제공")
    toolkit_desc.setStyleSheet(
        themed_style(
            "color:#c9d1d9;background:#161b22;"
            "border:1px solid #30363d;border-radius:6px;padding:5px 10px"
        )
    )
    toolkit_row.addWidget(toolkit_name)
    toolkit_row.addWidget(toolkit_desc)
    toolkit_row.addStretch()
    thanks_card.layout().addLayout(toolkit_row)
    layout.addWidget(thanks_card)

    layout.addStretch()
    window.work_mode_controller.attach_settings_targets(
        scroll=scroll, mode_card=mode_card, component_card=plugin_card,
        component_focus=window._equipment_plugin_primary_button,
        game_path_focus=window._equipment_plugin_game_executable_edit,
        loading_method_focus=window._equipment_plugin_loading_method_combo,
        npcap_focus=window._npcap_install_button,
        core_focus=window._nte_core_diagnostic_button,
    )
    return scroll
