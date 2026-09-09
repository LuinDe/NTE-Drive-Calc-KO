# 管理主窗口日志会话、数据加载、首页、设置页和截图清理状态。
"""MainWindow data, logging and account-scoped page helpers."""
from __future__ import annotations
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QFrame, QLabel, QMessageBox, QVBoxLayout
from src.app.constants import APP_VERSION, NETDISK_DOWNLOAD_LINKS
from src.app.theme import theme_color
from src.domain.stat_catalog import StatCatalog
from src.features.home.page import build_home_page, refresh_home_page
from src.features.scanning.file_lifecycle import (
    build_screenshot_cleanup_plan,
    execute_screenshot_cleanup,
    iter_image_files as _iter_image_files,
    managed_screenshot_usage,
)
from src.features.settings.page import build_settings_page
from src.optimizer.scoring import ScoringEngine
from src.services.dashboard_service import DashboardService
from src.services.legacy_allocation_static_catalog import build_legacy_allocation_static_catalog
from src.services.role_fork_template_service import (
    fork_templates_as_weapon_models,
    load_official_role_fork_templates,
)
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import (
    disable_session_log,
    enable_session_log,
    logger,
    session_log_path,
)

class MainWindowDataMixin:
    def _on_log(self, msg):
        if not self._log_enabled:
            return
        c = theme_color("#8b949e")
        if any(k in msg for k in ("ERROR", "error", "실패", "충돌")):
            c = "#f85149"
        elif any(k in msg for k in ("WARNING", "warning", "경고")):
            c = "#d2991d"
        elif any(k in msg for k in ("SUCCESS", "완료", "끝")):
            c = "#3fb950"
        self.log_view.moveCursor(QTextCursor.End)
        self.log_view.setTextColor(QColor(c))
        self.log_view.insertPlainText(msg + "\n")
        self.log_view.moveCursor(QTextCursor.End)

    def _clear_log(self):
        self.log_view.clear()

    def _current_log_context(self):
        return {
            "account_id": self.app_context.account.active_account_id,
            "context_generation": self.app_context.generation,
        }

    def _refresh_log_session_status(self):
        label = getattr(self, "_log_session_status_label", None)
        if label is None:
            return
        path = session_log_path()
        if self._log_enabled and path is not None:
            label.setText(f"상세 로그: {path.name}")
            label.setToolTip(str(path))
            return
        label.setText("상세 로그: 사용 안 함")
        label.setToolTip("")

    def _toggle_log(self, enabled, *, reason="settings"):
        toggle = getattr(self, "_log_toggle", None)
        if toggle is not None and toggle.isChecked() != bool(enabled):
            toggle.blockSignals(True)
            toggle.setChecked(bool(enabled))
            toggle.blockSignals(False)
        if enabled:
            try:
                log_path = enable_session_log(context=self._current_log_context())
            except Exception as exc:
                logger.error(f"실행 로그 파일 생성 실패: {exc}")
                if toggle is not None:
                    toggle.blockSignals(True)
                    toggle.setChecked(False)
                    toggle.blockSignals(False)
                self._ui_preferences["log_enabled"] = False
                self._save_ui_preferences()
                QMessageBox.warning(self, "실행 로그", "실행 로그 파일을 만들 수 없습니다. 로그 디렉터리가 쓰기 가능한지 확인하세요")
                self._refresh_log_session_status()
                return
            self._log_enabled = True
            self._ui_preferences["log_enabled"] = True
            self._save_ui_preferences()
            self.log_frame.setVisible(True)
            logger.info(
                "logging.ui_enabled | 설정에서 상세 실행 로그를 켰습니다"
                f" | file={log_path.name} reason={reason}"
            )
            self._refresh_log_session_status()
            return
        disable_session_log(
            reason=reason,
            context=self._current_log_context(),
        )
        self._log_enabled = False
        self._ui_preferences["log_enabled"] = False
        self._save_ui_preferences()
        self.log_frame.setVisible(False)
        self.log_view.clear()
        self.log_view.insertPlainText("(로그 꺼짐)\n")
        self._refresh_log_session_status()

    # ── Data
    def _load_data(self, reload_priority=True):
        try:
            config_dir = self.app_context.paths.config_dir
            user_database_path = self.app_context.account.user_database_path
            catalog = StatCatalog.from_config_dir(config_dir)
            self.stats_config = {
                "gold_base_values": catalog.gold_base_values,
                "tape_main_stats_pool": catalog.tape_main_stats,
                "tape_main_stat_values": catalog.tape_main_values,
                "tape_stat_values": catalog.tape_stat_values,
                "main_only_keywords": catalog.main_only_keywords,
                "stat_alias_mapping": catalog.stat_alias_mapping,
                "benefit_one": catalog.benefit_one,
                "benefit_alias_mapping": catalog.benefit_alias_mapping,
                "weight_pool": catalog.weight_pool,
            }
            self.tape_main_stats = catalog.tape_main_stats
            self.drive_sub_stats = list(catalog.gold_base_values.keys())
            self.weapons_db = fork_templates_as_weapon_models(load_official_role_fork_templates())
            static_catalog = build_legacy_allocation_static_catalog(
                config_dir=config_dir,
                user_database_path=user_database_path,
            )
            self.roles_db = static_catalog.roles_db
            self.sets_db = static_catalog.sets_db
            self.all_set_names = list(self.sets_db)
            self._shape_areas = {shape_id: int(shape.area) for shape_id, shape in static_catalog.shapes_db.items()}
            self.equipped_state = {}
            self.scoring_engine = ScoringEngine(
                str(config_dir),
                user_database_path=user_database_path,
                roles_db=self.roles_db,
            )
            logger.info(f"SQLite에서 캐릭터 {len(self.roles_db)}명, 세트 {len(self.sets_db)}개를 불러왔습니다")
            self._update_inventory_status()
            self.scanning_controller.role_selector.load_roles(
                self.roles_db,
                self.all_set_names,
                self.tape_main_stats,
                self.drive_sub_stats,
                weapons_db=self.weapons_db,
            )
            if reload_priority:
                self.scanning_controller.role_selector.load_startup_priority_config()
            self.equipment_presentation.update_catalog(
                roles_db=self.roles_db,
                scoring_engine=self.scoring_engine,
                shape_areas=self._shape_areas,
            )
            self.identification_controller.update_catalog(
                shape_areas=self._shape_areas,
                set_names=self.all_set_names,
                scoring_engine=self.scoring_engine,
            )
        except Exception as e:
            logger.error(f"불러오기 실패: {e}")

    def _refresh_execute(self):
        """Reload the legacy calculation role catalog when entering step two."""

        self._load_data(reload_priority=False)


    def _update_inventory_status(self):
        try:
            user_database_path = self.app_context.account.user_database_path
            if user_database_path.is_file():
                with UserDataDao(user_database_path) as dao:
                    summary = dao.current_inventory_summary()
                if summary is not None:
                    count = int(summary["stored_item_count"])
                    self.status_lbl.setText(f"안정 가방 {count}개")
                    self.status_lbl.setStyleSheet("color:#3fb950;font-size:12px")
                    return
        except Exception as exc:
            logger.debug(f"SQLite 가방 상태 읽기 실패: {exc}")
        self.status_lbl.setText("인벤토리가 비어 있음")
        self.status_lbl.setStyleSheet("color:#d2991d;font-size:12px")

    def _card(self, title):
        c = QFrame()
        c.setObjectName("card")
        l = QVBoxLayout(c)
        l.setContentsMargins(20, 16, 20, 16)
        l.setSpacing(8)
        lb = QLabel(title)
        lb.setObjectName("cardTitle")
        l.addWidget(lb)
        return c

    # ── Page: Home / 2.0 Dashboard
    def _page_home(self):
        return build_home_page(self)

    def _refresh_home(self):
        if not hasattr(self, "home_account_label"):
            return
        try:
            dashboard = DashboardService(
                self.app_context.account.user_database_path
            ).load()
            refresh_home_page(self, dashboard)
        except Exception as exc:
            self.home_account_label.setText(f"작업 공간 데이터를 일시적으로 사용할 수 없습니다: {exc}")
            logger.warning(f"2.0 작업 공간 새로 고침 실패: {exc}")

    def _page_settings(self):
        return build_settings_page(
            self,
            APP_VERSION,
            self.app_context,
            _iter_image_files,
            NETDISK_DOWNLOAD_LINKS,
        )

    def _refresh_ss(self):
        account = self.app_context.account
        usage = managed_screenshot_usage(
            account.screenshot_dir,
            account.account_data_root,
        )
        self._ss_info.setText(f"현재 스크린샷: {usage.count}개 · {usage.size_mb:.1f} MB")

    def _clear_ss(self):
        account = self.app_context.account
        plan = build_screenshot_cleanup_plan(
            account.screenshot_dir,
            account.account_data_root,
        )
        if plan.total_count == 0:
            QMessageBox.information(self, "정리", "정리할 파일이 없습니다.")
            return
        if (
            QMessageBox.question(
                self, "정리 확인", plan.confirmation_text(), QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            == QMessageBox.Yes
        ):
            result = execute_screenshot_cleanup(plan)
            self._refresh_ss()
            logger.success(f"스크린샷 {result.deleted}개를 정리했습니다")
            if result.failed_files:
                QMessageBox.warning(self, "정리 완료", f"파일 {len(result.failed_files)}개 삭제에 실패했습니다. 사용 중일 수 있습니다.")
            if plan.baseline_missing:
                QMessageBox.warning(
                    self, "정리 완료", "주의: 비교용 스크린샷을 잃으면 전체 스캔을 다시 하거나, 완전 자동 증분 스캔을 사용하지 마세요."
                )
