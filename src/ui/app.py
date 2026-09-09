# PySide6 主窗口入口和功能模块挂载。
"""NTE Drive Calc - PySide6 Desktop Application"""

import sys, os, threading, ctypes
from pathlib import Path
from typing import Optional

if getattr(sys, "frozen", False):
    _PACKAGE_ROOT = Path(sys._MEIPASS)
    _APP_DIR = Path(sys.executable).parent
else:
    _PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
    _APP_DIR = _PACKAGE_ROOT
sys.path.insert(0, str(_PACKAGE_ROOT))

from src.app.context import (
    AccountChangedEvent,
    AppContext,
    ApplicationPaths,
    CallbackAccountLifecycle,
)
from src.app.shared_data_seed import seed_shared_database
from src.services.character_shape_bonus_service import SHARED_DATABASE_ENV
from src.services.workshop_weight_template_service import (
    WORKSHOP_WEIGHT_TEMPLATE_ENV,
    start_workshop_weight_template_refresh,
)
from src.app.constants import (
    ACCOUNT_USER_FILES,
    APP_VERSION,
    CORE_CONFIG_FILES,
)
from src.app.theme import (
    apply_app_theme,
    install_dialog_defaults,
)

_BUNDLED_CONFIG_DIR = _PACKAGE_ROOT / "config"
_ASSET_DIR = _PACKAGE_ROOT / "assets"
_APP_ICON_PATH = _ASSET_DIR / "app_icon.ico"


def _select_data_root() -> Path:
    candidates = [_APP_DIR]
    if getattr(sys, "frozen", False):
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            candidates.append(Path(local_appdata) / "NTE Drive Calc")

    for base in candidates:
        try:
            for subdir in ("config", "scanned_images", "logs"):
                (base / subdir).mkdir(parents=True, exist_ok=True)
            probe = base / "config" / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return base
        except Exception:
            continue
    raise RuntimeError("쓰기 가능한 데이터 디렉터리를 만들 수 없습니다. 설치 디렉터리 또는 사용자 권한을 확인하세요.")


_DATA_ROOT = _select_data_root()
seed_shared_database(_PACKAGE_ROOT / "data" / "app_shared.sqlite3", _DATA_ROOT)

APPLICATION_PATHS = ApplicationPaths.from_roots(
    root=_PACKAGE_ROOT,
    app_dir=_APP_DIR,
    data_root=_DATA_ROOT,
    bundled_config_dir=_BUNDLED_CONFIG_DIR,
    asset_dir=_ASSET_DIR,
    app_icon_path=_APP_ICON_PATH,
)
os.environ[SHARED_DATABASE_ENV] = str(APPLICATION_PATHS.shared_database_path)
os.environ[WORKSHOP_WEIGHT_TEMPLATE_ENV] = str(
    APPLICATION_PATHS.workshop_weight_template_file
)


def _initialize_accounts():
    return ACCOUNT_MANAGER.initialize()


from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
)
from PySide6.QtCore import QTimer, Qt, Signal, QPoint
from PySide6.QtGui import QIcon

from src.features.scanning.file_lifecycle import (
    iter_image_files as _iter_image_files,
)
from src.observability import OperationContext, log_event
from src.utils.logger import (
    disable_session_log,
    logger,
    set_log_dir,
)
from src.ui.qt_log_sink import QtLogSink


