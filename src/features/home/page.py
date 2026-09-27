# 构建并刷新首页工作台。
"""构建并刷新首页工作台。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.constants import APP_VERSION
from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.features.home.feature_guide import add_feature_guides
from src.ui.dashboard_widgets import metric_card, set_status_badge
from src.ui.image_scaling import asset_pixmap


_SYNC_ERROR_GUIDANCE = {
    "NPCAP_NOT_FOUND": (
        "원인: Npcap이 설치되지 않았거나 시스템이 Npcap 드라이버를 로드할 수 없습니다.\n"
        "처리: “환경 설정”을 클릭해 Npcap 영역에서 Npcap 1.88을 다운로드·설치한 뒤 가방 동기화를 다시 시작하세요."
    ),
    "GAME_PROCESS_NOT_FOUND": (
        "원인: 실행 중인 게임 프로세스를 감지하지 못했습니다.\n해결: 검사 상세를 확인하고, 컴포넌트와 게임 상태를 확인한 뒤 다시 동기화하세요."
    ),
    "CAPTURE_DEVICE_NOT_FOUND": (
        "원인: 설정한 패킷 캡처 네트워크 어댑터가 없거나, 현재 게임 연결에 사용할 수 있는 어댑터가 없습니다.\n"
        "처리: 설정의 “가방 동기화”에서 캡처 어댑터를 비워 자동 선택으로 복원하거나, 현재 유효한 어댑터를 입력한 뒤 다시 시도하세요."
    ),
    "SYSTEM_PROBE_FAILED": (
        "원인: Windows 네트워크 연결 또는 프로세스 탐지에 실패했습니다.\n"
        "처리: 게임과 이 프로그램을 닫았다가 다시 여세요. 계속 실패하면 보안 소프트웨어 차단 여부를 확인하고 관리자 권한으로 실행해 보세요."
    ),
    "CAPTURE_ALREADY_RUNNING": (
        "원인: nte-core에 이미 패킷 캡처 작업이 존재합니다.\n해결: 「다시 동기화」를 클릭해 이전 세션이 마무리되길 기다리세요. 그래도 복구되지 않으면 본 프로그램을 종료했다가 다시 여세요."
    ),
    "CAPTURE_NOT_RUNNING": ("원인: nte-core의 패킷 캡처 세션이 이미 중지되었습니다.\n해결: 「다시 동기화」를 클릭해 세션을 다시 만드세요."),
    "PROTOCOL_VERSION_MISMATCH": (
        "원인: 이 프로그램과 nte-core의 프로토콜 버전이 일치하지 않습니다.\n처리: 같은 배포 패키지의 전체 프로그램을 다시 설치하고, 이전 버전 nte-core.exe를 섞어 쓰지 마세요."
    ),
    "HANDSHAKE_REQUIRED": (
        "원인: 이 프로그램과 nte-core의 초기화 핸드셰이크가 완료되지 않았습니다.\n처리: 프로그램을 재시작하고, 계속 실패하면 전체 배포 패키지를 다시 설치하세요."
    ),
    "INVENTORY_NOT_READY": (
        "원인: 아직 완전한 가방 데이터를 받지 못했습니다.\n해결: 검사 상세를 확인하고, 현재 동기화 소스의 안내에 따라 준비를 마치세요."
    ),
    "NteCoreNotFoundError": ("원인: 프로그램 디렉터리에 nte-core.exe가 없습니다.\n처리: 전체 배포 패키지를 다시 설치하고, 메인 프로그램만 따로 복사해 실행하지 마세요."),
    "NteCoreTimeoutError": (
        "원인: nte-core가 제한 시간 내에 응답하지 않았습니다.\n처리: 프로그램을 재시작하고, 계속 실패하면 보안 소프트웨어가 nte-core.exe를 차단하는지 확인하세요."
    ),
    "NteCoreProcessError": (
        "원인: nte-core.exe를 시작할 수 없거나 시작 후 비정상 종료되었습니다.\n"
        "처리: 보안 소프트웨어 격리 기록과 프로그램 디렉터리 권한을 확인한 뒤 전체 배포 패키지를 다시 설치하세요."
    ),
    "SNAPSHOT_SAVE_FAILED": (
        "원인: 안정 가방을 받았지만 현재 계정 데이터베이스에 저장하지 못했으며, 구체적인 원인은 아직 확인되지 않았습니다.\n"
        "처리: 백그라운드에서 자동으로 재시도합니다; 계속 실패하면 오류 코드와 계정 로그를 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_BUSY": (
        "원인: 현재 계정 데이터베이스에 잠금 충돌이 있어 대기 후에도 저장을 완료하지 못했습니다.\n"
        "처리: 다른 계산기 창과 해당 데이터베이스를 조작 중인 도구를 종료한 후 다시 시도하세요; 계속 실패하면 오류 코드를 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_READONLY": (
        "원인: SQLite가 현재 계정 데이터베이스에 기록할 수 없다고 보고했습니다 (읽기 전용).\n"
        "처리: 계정 데이터베이스와 해당 디렉터리의 쓰기 권한·읽기 전용 상태·보안 소프트웨어 차단 기록을 확인하세요."
    ),
    "SNAPSHOT_SAVE_PERMISSION": (
        "원인: SQLite가 이번 데이터베이스 접근 작업을 거부했습니다.\n"
        "처리: 계정 데이터 디렉터리 권한과 보안 소프트웨어 차단 기록을 확인하세요; 계속 실패하면 오류 코드를 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_FULL": (
        "원인: SQLite가 데이터베이스 또는 디스크가 가득 찼다고 보고했습니다. 데이터베이스 용량 한도에 도달했을 수도 있습니다.\n"
        "처리: 계정 데이터 드라이브와 시스템 임시 디렉터리가 있는 드라이브의 남은 공간을 확인하세요; 공간이 충분하다면 오류 코드를 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_CORRUPT": (
        "원인: SQLite가 데이터베이스 손상을 감지했거나, 파일이 유효한 SQLite 데이터베이스가 아닙니다.\n"
        "처리: 동기화를 중지하고 프로그램을 종료해 원래 계정 데이터를 보존하세요; 백업한 뒤 점검하고 오류 코드를 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_IO": (
        "원인: 계정 데이터를 저장할 때 하위 수준 읽기/쓰기 오류가 발생했습니다. 이 오류만으로 공간 부족이라고 판단할 수는 없습니다.\n"
        "처리: 저장 장치와 보안 소프트웨어의 차단 기록을 확인하세요; 원래 계정 데이터를 보존하고 오류 코드를 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_OPEN": (
        "원인: SQLite가 저장에 필요한 데이터베이스 또는 보조 파일을 열 수 없습니다.\n"
        "처리: 계정 데이터 디렉터리가 쓰기 가능한지, 저장 장치가 온라인 상태인지 확인하세요; 계속 실패하면 오류 코드를 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_SCHEMA": (
        "원인: 데이터베이스 구조가 바뀌었거나, 저장에 필요한 테이블·필드가 프로그램과 일치하지 않습니다.\n"
        "처리: 원래 계정 데이터를 보존하고, 데이터베이스 업그레이드를 점검할 수 있도록 프로그램 버전과 오류 코드를 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_CONSTRAINT": (
        "원인: 쓰기 작업이 데이터베이스 제약 조건 충돌을 일으켰습니다. 예를 들어 외래 키·고유성·NOT NULL 또는 필드 규칙입니다.\n"
        "처리: 원래 계정 데이터를 보존하고 오류 코드를 제보해 주세요; 데이터베이스를 반복해서 덮어쓰지 마세요."
    ),
    "SNAPSHOT_SAVE_TRANSACTION": (
        "원인: 계정 데이터베이스의 트랜잭션 상태가 비정상입니다.\n"
        "처리: 프로그램을 종료했다가 다시 연 후 재시도하세요; 계속 실패하면 오류 코드와 계정 로그를 제보해 주세요."
    ),
}

_WORKBENCH_VERSION = ".".join(APP_VERSION.split(".")[:2])


def inventory_sync_error_guidance(error_code: str | None, error: str | None, *, capture_source: str) -> str:
    """Translate sync failures into concrete user actions while retaining diagnostics."""
    code = str(error_code or "").strip()
    if code == "GAME_PROCESS_NOT_FOUND":
        if capture_source == "native":
            return (
                "원인: 실행 중인 게임 프로세스를 감지하지 못했습니다.\n"
                "처리: 컴포넌트를 배포하거나 업데이트해야 하면 게임을 완전히 종료한 상태에서 배포를 마친 뒤 게임을 시작하세요;"
                "컴포넌트 배포가 끝난 후에는 로그인하여 게임 장면에 진입하고, 동기화가 완료될 때까지 기다려 주세요."
            )
        if capture_source == "packet":
            return "원인: 실행 중인 게임 프로세스를 감지하지 못했습니다.\n해결: 먼저 게임을 시작해 로그인 화면에 머무르고, 패킷 캡처 리스너가 준비된 후 로그인하세요."
    if code == "INVENTORY_NOT_READY":
        if capture_source == "native":
            return (
                "원인: 아직 완전한 가방 데이터를 읽지 못했습니다.\n"
                "처리: 로그인해 게임 씬에 진입한 뒤 전체 데이터 동기화를 기다리세요; 이미 진입했다면 잠시 기다리고, 오랫동안 복구되지 않으면 검사 상세 정보를 확인하거나 동기화를 다시 시작하세요."
            )
        if capture_source == "packet":
            return "원인: 아직 완전한 가방 데이터를 캡처하지 못했습니다.\n해결: 로그인 화면으로 돌아가 패킷 캡처 리스너가 준비된 후 다시 로그인하고, 가방 수량이 안정될 때까지 기다리세요."
    if code in _SYNC_ERROR_GUIDANCE:
        return _SYNC_ERROR_GUIDANCE[code]
    detail = str(error or "").lower()
    if "permission denied" in detail or "access is denied" in detail:
        return "원인: 프로그램에 구성 요소를 시작하거나 계정 데이터를 기록할 권한이 없습니다.\n처리: 프로그램과 계정 데이터 디렉터리 권한을 확인하고 관리자 권한으로 실행해 보세요."
    if "database is locked" in detail:
        return (
            "원인: 현재 계정 데이터베이스를 다른 프로그램 또는 이 프로그램의 잔여 프로세스가 사용 중입니다.\n"
            "처리: 다른 NTE Drive Calc 창을 닫고 프로그램을 재시작한 뒤 다시 동기화하세요."
        )
    if "no space" in detail or "disk full" in detail:
        return "원인: 디스크 공간이 부족합니다.\n처리: 프로그램 또는 계정 데이터가 있는 드라이브를 정리한 뒤 다시 동기화하세요."
    return (
        "원인: 가방 동기화 구성 요소에서 분류되지 않은 오류가 발생했습니다.\n"
        "처리: 먼저 동기화를 중지했다가 다시 시작하세요; 그래도 실패하면 검사 상세 정보와 계정 로그를 확인하고 오류 코드를 제보해 주세요."
    )


def _section(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)
    title_label = QLabel(title)
    title_label.setObjectName("cardTitle")
    layout.addWidget(title_label)
    if description:
        subtitle = QLabel(description)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(subtitle)
    return card, layout


def _home_sync_help_text(mode: str) -> str:
    """Keep the next action clear for the current synchronization source."""

    if mode == "offline":
        return (
            "현재 오프라인 모드입니다. 저장된 데이터만 사용합니다.\n"
            '동기화하려면: 먼저 "작업 모드 설정"에서 동기화 가능한 모드를 선택해 확인하고,'
            "그런 다음 워크스페이스로 돌아가 「자동 동기화」를 켜세요."
        )
    if mode in {"medium", "developer"}:
        return (
            "1. “자동 동기화”를 켜고 안내에 따라 구성 요소를 준비하세요; 배포가 필요하면 먼저 게임을 종료하세요 (Loader는 런처도 종료해야 함).\n"
            "2. 로그인해 게임 장면에 진입하고 가방과 캐릭터 데이터가 저장될 때까지 기다리세요.\n"
            "3. 데이터가 갱신되지 않나요? “동기화 재시작”을 클릭하세요; 문제가 계속되면 “검사 상세”를 확인하세요."
        )
    return (
        "1. “자동 동기화”를 켜고 안내에 따라 패킷 캡처 환경을 확인하세요.\n"
        "2. 게임을 시작하고 패킷 캡처 감시가 준비된 뒤 로그인하세요; 전체 가방은 자동으로 저장됩니다.\n"
        "3. 데이터가 갱신되지 않나요? “동기화 재시작”을 클릭하고 안내에 따라 다시 로그인하세요; 문제가 계속되면 “검사 상세”를 확인하세요."
    )


def _show_home_sync_help(window) -> None:
    mode = getattr(window.work_mode_service.settings.mode, "value", "offline")
    dialog = QDialog(window)
    dialog.setObjectName("homeSyncHelpDialog")
    dialog.setWindowTitle("자동 동기화 · 사용 방법")
    dialog.setWindowModality(Qt.WindowModal)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 16)
    layout.setSpacing(18)
    instructions = QLabel(_home_sync_help_text(str(mode)), dialog)
    instructions.setObjectName("homeSyncHelpInstructions")
    instructions.setWordWrap(True)
    instructions.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    layout.addWidget(instructions)
    actions = QHBoxLayout()
    actions.addStretch()
    selected = {"target": ""}

    def choose(target: str) -> None:
        selected["target"] = target
        dialog.accept()

    mode_button = QPushButton("작업 모드 설정", dialog)
    mode_button.setObjectName("homeSyncHelpModeSettings")
    mode_button.clicked.connect(lambda: choose("mode"))
    actions.addWidget(mode_button)
    environment_button = QPushButton("환경 설정", dialog)
    environment_button.setObjectName("homeSyncHelpEnvironmentSettings")
    environment_button.clicked.connect(lambda: choose("game_path"))
    actions.addWidget(environment_button)
    close_button = QPushButton("닫기", dialog)
    close_button.setDefault(True)
    close_button.setFocus()
    close_button.clicked.connect(dialog.reject)
    actions.addWidget(close_button)
    layout.addLayout(actions)
    fit_dialog_to_available_screen(dialog, QSize(570, 250))
    if dialog.exec() == QDialog.Accepted and selected["target"]:
        window.work_mode_controller.open_settings(selected["target"])


def build_home_page(window) -> QScrollArea:
    page = QWidget()
    page.setObjectName("homePage")
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)

    root = QVBoxLayout(page)
    root.setContentsMargins(22, 18, 22, 22)
    root.setSpacing(16)

    hero = QFrame()
    hero.setObjectName("homeHero")
    hero.setStyleSheet(themed_style("QFrame#homeHero{background:#10243f;border:1px solid #1f6feb;border-radius:12px}"))
    hero_layout = QHBoxLayout(hero)
    hero_layout.setContentsMargins(22, 18, 22, 18)
    title_column = QVBoxLayout()
    title = QLabel(f"NTE Drive Calc {_WORKBENCH_VERSION} 작업 공간")
    title.setStyleSheet(themed_style("color:#f0f6fc;font-size:21px;font-weight:700"))
    window.home_account_label = QLabel("계정 데이터를 읽는 중…")
    window.home_account_label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    title_column.addWidget(title)
    title_column.addWidget(window.home_account_label)
    hero_layout.addLayout(title_column)
    hero_layout.addStretch()
    # 工作台头像取已核验角色目录中的黑羽 256px 图片，不替换战报数据集。
    hero_icon_path = GameUiAssetCatalog(
        window.app_context.paths.cultivation_asset_root
    ).character_icon(1042)
    if hero_icon_path is not None:
        hero_icon = QLabel()
        hero_icon.setObjectName("homeHeroAvatar")
        hero_icon.setFixedSize(72, 72)
        hero_icon.setPixmap(asset_pixmap(
            hero_icon_path, 72, hero_icon.devicePixelRatioF()
        ))
        hero_icon.setStyleSheet("background:transparent")
        hero_layout.addWidget(hero_icon)
    window.home_sync_badge = QLabel("시작 전")
    window.home_sync_badge.setAlignment(Qt.AlignCenter)
    set_status_badge(window.home_sync_badge, "시작 전", "neutral")
    hero_layout.addWidget(window.home_sync_badge)
    root.addWidget(hero)

    window.home_upgrade_banner = QFrame(page)
    window.home_upgrade_banner.setObjectName("homeUpgradeBanner")
    window.home_upgrade_banner.setStyleSheet(themed_style(
        "QFrame#homeUpgradeBanner{background:#2a2112;border:1px solid #d29922;border-radius:8px}"
    ))
    upgrade_row = QHBoxLayout(window.home_upgrade_banner)
    upgrade_row.setContentsMargins(16, 12, 16, 12)
    window.home_upgrade_summary = QLabel("구버전 컴포넌트 업그레이드: 안내에 따라 진행하세요.", window.home_upgrade_banner)
    window.home_upgrade_summary.setWordWrap(True)
    upgrade_row.addWidget(window.home_upgrade_summary, 1)
    upgrade_button = QPushButton("계속 처리", window.home_upgrade_banner)
    upgrade_button.setObjectName("btnNew")
    upgrade_button.clicked.connect(window.work_mode_controller.show_upgrade_guide)
    upgrade_row.addWidget(upgrade_button)
    window.home_upgrade_banner.hide()
    root.addWidget(window.home_upgrade_banner)

    metrics = QGridLayout()
    metrics.setHorizontalSpacing(12)
    metrics.setVerticalSpacing(12)
    definitions = (
        ("inventory", "안정 가방", "첫 동기화 대기 중"),
        ("module", "드라이브", "현재 안정 가방"),
        ("core", "카트리지", "현재 안정 가방"),
        ("equipped", "장착됨", "현재 안정 스냅샷 기준"),
        ("plans", "장비 세팅 방안", "현재 계정에 저장됨"),
        ("characters", "캐릭터 카탈로그", "현재 계정은 아직 캐릭터를 동기화하지 않음"),
    )
    window.home_metric_labels = {}
    for index, (key, label, subtitle) in enumerate(definitions):
        card, value_label, subtitle_label = metric_card(label, "—", subtitle)
        window.home_metric_labels[key] = (value_label, subtitle_label)
        metrics.addWidget(card, index // 3, index % 3)
    root.addLayout(metrics)

    sync_card, sync_layout = _section("게임 데이터 동기화")
    window.home_sync_title = sync_layout.itemAt(0).widget()
    # 保留兼容投影供控制器和诊断读取，常态页面只展示当前同步状态。
    window.home_sync_source_label = QLabel("", sync_card)
    window.home_sync_source_label.hide()
    window.home_sync_detail = QLabel("가방 동기화가 아직 시작되지 않았습니다")
    window.home_sync_detail.setWordWrap(True)
    sync_layout.addWidget(window.home_sync_detail)
    window.home_character_sync_detail = QLabel("캐릭터 육성: 아직 저장된 게임 육성 데이터가 없습니다.")
    window.home_character_sync_detail.setWordWrap(True)
    window.home_character_sync_detail.setProperty("savedSummary", window.home_character_sync_detail.text())
    sync_layout.addWidget(window.home_character_sync_detail)
    window.home_character_sync_detail.hide()
    sync_actions = QHBoxLayout()
    window.home_auto_sync_toggle = QCheckBox("자동 동기화")
    window.home_auto_sync_toggle.setChecked(window.work_mode_service.settings.auto_sync_enabled)
    window.home_auto_sync_toggle.toggled.connect(window.auto_sync_controller.set_enabled)
    window.home_restart_sync_button = QPushButton("동기화 재시작")
    window.home_restart_sync_button.setObjectName("btnPrimary")
    window.home_restart_sync_button.clicked.connect(window.auto_sync_controller.open_restart)
    check = QPushButton("검사 상세")
    check.setObjectName("btnNew")
    check.clicked.connect(lambda: window.work_mode_controller.check(show=True))
    window.home_sync_help_button = QPushButton("사용 방법")
    window.home_sync_help_button.clicked.connect(lambda: _show_home_sync_help(window))
    sync_actions.addWidget(window.home_auto_sync_toggle)
    sync_actions.addWidget(window.home_restart_sync_button)
    sync_actions.addWidget(check)
    sync_actions.addWidget(window.home_sync_help_button)
    sync_actions.addStretch()
    sync_layout.addLayout(sync_actions)
    window.home_sync_action_hint = QLabel("")
    window.home_sync_action_hint.setWordWrap(True)
    sync_layout.addWidget(window.home_sync_action_hint)
    # 保留兼容投影供控制器与测试读取，页面由“稳定背包”指标卡统一展示保存时间。
    window.home_last_sync_label = QLabel("아직 저장된 가방이 없습니다", sync_card)
    window.home_last_sync_label.hide()
    root.addWidget(sync_card)

    guides_card, guides_layout = _section("기능 설명")
    add_feature_guides(guides_layout, window, window._go)
    root.addWidget(guides_card)

    root.addStretch()
    return scroll


def _local_snapshot_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def refresh_home_page(window, dashboard: dict[str, Any]) -> None:
    account = dashboard["account"]
    inventory = dashboard.get("inventory")
    window.home_account_label.setText(f"현재 계정: {account['account_name']} · 가방, 캐릭터 육성, 장비 구성 방안은 독립적으로 저장됩니다")

    values = {
        "inventory": int(inventory["stored_item_count"]) if inventory else 0,
        "module": int(inventory["module_count"]) if inventory else 0,
        "core": int(inventory["core_count"]) if inventory else 0,
        "equipped": int(inventory["equipped_count"]) if inventory else 0,
        "plans": int(dashboard["loadout_plan_count"]),
        "characters": int(dashboard["characters"]["catalog_count"]),
    }
    for key, value in values.items():
        window.home_metric_labels[key][0].setText(str(value))

    synced_count = int(dashboard["characters"]["synced_count"])
    window.home_metric_labels["characters"][1].setText(
        f"현재 계정에 캐릭터 {synced_count}명 동기화됨" if synced_count else "현재 계정은 아직 캐릭터를 동기화하지 않음"
    )
    profile_count = int(dashboard["characters"]["profile_count"])
    role_detail = (f"캐릭터 육성: {profile_count}명 캐릭터의 확인된 육성 필드를 저장했습니다." if profile_count else
                   "캐릭터 육성: 아직 저장된 게임 육성 데이터가 없습니다. 연결하면 자동으로 읽어옵니다.")
    window.home_character_sync_detail.setProperty("savedSummary", role_detail)
    window.home_character_sync_detail.setText(role_detail)

    inventory_subtitle = window.home_metric_labels["inventory"][1]
    if inventory:
        saved_time = _local_snapshot_time(inventory["captured_at_utc"])
        inventory_subtitle.setText(f"스냅샷 #{inventory['snapshot_id']} · {saved_time}")
    else:
        inventory_subtitle.setText("첫 동기화 대기 중")
    if inventory:
        window.home_last_sync_label.setText(
            f"마지막 저장 (로컬 시간): {saved_time} · 드라이브 {inventory['module_count']}개"
            f" · 콘솔 {inventory['core_count']}개"
        )
        window.home_last_sync_label.setToolTip(
            "현재 가방 스냅샷의 저장 시간입니다. 동기화 내용에 변화가 없으면 기존 스냅샷을 그대로 사용하며 저장 시간은 갱신되지 않습니다; 이번 동기화 상태는 위쪽을 참조하세요."
        )
    else:
        window.home_last_sync_label.setText("아직 저장된 가방이 없습니다")
        window.home_last_sync_label.setToolTip("")
    window.auto_sync_controller.render()
