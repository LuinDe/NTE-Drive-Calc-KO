# 构建并刷新首页工作台。
"""构建并刷新首页工作台。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
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
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.ui.dashboard_widgets import metric_card, set_status_badge


_SYNC_ERROR_GUIDANCE = {
    "NPCAP_NOT_FOUND": (
        "원인: Npcap이 설치되지 않았거나 시스템이 Npcap 드라이버를 로드할 수 없습니다.\n"
        "처리: “환경 설정”을 클릭해 Npcap 영역에서 Npcap 1.88을 다운로드·설치한 뒤 가방 동기화를 다시 시작하세요."
    ),
    "GAME_PROCESS_NOT_FOUND": (
        "원인: 실행 중인 게임 프로세스를 감지하지 못했습니다.\n처리: 먼저 게임을 실행해 로그인 화면에 머문 뒤 가방 동기화를 다시 시작하세요."
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
        "원인: nte-core에 이미 패킷 캡처 작업이 있습니다.\n처리: 먼저 “동기화 중지”를 클릭하고, 상태가 복구되지 않으면 프로그램을 재시작한 뒤 다시 동기화하세요."
    ),
    "CAPTURE_NOT_RUNNING": ("원인: nte-core의 패킷 캡처 세션이 이미 중지되었습니다.\n처리: “가방 동기화 시작”을 클릭해 세션을 다시 만드세요."),
    "PROTOCOL_VERSION_MISMATCH": (
        "원인: 이 프로그램과 nte-core의 프로토콜 버전이 일치하지 않습니다.\n처리: 같은 배포 패키지의 전체 프로그램을 다시 설치하고, 이전 버전 nte-core.exe를 섞어 쓰지 마세요."
    ),
    "HANDSHAKE_REQUIRED": (
        "원인: 이 프로그램과 nte-core의 초기화 핸드셰이크가 완료되지 않았습니다.\n처리: 프로그램을 재시작하고, 계속 실패하면 전체 배포 패키지를 다시 설치하세요."
    ),
    "INVENTORY_NOT_READY": (
        "원인: 아직 완전한 가방 데이터를 캡처하지 못했습니다.\n처리: 게임 로그인 화면에서 동기화를 시작한 뒤 게임에 접속하고, 가방 수량이 안정될 때까지 기다리세요."
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
        "처리: 아래 기술 상세와 계정 로그의 저장 실패 기록을 제보해 주세요. 백그라운드에서 자동으로 재시도합니다."
    ),
    "SNAPSHOT_SAVE_BUSY": (
        "원인: 현재 계정 데이터베이스에 잠금 충돌이 있어 대기 후에도 저장을 완료하지 못했습니다.\n"
        "처리: 다른 계산기 창과 해당 데이터베이스를 사용 중인 도구를 종료한 뒤 다시 시도하세요. 계속 실패하면 기술 상세를 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_READONLY": (
        "원인: SQLite가 현재 계정 데이터베이스에 기록할 수 없다고 보고했습니다 (읽기 전용).\n"
        "처리: 계정 데이터베이스와 해당 디렉터리의 쓰기 권한·읽기 전용 상태·보안 소프트웨어 차단 기록을 확인하세요."
    ),
    "SNAPSHOT_SAVE_PERMISSION": (
        "원인: SQLite가 이번 데이터베이스 접근 작업을 거부했습니다.\n"
        "처리: 계정 데이터 디렉터리 접근 권한과 보안 소프트웨어 차단 기록을 확인하고 기술 상세를 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_FULL": (
        "원인: SQLite가 데이터베이스 또는 디스크가 가득 찼다고 보고했습니다. 데이터베이스 용량 한도에 도달했을 수도 있습니다.\n"
        "처리: 계정 데이터가 있는 드라이브와 시스템 임시 디렉터리가 있는 드라이브의 공간을 확인하세요. 공간이 충분하면 기술 상세를 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_CORRUPT": (
        "원인: SQLite가 데이터베이스 손상을 감지했거나, 파일이 유효한 SQLite 데이터베이스가 아닙니다.\n"
        "처리: 동기화를 중지하고 프로그램을 종료하세요. 기존 계정 데이터는 그대로 두고, 백업한 뒤 확인하거나 복구하세요. 기술 상세도 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_IO": (
        "원인: 계정 데이터를 저장할 때 하위 수준 읽기/쓰기 오류가 발생했습니다. 이 오류만으로 공간 부족이라고 판단할 수는 없습니다.\n"
        "처리: 저장 장치, 파일 시스템, 보안 소프트웨어 차단 기록을 확인하세요. 기존 계정 데이터는 그대로 두고 기술 상세를 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_OPEN": (
        "원인: SQLite가 저장에 필요한 데이터베이스 또는 보조 파일을 열 수 없습니다.\n"
        "처리: 계정 데이터 디렉터리에 접근·쓰기가 가능한지, 저장 장치가 연결되어 있는지 확인하고 기술 상세를 제보해 주세요."
    ),
    "SNAPSHOT_SAVE_SCHEMA": (
        "원인: 데이터베이스 구조가 바뀌었거나, 저장에 필요한 테이블·필드가 프로그램과 일치하지 않습니다.\n"
        "처리: 기존 계정 데이터베이스를 그대로 두고 프로그램 버전과 기술 상세를 제보해 주시면 데이터베이스 업그레이드나 구조 이상을 확인할 수 있습니다."
    ),
    "SNAPSHOT_SAVE_CONSTRAINT": (
        "원인: 쓰기 작업이 데이터베이스 제약 조건 충돌을 일으켰습니다. 예를 들어 외래 키·고유성·NOT NULL 또는 필드 규칙입니다.\n"
        "처리: 기존 계정 데이터베이스를 그대로 두고 기술 상세의 SQLite 오류 이름과 실패 단계를 제보해 주시면 충돌 지점을 찾을 수 있습니다."
    ),
    "SNAPSHOT_SAVE_TRANSACTION": (
        "원인: 계정 데이터베이스의 트랜잭션 상태가 비정상입니다.\n"
        "처리: 프로그램을 종료했다가 다시 연 뒤 재시도하세요. 계속 실패하면 기술 상세의 실패 단계와 롤백 상태를 제보해 주세요."
    ),
}

_WORKBENCH_VERSION = ".".join(APP_VERSION.split(".")[:2])


def inventory_sync_error_guidance(error_code: str | None, error: str | None) -> str:
    """Translate sync failures into concrete user actions while retaining diagnostics."""
    code = str(error_code or "").strip()
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
        "처리: 먼저 동기화를 중지했다가 다시 시작하세요. 계속 실패하면 아래 기술 상세와 로그를 확인한 뒤 전체 오류를 제보해 주세요."
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
    # 工作台使用灵可的正式头像。
    hero_icon_path = GameUiAssetCatalog(
        window.app_context.paths.asset_dir / "game_ui"
    ).character_icon(1072)
    if hero_icon_path is not None:
        hero_icon = QLabel()
        hero_icon.setObjectName("homeHeroAvatar")
        hero_icon.setFixedSize(72, 72)
        hero_icon.setPixmap(
            QPixmap(str(hero_icon_path)).scaled(
                72,
                72,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )
        hero_icon.setStyleSheet("background:transparent")
        hero_layout.addWidget(hero_icon)
    window.home_sync_badge = QLabel("시작 전")
    window.home_sync_badge.setAlignment(Qt.AlignCenter)
    set_status_badge(window.home_sync_badge, "시작 전", "neutral")
    hero_layout.addWidget(window.home_sync_badge)
    root.addWidget(hero)

    metrics = QGridLayout()
    metrics.setHorizontalSpacing(12)
    metrics.setVerticalSpacing(12)
    definitions = (
        ("inventory", "안정 가방", "첫 동기화 대기 중"),
        ("module", "드라이브", "원본 게임 UID"),
        ("core", "카트리지", "원본 게임 UID"),
        ("equipped", "장착됨", "현재 안정 스냅샷 기준"),
        ("plans", "장비 세팅 방안", "현재 계정에 저장됨"),
        ("characters", "캐릭터 데이터", "프로그램에 포함된 정적 데이터베이스에서 가져옴"),
    )
    window.home_metric_labels = {}
    for index, (key, label, subtitle) in enumerate(definitions):
        card, value_label, subtitle_label = metric_card(label, "—", subtitle)
        window.home_metric_labels[key] = (value_label, subtitle_label)
        metrics.addWidget(card, index // 3, index % 3)
    root.addLayout(metrics)

    sync_card, sync_layout = _section(
        "가방 동기화",
        "프록시와 가속기를 끄고 게임 로그인 화면에 머문 뒤 동기화를 시작하고 게임에 접속하세요. 그동안 네트워크가 원활한지 꼭 확인하세요!!!",
    )
    window.home_sync_detail = QLabel("nte-core가 아직 시작되지 않음")
    window.home_sync_detail.setWordWrap(True)
    sync_layout.addWidget(window.home_sync_detail)
    sync_actions = QHBoxLayout()
    window.home_start_sync_button = QPushButton("가방 동기화 시작")
    window.home_start_sync_button.setObjectName("btnPrimary")
    window.home_start_sync_button.clicked.connect(window._start_inventory_sync)
    window.home_stop_sync_button = QPushButton("동기화 중지")
    window.home_stop_sync_button.clicked.connect(window._stop_inventory_sync)
    window.home_stop_sync_button.setEnabled(False)
    environment_button = QPushButton("환경 설정")
    environment_button.clicked.connect(window._focus_environment_configuration)
    sync_actions.addWidget(window.home_start_sync_button)
    sync_actions.addWidget(window.home_stop_sync_button)
    sync_actions.addWidget(environment_button)
    sync_actions.addStretch()
    sync_layout.addLayout(sync_actions)
    root.addWidget(sync_card)

    actions_card, actions_layout = _section("빠른 작업")
    actions = QHBoxLayout()
    for label, page_key in (
        ("장비 세팅 계산", "execute"),
        ("방안 보기", "equipment"),
        ("캐릭터 한계 이득", "my_role"),
        ("창고 관리", "warehouse"),
        ("콘솔 감정", "identify"),
    ):
        button = QPushButton(label)
        button.clicked.connect(lambda _checked=False, key=page_key: window._go(key))
        actions.addWidget(button)
    actions.addStretch()
    actions_layout.addLayout(actions)
    root.addWidget(actions_card)

    root.addStretch()
    return scroll


def refresh_home_page(window, dashboard: dict[str, Any]) -> None:
    account = dashboard["account"]
    inventory = dashboard.get("inventory")
    window.home_account_label.setText(f"현재 계정: {account['account_name']} · 데이터베이스는 이 계정의 안정 가방과 방안만 저장합니다")

    values = {
        "inventory": int(inventory["stored_item_count"]) if inventory else 0,
        "module": int(inventory["module_count"]) if inventory else 0,
        "core": int(inventory["core_count"]) if inventory else 0,
        "equipped": int(inventory["equipped_count"]) if inventory else 0,
        "plans": int(dashboard["loadout_plan_count"]),
        "characters": int(dashboard["static"]["counts"]["character"]),
    }
    for key, value in values.items():
        window.home_metric_labels[key][0].setText(str(value))

    inventory_subtitle = window.home_metric_labels["inventory"][1]
    if inventory:
        inventory_subtitle.setText(f"스냅샷 #{inventory['snapshot_id']} · {inventory['captured_at_utc']}")
    else:
        inventory_subtitle.setText("첫 동기화 대기 중")