from src.features.accounts.manager import AccountManager, populate_account_combo, show_account_manager_dialog
from src.features.settings.page import refresh_account_scoped_settings
from src.integrations.global_hotkeys import GlobalHotkeyManager
from src.services.global_theme_settings_service import GlobalThemeSettingsService
from src.services.mod_plugin_loading_service import ModPluginLoadingService
from src.ui.main_window_mixins import FeatureMainWindowMixin
from src.ui.equipment_presentation import EquipmentPresentation
from src.features.blueprints.page import BlueprintPage
from src.features.toolbox.page import ToolboxDependencies, ToolboxPage
from src.services.cultivation_planner_service import CultivationPlannerService
from src.features.static_catalog.controller import StaticCatalogController
from src.features.static_catalog.dependencies import (
    build_static_catalog_domain_pages,
    build_static_catalog_providers,
)
from src.features.static_catalog.page import StaticCatalogPage
from src.services.static_catalog_service import StaticCatalogService
from src.services.warehouse_inventory_service import WarehouseInventoryService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.services.rewind_shape_recommendation_service import RewindShapeRecommendationService
from src.features.battle_report.dependencies import build_battle_report_controller
from src.features.identification.controller import IdentificationController
from src.features.onboarding.guide import OnboardingGuide
from src.features.official_role.page import refresh_official_role_page
from src.features.scanning.controller import ScanningController

ACCOUNT_MANAGER = AccountManager(
    APPLICATION_PATHS.data_root,
    APPLICATION_PATHS.bundled_config_dir,
    _iter_image_files,
    CORE_CONFIG_FILES,
    ACCOUNT_USER_FILES,
)

_INITIAL_ACCOUNT_STATE = _initialize_accounts()
APP_CONTEXT = AppContext(
    APPLICATION_PATHS,
    _INITIAL_ACCOUNT_STATE,
)
GLOBAL_THEME_SETTINGS = GlobalThemeSettingsService(
    APPLICATION_PATHS.global_ui_preferences_file
)
from src.features.inventory.warehouse import configure_warehouse_view_template_roots

configure_warehouse_view_template_roots(
    APP_CONTEXT.paths.template_dir,
    APP_CONTEXT.paths.bundled_config_dir / "templates",
    asset_root=APP_CONTEXT.paths.asset_dir,
)


def _is_admin():
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _restart_as_admin():
    if sys.platform != "win32":
        return False
    try:
        args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
        params = " ".join(f'"{a}"' for a in args)
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        return result > 32
    except Exception:
        return False


def _ensure_admin():
    if _is_admin():
        return
    if _restart_as_admin():
        sys.exit(0)
    raise RuntimeError("관리자 권한으로 실행해야 합니다. 프로그램을 마우스 오른쪽 버튼으로 클릭해 “관리자 권한으로 실행”을 선택하세요.")


try:
    from src.i18n_display import install as _install_i18n_display

    _install_i18n_display()
except Exception as _i18n_exc:  # pragma: no cover - display layer is optional
    print(f"[i18n_display] install failed: {_i18n_exc}")

# ── Main Window
from src.ui.main_window_data_mixin import MainWindowDataMixin

from src.ui.main_window_navigation_mixin import MainWindowNavigationMixin
from src.ui.main_window_theme_mixin import MainWindowThemeMixin

class MainWindow(MainWindowThemeMixin, MainWindowNavigationMixin, MainWindowDataMixin, FeatureMainWindowMixin, QMainWindow):
    log_signal = Signal(str)
    inventory_sync_state_signal = Signal(object)
    W, H = 1260, 860

    def __init__(self):
        super().__init__()
        self.app_context = APP_CONTEXT
        self.setWindowTitle("NTE Drive Calc")
        screen_geo = QApplication.primaryScreen().availableGeometry()
        initial_w = min(self.W, max(640, screen_geo.width() - 80))
        initial_h = min(self.H, max(480, screen_geo.height() - 80))
        min_w = min(1000, max(640, screen_geo.width() - 120))
        min_h = min(700, max(480, screen_geo.height() - 120))
        self.resize(initial_w, initial_h)
        self.setMinimumSize(min_w, min_h)
        if self.app_context.paths.app_icon_path.exists():
            self.setWindowIcon(QIcon(str(self.app_context.paths.app_icon_path)))
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self._drag_pos: Optional[QPoint] = None
        self._drag_edges: tuple[bool, bool, bool, bool] = (False,) * 4
        self._resize_margin = 8
        self.move(
            screen_geo.x() + (screen_geo.width() - initial_w) // 2,
            screen_geo.y() + (screen_geo.height() - initial_h) // 2,
        )
        self.roles_db: dict = {}
        self.sets_db: dict = {}
        self.all_set_names: list[str] = []
        self.tape_main_stats: list[str] = []
        self.stats_config: dict = {}
        self.equipped_state: dict = {}
        self._shape_areas: dict = {}
        self.scoring_engine = None
        self._inventory_sync_service = None
        self._application_log_context = OperationContext.create(
            "application",
        )
        self._account_switch_operation = None
        self._account_context_unsubscribe = self.app_context.subscribe_account_changed(
            self._on_app_context_account_changed
        )
        self._inventory_sync_lifecycle = CallbackAccountLifecycle(
            is_running=lambda: bool(self._inventory_sync_service and self._inventory_sync_service.is_running),
            stop=self._stop_inventory_sync,
            rebuild=lambda _account: None,
            start=self._start_inventory_sync,
        )
        self._unregister_inventory_sync_lifecycle = self.app_context.register_account_lifecycle(
            self._inventory_sync_lifecycle,
            nte_core=True,
        )
        self._log_enabled = False
        set_log_dir(self.app_context.account.log_dir, reopen_session=False)
        log_event(
            "INFO",
            "application.started",
            "앱 메인 창 초기화 시작",
            self._application_log_context,
            app_version=APP_VERSION,
            administrator_required=True,
        )

        # Hotkey config
        self._hk_capture = "F9"
        self._hk_finish = "F10"
        self._hk_stop = "F12"
        self._hk_battle_rerecord = "F11"
        self._account_settings = self.app_context.account_settings
        legacy_theme = self._account_settings.legacy_theme_preference()
        self._account_settings.migrate_legacy_settings()
        self._account_settings.remove_legacy_theme_preference()
        self._global_theme_settings = GLOBAL_THEME_SETTINGS
        self._mod_plugin_loading_service = ModPluginLoadingService(
            application_root=self.app_context.paths.root
        )
        self._theme_preference = self._load_theme_preference(legacy_theme)
        self._load_hotkey_config()
        self.global_hotkey_manager = GlobalHotkeyManager(
            capture_hotkey=self._hk_capture,
            finish_hotkey=self._hk_finish,
            stop_hotkey=self._hk_stop,
            battle_rerecord_hotkey=self._hk_battle_rerecord,
        )
        self.equipment_presentation = EquipmentPresentation(
            app_context=self.app_context,
            dialog_parent=self,
        )
        self.scanning_controller = ScanningController(
            app_context=self.app_context,
            dialog_parent=self,
            minimize_window=self.showMinimized,
            restore_window=self.showNormal,
            activate_window=self.activateWindow,
            update_inventory_status=self._update_inventory_status,
            refresh_home=self._refresh_home,
            preferences_provider=lambda: self._ui_preferences,
            save_preferences=self._save_ui_preferences,
            refresh_roles=lambda: refresh_official_role_page(self),
            refresh_equipment=self.refresh_saved_equipment_after_mutation,
            card_factory=self._card,
            equipment_presentation=self.equipment_presentation,
            hotkey_manager=self.global_hotkey_manager,
        )
        self.identification_controller = IdentificationController(
            app_context=self.app_context,
            dialog_parent=self,
            card_factory=self._card,
            equipment_presentation=self.equipment_presentation,
            hotkey_manager=self.global_hotkey_manager,
            minimize_window=self.showMinimized,
            restore_window=self.showNormal,
            activate_window=self.activateWindow,
        )
        self.battle_report_controller = build_battle_report_controller(
            app_context=self.app_context,
            dialog_parent=self,
            inventory_sync_is_running=lambda: bool(
                self._inventory_sync_service
                and self._inventory_sync_service.is_running
            ),
            stop_inventory_sync=self._stop_inventory_sync,
            start_inventory_sync=self._start_inventory_sync,
            hotkey_manager=self.global_hotkey_manager,
        )
        self.blueprint_page = BlueprintPage(
            app_context=self.app_context,
            navigate=self._go,
        )
        self.toolbox_page = ToolboxPage(
            dependencies=ToolboxDependencies(
                rewind_service_factory=lambda: RewindShapeRecommendationService(
                    user_database_path=self.app_context.account.user_database_path,
                    static_database_path=self.app_context.paths.static_database_path,
                ),
                cultivation_service_factory=lambda: CultivationPlannerService(
                    user_database_path=self.app_context.account.user_database_path,
                    static_database_path=self.app_context.paths.static_database_path,
                ),
                navigate_static_catalog=lambda: self._go("static_catalog"),
            ),
            dialog_parent=self,
        )
        static_catalog_controller = StaticCatalogController(
            StaticCatalogService(
                static_database_path=self.app_context.paths.static_database_path,
                providers=build_static_catalog_providers(
                    self.app_context.paths.static_database_path
                ),
            )
        )
        static_catalog_domains = ()
        try:
            static_catalog_domains = build_static_catalog_domain_pages(
                self.app_context.paths.static_database_path,
                self.app_context.paths.asset_dir / "game_ui",
                equipment_presentation=self.equipment_presentation,
                equipment_inventory_loader=self._load_static_catalog_inventory,
                open_catalog_link=lambda link: (
                    self.static_catalog_page.open_catalog_link(link)
                ),
            )
            self.static_catalog_page = StaticCatalogPage(
                controller=static_catalog_controller,
                dialog_parent=self,
                game_ui_asset_root=self.app_context.paths.asset_dir / "game_ui",
                domain_pages=static_catalog_domains,
            )
        except Exception:
            for spec in reversed(static_catalog_domains):
                try:
                    spec.close()
                except Exception:
                    pass
            try:
                static_catalog_controller.close()
            except Exception:
                pass
            raise
        self.onboarding_guide = OnboardingGuide(
            app_context=self.app_context,
            parent=self,
        )
        self._update_config = self._load_update_config()
        self._ui_preferences = self._load_ui_preferences()
        self._apply_theme_preference()
        self._update_check_manual = True

        self.log_signal.connect(self._on_log)
        self.inventory_sync_state_signal.connect(self._on_inventory_sync_state)
        self._log_sink = QtLogSink(self.log_signal)
        try:
            from loguru import logger as lu

            self._qt_log_sink_id = lu.add(
                self._log_sink,
                format="{time:HH:mm:ss} | {level: <8} | {message}",
                level="INFO",
                colorize=False,
            )
        except Exception as exc:
            self._qt_log_sink_id = None
            logger.debug(f"화면 로그 출력 등록 실패, 파일 로그에만 기록합니다: {exc}")
        self._build_ui()
        if self._ui_preferences["log_enabled"]:
            self._toggle_log(True)
        self._load_data()
        self._workshop_weight_refresh_thread = None
        QTimer.singleShot(1500, lambda: setattr(
            self, "_workshop_weight_refresh_thread", start_workshop_weight_template_refresh(
                self.app_context.paths.workshop_weight_template_file, self.app_context.paths.static_database_path)))
        self._refresh_home()
        self._maybe_auto_start_inventory_sync()
        self._on_log("시스템 준비 완료")
        self.onboarding_guide.maybe_show()
        self._maybe_check_updates_on_startup()

    def _load_static_catalog_inventory(self):
        """Freeze the current account projection for the equipment archive."""

        account = self.app_context.account
        static_path = self.app_context.paths.static_database_path
        service = WarehouseInventoryService(
            account.user_database_path,
            static_dao_factory=lambda: StaticGameDataDao(static_path),
        )
        return (
            account.active_account_id,
            self.app_context.generation,
            service.load_current_snapshot(),
        )

    # ── Frameless
    def _on_edge(self, pos):
        w, h = self.width(), self.height()
        m = self._resize_margin
        return (pos.x() < m, pos.y() < m, pos.x() > w - m, pos.y() > h - m)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint()
            self._drag_edges = self._on_edge(e.position().toPoint())
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        drag_edges = getattr(self, "_drag_edges", (False,) * 4)
        left_button_held = bool(e.buttons() & Qt.LeftButton)
        if not left_button_held:
            self._drag_pos = None
            self._drag_edges = (False,) * 4
            drag_edges = self._drag_edges
        if self._drag_pos is not None and left_button_held and any(drag_edges):
            d = e.globalPosition().toPoint() - self._drag_pos
            g = self.geometry()
            L, T, R, B = drag_edges
            if L:
                g.setLeft(g.left() + d.x())
            if T:
                g.setTop(g.top() + d.y())
            if R:
                g.setRight(g.right() + d.x())
            if B:
                g.setBottom(g.bottom() + d.y())
            self.setGeometry(
                g.normalized()
                if g.width() >= self.minimumWidth() and g.height() >= self.minimumHeight()
                else self.geometry()
            )
            self._drag_pos = e.globalPosition().toPoint()
        elif not any(drag_edges):
            pos = e.position().toPoint()
            E = self._on_edge(pos)
            if E[0] and E[1]:
                self.setCursor(Qt.SizeFDiagCursor)
            elif E[2] and E[3]:
                self.setCursor(Qt.SizeFDiagCursor)
            elif E[0] and E[3]:
                self.setCursor(Qt.SizeBDiagCursor)
            elif E[1] and E[2]:
                self.setCursor(Qt.SizeBDiagCursor)
            elif E[0] or E[2]:
                self.setCursor(Qt.SizeHorCursor)
            elif E[1] or E[3]:
                self.setCursor(Qt.SizeVerCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = None
            self._drag_edges = (False,) * 4
        super().mouseReleaseEvent(e)

    def closeEvent(self, e):
        if getattr(self, "_config_dirty", False) and not self._confirm_leave_config_page():
            e.ignore()
            return
        if getattr(self, "_my_role_dirty", False) and not self._confirm_leave_my_role_page():
            e.ignore()
            return
        if hasattr(self.scanning_controller, "role_selector"):
            try:
                self.scanning_controller.role_selector.save_temporary_priority_config()
            except Exception as exc:
                logger.warning(f"임시 우선순위 저장 실패: {exc}")
        try:
            self.battle_report_controller.close()
        except Exception as exc:
            logger.warning(f"전투 리포트 수집 중지 실패: {exc}")
        try:
            self.scanning_controller.close()
        except Exception as exc:
            logger.warning(f"비전 스캔 중지 실패: {exc}")
        try:
            self._stop_inventory_sync()
        except Exception as exc:
            logger.warning(f"가방 동기화 중지 실패: {exc}")
        try:
            self._mod_plugin_loading_service.close()
        except Exception as exc:
            logger.warning(f"Mod Loader 중지 실패: {exc}")
        try:
            self.static_catalog_page.close()
        except Exception as exc:
            logger.warning(f"게임 자료실 닫기 실패: {exc}")
        self.global_hotkey_manager.close()
        self._unregister_inventory_sync_lifecycle()
        self._account_context_unsubscribe()
        log_event(
            "INFO",
            "application.stopping",
            "앱이 백그라운드 서비스를 중지하고 종료하는 중",
            self._application_log_context,
        )
        if self._log_enabled:
            disable_session_log(
                reason="application_exit",
                context=self._current_log_context(),
            )
            self._log_enabled = False
        if self._qt_log_sink_id is not None:
            try:
                from loguru import logger as lu

                lu.remove(self._qt_log_sink_id)
            except Exception:
                pass
            self._qt_log_sink_id = None
        super().closeEvent(e)

    def _tb_press(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint()
            self._drag_edges = (False,) * 4

    def _tb_move(self, e):
        if self._drag_pos is not None and bool(e.buttons() & Qt.LeftButton):
            self.move(self.pos() + e.globalPosition().toPoint() - self._drag_pos)
            self._drag_pos = e.globalPosition().toPoint()

    def _tb_release(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = None
            self._drag_edges = (False,) * 4

    def _tb_dbl(self, e):
        if e.button() == Qt.LeftButton:
            self._toggle_max()

    def _toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    # ── Build

    def _refresh_account_combo(self):
        if not hasattr(self, "account_combo"):
            return
        self.account_combo.blockSignals(True)
        populate_account_combo(
            self.account_combo,
            ACCOUNT_MANAGER.read_index(),
            self.app_context.account.active_account_id,
        )
        self.account_combo.blockSignals(False)

    def _on_account_combo_changed(self, index):
        if index < 0:
            return
        account_id = self.account_combo.itemData(index)
        if account_id and account_id != self.app_context.account.active_account_id:
            if not self._switch_account(account_id):
                self._refresh_account_combo()

    def _switch_account(self, account_id):
        operation = OperationContext.create(
            "account",
            account_id=self.app_context.account.active_account_id,
            context_generation=self.app_context.generation,
        )
        self._account_switch_operation = operation
        log_event(
            "INFO",
            "account.switch_started",
            "계정 전환 시작",
            operation,
            target_account_id=account_id,
        )
        if getattr(self, "_my_role_dirty", False) and not self._confirm_leave_my_role_page():
            log_event(
                "INFO",
                "account.switch_cancelled",
                "저장되지 않은 캐릭터 수정 때문에 계정 전환이 취소되었습니다",
                operation,
                reason="role_dirty",
            )
            return False
        if getattr(self, "_config_dirty", False) and not self._confirm_leave_config_page():
            log_event(
                "INFO",
                "account.switch_cancelled",
                "저장되지 않은 기본 가중치 수정 때문에 계정 전환이 취소되었습니다",
                operation,
                reason="basic_weight_dirty",
            )
            return False
        if self._equipment_assembly_is_running():
            QMessageBox.information(
                self,
                "장착 작업 실행 중",
                "현재 장착 작업이 아직 이 계정의 데이터와 게임 상태를 사용 중입니다. 작업이 끝날 때까지 기다리거나, 자동 장착에서 F12로 중지한 뒤 계정을 전환하세요.",
            )
            log_event(
                "WARNING",
                "account.switch_blocked",
                "장착 작업 실행 중 계정 전환 차단",
                operation,
                reason="equipment_assembly_running",
            )
            return False
        if (
            self.scanning_controller.is_running()
            or self.identification_controller.is_running()
        ):
            QMessageBox.information(
                self,
                "비전 작업 실행 중",
                "현재 스캔 또는 감정 작업이 아직 이 계정의 스크린샷과 데이터베이스를 사용 중입니다. 작업이 끝날 때까지 기다리거나 스캔을 취소한 뒤 계정을 전환하세요.",
            )
            log_event(
                "WARNING",
                "account.switch_blocked",
                "비전 작업 실행 중 계정 전환 차단",
                operation,
                reason="vision_worker_running",
            )
            return False
        if self.battle_report_controller.is_running():
            QMessageBox.information(
                self,
                "전투 리포트 수집 중",
                "현재 전투 리포트가 아직 이 계정과 nte-core 세션을 사용 중입니다. 먼저 전투 리포트 수집을 끝낸 뒤 계정을 전환하세요.",
            )
            log_event(
                "WARNING",
                "account.switch_blocked",
                "전투 리포트 수집 중 계정 전환 차단",
                operation,
                reason="battle_report_running",
            )
            return False
        data = ACCOUNT_MANAGER.read_index()
        if not any(a.get("id") == account_id for a in data.get("accounts", [])):
            log_event(
                "ERROR",
                "account.switch_failed",
                "대상 계정이 없습니다",
                operation,
                target_account_id=account_id,
                reason="account_not_found",
            )
            return False
        ACCOUNT_MANAGER.set_active_account_id(account_id)
        target_account = ACCOUNT_MANAGER.activate(account_id)
        self.app_context.switch_account(target_account)
        if not (self._inventory_sync_service and self._inventory_sync_service.is_running):
            self._maybe_auto_start_inventory_sync()
        return True

    def _on_app_context_account_changed(self, event: AccountChangedEvent):
        operation = self._account_switch_operation or OperationContext.create(
            "account",
            account_id=event.previous.active_account_id,
            context_generation=max(0, event.generation - 1),
        )
        if self._log_enabled:
            disable_session_log(
                reason="account_switch",
                context={
                    "account_id": event.previous.active_account_id,
                    "context_generation": max(0, event.generation - 1),
                },
            )
        self._log_enabled = False
        set_log_dir(event.current.log_dir, reopen_session=False)
        self._account_settings = self.app_context.account_settings
        self._account_settings.migrate_legacy_settings()
        self._account_settings.remove_legacy_theme_preference()
        self._load_hotkey_config()
        self.global_hotkey_manager.update_configuration(
            capture_hotkey=self._hk_capture,
            finish_hotkey=self._hk_finish,
            stop_hotkey=self._hk_stop,
            battle_rerecord_hotkey=self._hk_battle_rerecord,
        )
        self._update_config = self._load_update_config()
        self._ui_preferences = self._load_ui_preferences()
        self._toggle_log(
            bool(self._ui_preferences["log_enabled"]),
            reason="account_switch",
        )
        refresh_account_scoped_settings(self)
        self.identification_controller.reset_account_state()
        self.battle_report_controller.reset_account_state()
        self.blueprint_page.reset_account_state()
        self.scanning_controller.reset_account_state()
        self.reset_equipment_account_state()
        self._load_data()
        if hasattr(self, "weighted_role_selector"):
            self._refresh_weighted_allocation()
        active_page = self._nav_key_for_index(self.stack.currentIndex())
        if active_page not in {"home", "execute"}:
            self.refresh_current_account_page()
        self._refresh_account_combo()
        if hasattr(self, "_ss_info"):
            self._refresh_ss()
        self._refresh_home()
        log_event(
            "INFO",
            "account.switch_succeeded",
            "계정 전환 완료",
            operation,
            target_account_id=event.current.active_account_id,
            new_context_generation=event.generation,
        )
        self._account_switch_operation = None

    def _manage_accounts(self):
        show_account_manager_dialog(
            self,
            self._current_style_sheet(),
            ACCOUNT_MANAGER,
            self.app_context.account.active_account_id,
            self._switch_account,
            self._refresh_account_combo,
        )

# ── Entry
def _global_exception_handler(exc_type, exc_value, exc_tb):
    """全局异常处理，防止未捕获异常导致闪退"""
    import traceback as tb

    error_msg = "".join(tb.format_exception(exc_type, exc_value, exc_tb))
    logger.error(f"처리되지 않은 예외:\n{error_msg}")
    try:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(None, "프로그램 예외", f"처리되지 않은 예외가 발생했습니다:\n\n{error_msg[:1000]}")
    except Exception as exc:
        logger.error(f"전역 예외 팝업 표시 실패: {exc}")


def run_gui():
    import faulthandler

    _ensure_admin()
    APP_CONTEXT.account.log_dir.mkdir(parents=True, exist_ok=True)
    _fault_log = open(
        str(APP_CONTEXT.account.log_dir / "crash_dump.log"),
        "w",
        encoding="utf-8",
    )
    faulthandler.enable(file=_fault_log)

    sys.excepthook = _global_exception_handler
    threading.excepthook = lambda args: logger.error(
        f"스레드 예외 [{args.thread}]: {args.exc_type.__name__}: {args.exc_value}"
    )
    if hasattr(Qt, "AA_DontUseNativeDialogs"):
        QApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    account_settings = APP_CONTEXT.account_settings
    legacy_theme = account_settings.legacy_theme_preference()
    account_settings.migrate_legacy_settings()
    account_settings.remove_legacy_theme_preference()
    apply_app_theme(
        app,
        GLOBAL_THEME_SETTINGS.load(legacy_theme=legacy_theme),
    )
    install_dialog_defaults(app)
    if APP_CONTEXT.paths.app_icon_path.exists():
        app.setWindowIcon(QIcon(str(APP_CONTEXT.paths.app_icon_path)))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_gui()
