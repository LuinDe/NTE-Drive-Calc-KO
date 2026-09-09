# 编排扫描、解析、文件生命周期与进度回调。
"""Scanning workflow implementation used by ScanningController."""

from __future__ import annotations
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from src.app.constants import DRONE_HELP, OFFLINE_HELP, SCAN_HELP
from src.app.dialogs import show_help
from src.app.theme import current_style_sheet
from src.app.workers import FullVisualScanParseWorkerThread, ScanWorkerThread
from src.features.allocation.execute_page import build_execute_page
from src.features.allocation.preference_modes import role_preference_mode_error
from src.features.allocation.role_selector import RoleSelector
from src.features.scanning.dependencies import (
    current_scanning_dependencies as _current_scanning_dependencies,
    scanning_dependencies_are_current as _scanning_dependencies_are_current,
    task_scanning_dependencies as _task_scanning_dependencies,
)
from src.features.scanning.manual_recovery import complete_pending_manual_items
from src.features.scanning.operation_logging import (
    begin_scan_operation as _begin_scan_operation,
    scan_event as _scan_event,
)
from src.features.scanning.post_action_dialog import load_scan_post_action_config, show_scan_post_action_dialog
from src.features.scanning.scan_contracts import (
    offline_scope_replaces_inventory,
    vision_cancel_message,
)
from src.features.scanning.post_action_summary import append_state_mismatch_summary
from src.domain.post_actions import post_actions_enabled, validate_post_action_config
from src.features.scanning.vision_worker import VisionWorkerThread
from src.services.full_visual_snapshot_commit import IncompleteVisionScanError, commit_completed_vision_inventory
from src.utils.logger import logger

def _page_execute(self):
    return build_execute_page(
        self,
        lambda: RoleSelector(
            priority_config_path_provider=lambda: (
                _current_scanning_dependencies(self).user_config_dir / "priority_config.json"
            ),
            style_sheet=current_style_sheet(),
            help_callback=show_help,
        ),
        SCAN_HELP,
        DRONE_HELP,
        OFFLINE_HELP,
        show_help,
    )


def _on_scan_change(self, id):
    if hasattr(self, "offline_frame"):
        self.offline_frame.setVisible(id == 3)
    self.total_count_frame.setVisible(id == 1)
    if hasattr(self, "full_scan_driver_frame"):
        self.full_scan_driver_frame.setVisible(id == 1)
    if hasattr(self, "scan_dual_thread_frame"):
        self.scan_dual_thread_frame.setVisible(id == 1)
    self.drone_frame.setVisible(id == 2)


def _on_priority_changed(self):
    pass


def _open_scan_post_action_manager(self):
    dependencies = _current_scanning_dependencies(self)
    show_scan_post_action_dialog(
        self.dialog_parent,
        dependencies.user_config_dir,
        dependencies.config_dir,
        user_database_path=dependencies.user_database_path,
    )


def _do_exec(self):
    dependencies = _current_scanning_dependencies(self)
    sel = self.role_selector.get_selected()
    sm = str(self.scan_group.checkedId())
    parse_only = not sel and sm in ("1", "2", "3")
    if not sel and not parse_only:
        QMessageBox.warning(self.dialog_parent, "알림", "먼저 대상 캐릭터를 선택하세요!")
        return
    total_drives = None
    capture_driver = "mouse"
    if sm == "1":
        driver_button = (
            self.full_scan_driver_group.checkedButton()
            if hasattr(self, "full_scan_driver_group")
            else None
        )
        if driver_button is not None:
            capture_driver = str(driver_button.property("capture_driver") or "mouse")
        raw_count = self.total_count_edit.text().strip()
        if not raw_count:
            QMessageBox.warning(
                self.dialog_parent,
                "알림",
                "전체 스캔 전에 인벤토리 수량을 입력하세요.",
            )
            return
        total_drives = int(raw_count)
        if not 0 < total_drives <= 2000:
            QMessageBox.warning(
                self.dialog_parent,
                "알림",
                "인벤토리 수량은 1~2000 사이여야 합니다.",
            )
            return
    if parse_only:
        QMessageBox.information(
            self.dialog_parent,
            "인벤토리 데이터만 생성",
            "현재 선택한 캐릭터가 없어 이번 스캔 분석은 SQLite 가방 스냅샷에만 기록하고 장비 세팅 계산은 하지 않습니다.",
        )
    offline_scope = None
    if sm == "3":
        checked = self.offline_group.checkedButton() if hasattr(self, "offline_group") else None
        offline_scope = checked.property("offline_key") if checked else "incremental"
        if offline_scope == "all":
            ret = QMessageBox.warning(
                self.dialog_parent,
                "전체 스크린샷 분석",
                "전체 스크린샷 분석은 폴더 루트의 모든 스크린샷을 읽으므로 이전 스크린샷이 인벤토리에 중복 기록될 수 있습니다.\n\n인벤토리에 이상이 생기면 전체 스캔을 다시 하세요.\n\n계속할까요?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return
    pending_drone_mode = None
    if sm == "2":
        pending_drone_mode = "auto" if self.drone_group.checkedId() == 1 else "semi"
        if pending_drone_mode == "auto" and not (dependencies.screenshot_dir / "raw_drive_0001.png").exists():
            QMessageBox.warning(
                self.dialog_parent,
                "전체 스캔을 다시 해야 함",
                "버전 업데이트로 분석 로직이 바뀌어 전체 스캔을 다시 해야 합니다",
            )
            return
    strat = ["role_priority", "update_mode"][max(0, min(1, self.strategy_group.checkedId()))]
    cs = self.role_selector.get_custom_sets()
    cw = self.role_selector.get_custom_weapons() if hasattr(self.role_selector, "get_custom_weapons") else {}
    tmf = self.role_selector.get_tape_main_filters()
    cpm = self.role_selector.get_crit_priority_modes()
    crc = self.role_selector.get_crit_rate_caps()
    crb = self.role_selector.get_crit_rate_baselines()
    sem = self.role_selector.get_set_effect_modes()
    pg = self.role_selector.get_priority_groups() if hasattr(self.role_selector, "get_priority_groups") else None
    preference_error = role_preference_mode_error(strat, tmf, cpm, crc)
    if preference_error:
        QMessageBox.warning(
            self.dialog_parent,
            "스탯 직접 선택을 사용할 수 없음",
            preference_error,
        )
        return
    if not parse_only and not self._allocation_filter_settings_are_valid():
        return
    if not parse_only and not self._confirm_unsaved_allocation_before_recompute():
        return
    post_actions_config = None
    if sm == "1":
        post_actions_config = load_scan_post_action_config(
            dependencies.user_config_dir,
            user_database_path=dependencies.user_database_path,
        )
        post_action_error = validate_post_action_config(post_actions_config)
        if post_action_error:
            QMessageBox.warning(
                self.dialog_parent,
                "스캔 후 관리 설정이 유효하지 않음",
                post_action_error,
            )
            return
    self.btn_run.setEnabled(False)
    self.btn_run.setText("⏳ 계산 중...")
    self.result_card.setVisible(False)
    self._pending_strat = strat
    self._pending_sel = sel
    self._pending_cs = cs
    self._pending_custom_weapons = cw
    self._pending_tape_main_filters = tmf
    self._pending_crit_priority_modes = cpm
    self._pending_crit_rate_caps = crc
    self._pending_crit_rate_baselines = crb
    self._pending_set_effect_modes = sem
    self._pending_priority_groups = pg
    self._pending_filter_settings = self._allocation_filter_settings
    self._pending_archive_paths = []
    self._pending_parse_only = parse_only

    if sm == "3":
        scope = {"full": "full", "incremental": "incremental", "all": "all"}.get(offline_scope, "incremental")
        self._start_vision_processing(replace_output=offline_scope_replaces_inventory(scope), parse_scope=scope)
    elif sm == "2":
        drone_mode = pending_drone_mode or ("auto" if self.drone_group.checkedId() == 1 else "semi")
        self._start_scan(drone_mode)
    elif sm == "1":
        parse_during_scan = True
        if hasattr(self, "scan_dual_thread_check"):
            parse_during_scan = bool(self.scan_dual_thread_check.isChecked())
        amd_compatibility = False
        if hasattr(self, "scan_amd_compat_check"):
            amd_compatibility = bool(self.scan_amd_compat_check.isChecked())
        if amd_compatibility:
            parse_during_scan = False
        self._start_gamepad_scan(
            total_drives,
            post_actions_config=post_actions_config,
            selected_roles=None,
            parse_during_scan=parse_during_scan,
            amd_compatibility=amd_compatibility,
            capture_driver=capture_driver,
        )
    else:
        self._allocation_controller.start(
            strategy=strat,
            selected_roles=sel,
            custom_sets=cs,
            tape_main_filters=tmf,
            crit_priority_modes=cpm,
            set_effect_modes=sem,
            priority_groups=pg,
            crit_rate_caps=crc,
            crit_rate_baselines=crb,
            custom_weapons=cw,
            filter_settings=self._allocation_filter_settings,
            blueprint_combo_limit=self._allocation_filter_settings.blueprint_combo_limit,
        )


def _start_vision_processing(self, replace_output=False, parse_scope="all"):
    dependencies = _current_scanning_dependencies(self)
    self._scan_dependencies = dependencies
    if not getattr(self, "_scan_operation_active", False):
        _begin_scan_operation(
            self,
            dependencies,
            route="offline_parse",
            parse_scope=parse_scope,
        )
    input_dir = str(dependencies.screenshot_dir)
    self._pending_archive_paths = []
    self._pending_parse_scope = parse_scope
    skip_names = self._prepare_incremental_parse(parse_scope)
    if skip_names is None:
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  계산 시작")
        self._pending_parse_only = False
        return
    matching_files = self._matching_scope_files(parse_scope, skip_names)
    if not matching_files:
        deleted = self._delete_paths(getattr(self, "_pending_delete_after_parse", []) or [])
        self._pending_delete_after_parse = []
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  계산 시작")
        self._pending_parse_only = False
        self._update_inventory_status()
        _scan_event(
            self,
            "INFO",
            "scanning.succeeded",
            "스캔 분석 완료",
            success_count=0,
            failed_count=0,
            duplicate_count=deleted,
            snapshot_written=False,
        )
        QMessageBox.information(
            self.dialog_parent,
            "분석 완료",
            f"분석 성공 0장, 분석 실패 0장, 중복 제거 {deleted}장.",
        )
        return
    self._vision_worker = VisionWorkerThread(
        input_dir,
        self,
        replace_output=replace_output,
        parse_scope=parse_scope,
        skip_names=skip_names,
        config_dir=str(dependencies.config_dir),
    )
    self._progress_dlg = QProgressDialog(
        "스크린샷을 분석하는 중...",
        "취소",
        0,
        100,
        self.dialog_parent,
    )
    self._progress_dlg.setWindowTitle("스크린샷 분석 진행률")
    self._progress_dlg.setMinimumWidth(400)
    self._progress_dlg.setAutoClose(False)
    self._progress_dlg.setAutoReset(False)
    self._progress_dlg.canceled.connect(self._on_vision_cancel)
    self._progress_dlg.show()
    self._vision_worker.progress.connect(self._on_vision_progress)
    self._vision_worker.processing_done.connect(self._on_vision_done)
    self._vision_worker.canceled.connect(self._on_vision_canceled)
    self._vision_worker.error.connect(self._on_vision_error)
    self._vision_worker.start()


def _on_vision_progress(self, current, total, filename):
    self._progress_dlg.setMaximum(total)
    self._progress_dlg.setValue(current)
    self._progress_dlg.setLabelText(f"분석 중 ({current}/{total}): {filename}")


def _on_vision_done(self, stats):
    dependencies = _task_scanning_dependencies(self)
    stats = stats or {}
    if not _scanning_dependencies_are_current(self, dependencies):
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  계산 시작")
        self._pending_parse_only = False
        return
    self._pending_archive_paths = []
    logger.info("비전 분석 스레드 완료, 분배 계산을 시작합니다...")
    if hasattr(self, "_progress_dlg") and self._progress_dlg:
        self._progress_dlg.close()
    vision_worker = getattr(self, "_vision_worker", None)
    if vision_worker is not None and vision_worker.isRunning():
        vision_worker.wait(5000)
    post = self._postprocess_vision_files(stats)
    manual_items = []
    pending_manual_count = int(stats.get("pending_manual_count", 0) or 0)
    if pending_manual_count:
        try:
            manual_result = complete_pending_manual_items(
                self.dialog_parent,
                stats,
                dependencies.config_dir,
            )
            if manual_result is None:
                _scan_event(
                    self,
                    "WARNING",
                    "scanning.cancelled",
                    "사용자가 스캔 보완 입력을 취소했습니다",
                    pending_manual_count=pending_manual_count,
                )
                QMessageBox.information(
                    self.dialog_parent,
                    "보완 입력이 취소되었습니다",
                    "이번 전체 비전 스캔은 SQLite 가방 스냅샷에 기록되지 않았습니다.",
                )
                return
            manual_items = manual_result
        except Exception as exc:
            logger.error(f"인식 대기 장비 보완 입력 실패: {exc}")
            _scan_event(
                self,
                "ERROR",
                "scanning.failed",
                "스캔 보완 입력 실패",
                stage="manual_recovery",
                error=exc,
            )
            QMessageBox.warning(
                self.dialog_parent,
                "보완 입력 실패",
                f"이번 스캔은 SQLite 가방 스냅샷에 기록되지 않았습니다: {exc}",
            )
            return
    success_count = int(stats.get("success_count", 0) or 0)
    failed_count = int(stats.get("failed_count", 0) or 0)
    duplicate_count = int(stats.get("duplicate_count", 0) or 0) + int(post.get("probe_duplicates", 0) or 0)
    summary = f"분석 성공 {success_count}장, 분석 실패 {failed_count}장, 중복 제거 {duplicate_count}장."
    vision_snapshot_id = None
    try:
        vision_snapshot_id = commit_completed_vision_inventory(
            dependencies.user_database_path,
            stats,
            manual_items,
        )
    except IncompleteVisionScanError as exc:
        _scan_event(
            self,
            "WARNING",
            "scanning.incomplete",
            "전체 비전 스캔 분석이 불완전함",
            expected_count=exc.expected_count,
            parsed_count=exc.parsed_count,
            failed_count=exc.failed_count,
        )
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  계산 시작")
        self._pending_parse_only = False
        QMessageBox.warning(
            self.dialog_parent,
            "스캔 결과가 불완전함",
            f"{exc}\n이번 결과는 SQLite 현재 인벤토리 스냅샷으로 전환되지 않았습니다.",
        )
        return
    except Exception as exc:
        logger.error(f"비전 스캔 SQLite 스냅샷 기록 실패: {exc}")
        _scan_event(
            self,
            "ERROR",
            "scanning.failed",
            "비전 인벤토리 스냅샷 기록 실패",
            stage="snapshot_commit",
            error=exc,
        )
        QMessageBox.warning(
            self.dialog_parent,
            "인벤토리 기록 실패",
            f"이번 스캔은 SQLite 가방 스냅샷에 기록되지 않았습니다: {exc}",
        )
        return
    if isinstance(vision_snapshot_id, int) and vision_snapshot_id > 0:
        summary += f"\n비전 스캔 인벤토리 스냅샷 #{vision_snapshot_id}을(를) 기록했습니다. 패킷 캡처 스냅샷이 없을 때 계산과 자동 장착에 사용할 수 있습니다."
        refresh_home = getattr(self, "_refresh_home", None)
        if callable(refresh_home):
            refresh_home()
    if pending_manual_count:
        summary += (
            f"\n보완 입력 대기 {pending_manual_count}개, 보완 입력 완료 {len(manual_items)}개를 이번 인식 결과와 함께 SQLite 스냅샷에 기록했습니다."
        )
    _scan_event(
        self,
        "INFO",
        "scanning.succeeded",
        "스캔 분석 완료",
        parse_scope=stats.get("parse_scope"),
        success_count=success_count,
        failed_count=failed_count,
        duplicate_count=duplicate_count,
        pending_manual_count=pending_manual_count,
        recovered_manual_count=len(manual_items),
        snapshot_id=vision_snapshot_id,
        snapshot_written=bool(vision_snapshot_id),
    )
    if stats.get("post_actions_enabled"):
        summary += (
            "\n스캔 후 관리:"
            f"계산 참여 {int(stats.get('post_action_candidate_count', 0) or 0)}개,"
            f"대상 변경 {int(stats.get('post_action_target_count', 0) or 0)}개,"
            f"처리 완료 {int(stats.get('post_action_applied_count', 0) or 0)}개."
            f"\n폐기 {int(stats.get('discard_set_count', 0) or 0)}개,"
            f"폐기 취소 {int(stats.get('discard_clear_count', 0) or 0)}개;"
            f"잠금 {int(stats.get('lock_set_count', 0) or 0)}개,"
            f"잠금 취소 {int(stats.get('lock_clear_count', 0) or 0)}개."
        )
        filtered_parts = []
        if int(stats.get("post_action_quality_filtered_count", 0) or 0):
            filtered_parts.append(f"품질 범위 필터 {int(stats.get('post_action_quality_filtered_count', 0) or 0)}개")
        if int(stats.get("post_action_type_filtered_count", 0) or 0):
            filtered_parts.append(f"처리 종류 필터 {int(stats.get('post_action_type_filtered_count', 0) or 0)}개")
        if int(stats.get("post_action_type_range_filtered_count", 0) or 0):
            filtered_parts.append(f"유형 범위 필터 {int(stats.get('post_action_type_range_filtered_count', 0) or 0)}개")
        if filtered_parts:
            summary += "\n" + ",".join(filtered_parts) + "."
        summary = append_state_mismatch_summary(summary, stats)
    details = []
    if post.get("moved_failed"):
        details.append(f"실패 스크린샷 {post['moved_failed']}장을 failed 폴더로 이동했습니다.")
    if post.get("renamed"):
        details.append(f"증분 스크린샷 {post['renamed']}장의 이름을 바꿔 전체 순서에 편입했습니다.")
    if details:
        summary += "\n" + "\n".join(details)
    if getattr(self, "_pending_parse_only", False):
        self._pending_archive_paths = []
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  계산 시작")
        self._update_inventory_status()
        QMessageBox.information(
            self.dialog_parent,
            "인벤토리 데이터가 생성됨",
            summary + "\n\n이번에는 캐릭터 우선순위를 설정하지 않아 SQLite 가방 스냅샷만 생성/갱신했고 장비 세팅 계산은 하지 않았습니다.",
        )
        self._pending_parse_only = False
        return
    from PySide6.QtCore import QTimer

    QMessageBox.information(self.dialog_parent, "스크린샷 분석 완료", summary)
    QTimer.singleShot(100, self._start_allocation_worker)


def _on_vision_error(self, err):
    _scan_event(
        self,
        "ERROR",
        "scanning.failed",
        "스크린샷 분석 실패",
        stage="vision_parse",
        error=err,
    )
    self._progress_dlg.close()
    self.btn_run.setEnabled(True)
    self.btn_run.setText("⚡  계산 시작")
    self._pending_parse_only = False
    QMessageBox.critical(
        self.dialog_parent,
        "분석 실패",
        f"스크린샷 분석 오류:\n{err}",
    )


def _on_vision_cancel(self):
    vision_worker = getattr(self, "_vision_worker", None)
    if vision_worker is not None and vision_worker.isRunning():
        vision_worker.request_cancel()
        self._progress_dlg.setCancelButton(None)
        self._progress_dlg.setLabelText("분석을 취소하는 중입니다. 현재 스크린샷 처리가 끝날 때까지 기다리세요...")
        return
    self.btn_run.setEnabled(True)
    self.btn_run.setText("⚡  계산 시작")


def _on_vision_canceled(self, count):
    _scan_event(
        self,
        "WARNING",
        "scanning.cancelled",
        "사용자가 스크린샷 분석을 취소했습니다",
        parsed_count=int(count or 0),
    )
    if hasattr(self, "_progress_dlg") and self._progress_dlg:
        self._progress_dlg.close()
    self.btn_run.setEnabled(True)
    self.btn_run.setText("계산 시작")
    self._pending_parse_only = False
    QMessageBox.information(
        self.dialog_parent,
        "분석이 취소되었습니다",
        vision_cancel_message(count),
    )


def _start_scan(self, drone_mode):
    dependencies = _current_scanning_dependencies(self)
    self._scan_dependencies = dependencies
    _begin_scan_operation(self, dependencies, route=str(drone_mode))
    self._pending_scan_mode = drone_mode
    self.showMinimized()
    self._scan_worker = ScanWorkerThread(
        output_dir=dependencies.screenshot_dir,
        template_path=dependencies.template_dir / "new_tag.png",
        mode=drone_mode,
        parent=self,
    )
    self._scan_worker.scan_done.connect(self._on_scan_done)
    self._scan_worker.error.connect(self._on_scan_error)
    self._start_scan_hotkeys(drone_mode)
    self.btn_run.setText(f"⏳  스캔 중... ({self._hotkey_manager.configuration.stop} 중지)")
    self._scan_worker.start()


def _start_gamepad_scan(
    self, total_drives, post_actions_config=None, selected_roles=None, parse_during_scan=True,
    amd_compatibility=False, capture_driver="mouse",
):
    dependencies = _current_scanning_dependencies(self)
    self._scan_dependencies = dependencies
    self._replace_inventory_on_next_parse = True
    capture_driver = "gamepad" if capture_driver == "gamepad" else "mouse"
    self._pending_scan_mode = capture_driver
    self._pending_parse_scope = "full"
    self._pending_delete_after_parse = []
    self._pending_probe_duplicate_count = 0
    self._gamepad_parse_progress = (0, total_drives, "")
    self._gamepad_pipeline_finished = False
    self._gamepad_post_actions_enabled = bool(post_actions_enabled(post_actions_config))
    self._gamepad_suppress_parse_ui = False
    action_hint = ""
    if self._gamepad_post_actions_enabled:
        action_hint = (
            "\n\n스캔 후 관리가 켜져 있습니다: 스캔 분석 후 계산을 계속하고 폐기/잠금 상태를 동기화합니다."
            "\n스캔이 시작되면 정렬, 필터, 스크롤을 바꾸거나 가방을 직접 조작하지 마세요."
        )
    ret = QMessageBox.question(
        self.dialog_parent,
        "전체 스캔 준비",
        "“확인”을 클릭하면 프로그램이 최소화되고 전체 스캔을 준비합니다.\n\n"
        "게임의 드라이브 창고 페이지로 전환하세요.\n"
        + (
            "목록을 맨 위로 스크롤하세요. 프로그램이 카운트다운 후 격자 기준 무작위 오프셋으로 클릭하며 스크롤 순회 스크린샷을 찍습니다."
            if capture_driver == "mouse"
            else "첫 줄 첫 번째 드라이브를 선택한 상태인지 확인하세요. 프로그램이 카운트다운 후 가상 게임패드로 순회 스크린샷을 찍습니다."
        )
        + action_hint,
        QMessageBox.Ok | QMessageBox.Cancel,
        QMessageBox.Cancel,
    )
    if ret != QMessageBox.Ok:
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  계산 시작")
        self._replace_inventory_on_next_parse = False
        self._pending_scan_mode = None
        self._gamepad_post_actions_enabled = False
        self._gamepad_suppress_parse_ui = False
        return
    _begin_scan_operation(
        self,
        dependencies,
        route=capture_driver,
        expected_capture_count=int(total_drives or 0),
        parse_during_scan=bool(parse_during_scan),
        post_actions_enabled=bool(self._gamepad_post_actions_enabled),
    )
    self.showMinimized()
    self._gamepad_worker = FullVisualScanParseWorkerThread(
        total_drives=total_drives,
        screenshot_dir=dependencies.screenshot_dir,
        config_dir=dependencies.config_dir,
        user_database_path=dependencies.user_database_path,
        parent=self,
        post_actions_config=post_actions_config,
        selected_roles=selected_roles,
        parse_during_scan=parse_during_scan,
        amd_compatibility=amd_compatibility,
        capture_driver=capture_driver,
        result_is_current=lambda: (
            self.app_context.generation == dependencies.generation
            and self.app_context.account.active_account_id == dependencies.account_id
            and self.app_context.account.user_database_path == dependencies.user_database_path
        ),
    )
    self._gamepad_worker.scan_done.connect(self._on_gamepad_scan_done)
    self._gamepad_worker.progress.connect(self._on_gamepad_parse_progress)
    self._gamepad_worker.parse_done.connect(self._on_gamepad_parse_done)
    self._gamepad_worker.post_actions_ready.connect(self._on_gamepad_post_actions_ready)
    self._gamepad_worker.processing_done.connect(self._on_gamepad_pipeline_done)
    self._gamepad_worker.error.connect(self._on_gamepad_error)
    self._start_scan_hotkeys(capture_driver)
    label = "마우스" if capture_driver == "mouse" else "게임패드"
    self.btn_run.setText(f"⏳  {label} 스캔/분석 중... ({self._hotkey_manager.configuration.stop} 중지)")
    self._gamepad_worker.start()


def _on_gamepad_scan_done(self, captured, total):
    if getattr(self, "_gamepad_pipeline_finished", False):
        return
    if getattr(self, "_gamepad_suppress_parse_ui", False):
        return
    self.showNormal()
    self.activateWindow()
    current, progress_total, filename = getattr(self, "_gamepad_parse_progress", (0, total, ""))
    progress_total = max(int(progress_total or 0), int(total or 0), int(captured or 0), 1)
    dlg = getattr(self, "_progress_dlg", None)
    if dlg and dlg.isVisible():
        self._on_gamepad_parse_progress(current, progress_total, filename)
        return
    self._progress_dlg = QProgressDialog(
        "스캔 완료, 스크린샷을 분석하는 중...",
        "",
        0,
        progress_total,
        self.dialog_parent,
    )
    self._progress_dlg.setWindowTitle("전체 분석 진행률")
    self._progress_dlg.setMinimumWidth(420)
    self._progress_dlg.setAutoClose(False)
    self._progress_dlg.setAutoReset(False)
    self._progress_dlg.setCancelButton(None)
    self._progress_dlg.show()
    self._on_gamepad_parse_progress(current, progress_total, filename)


def _on_gamepad_parse_progress(self, current, total, filename):
    self._gamepad_parse_progress = (current, total, filename)
    if getattr(self, "_gamepad_suppress_parse_ui", False):
        return
    dlg = getattr(self, "_progress_dlg", None)
    if not dlg:
        return
    dlg.setMaximum(max(int(total or 0), 1))
    dlg.setValue(int(current or 0))
    if filename:
        dlg.setLabelText(f"스캔 완료, 분석 중 ({current}/{total}): {filename}")
    else:
        dlg.setLabelText(f"스캔 완료, 분석 진행을 기다리는 중... ({current}/{total})")


def _on_gamepad_parse_done(self):
    dlg = getattr(self, "_progress_dlg", None)
    if dlg:
        dlg.close()
        self._progress_dlg = None


def _on_gamepad_post_actions_ready(self):
    self._gamepad_suppress_parse_ui = True
    self._on_gamepad_parse_done()
    self.showMinimized()
    QApplication.processEvents()
    worker = getattr(self, "_gamepad_worker", None)
    if worker is not None and hasattr(worker, "acknowledge_post_actions_ready"):
        worker.acknowledge_post_actions_ready()


def _on_gamepad_error(self, err):
    _scan_event(
        self,
        "ERROR",
        "scanning.failed",
        "전체 비전 스캔 실패",
        stage="full_visual_pipeline",
        error=err,
    )
    self._stop_scan_hotkeys()
    self._gamepad_pipeline_finished = True
    self._gamepad_suppress_parse_ui = False
    self._gamepad_post_actions_enabled = False
    self._replace_inventory_on_next_parse = False
    self.showNormal()
    self.activateWindow()
    if hasattr(self, "_progress_dlg") and self._progress_dlg:
        self._progress_dlg.close()
    self.btn_run.setEnabled(True)
    self.btn_run.setText("⚡  계산 시작")
    self._pending_parse_only = False
    QMessageBox.critical(
        self.dialog_parent,
        "전체 비전 스캔 실패",
        f"전체 스캔 오류:\n{err}",
    )


def _on_gamepad_pipeline_done(self, stats):
    self._stop_scan_hotkeys()
    self._gamepad_pipeline_finished = True
    self._gamepad_suppress_parse_ui = False
    self._gamepad_post_actions_enabled = False
    self.showNormal()
    self.activateWindow()
    self._replace_inventory_on_next_parse = False
    self._pending_scan_mode = None
    if stats.get("discarded_stale"):
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  계산 시작")
        self._pending_parse_only = False
        return
    self._on_vision_done(stats)


def _on_scan_done(self, count):
    self._stop_scan_hotkeys()
    self.showNormal()
    self.activateWindow()
    if count > 0:
        replace_output = getattr(self, "_replace_inventory_on_next_parse", False)
        self._replace_inventory_on_next_parse = False
        scan_mode = getattr(self, "_pending_scan_mode", None)
        if replace_output or scan_mode == "gamepad":
            self._start_vision_processing(replace_output=True, parse_scope="full")
        elif scan_mode == "auto":
            self._start_vision_processing(replace_output=False, parse_scope="incremental_auto")
        elif scan_mode == "semi":
            self._start_vision_processing(replace_output=False, parse_scope="incremental_semi")
        else:
            self._start_vision_processing(replace_output=False, parse_scope="incremental")
    else:
        self._replace_inventory_on_next_parse = False
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  계산 시작")
        self._pending_parse_only = False
        _scan_event(
            self,
            "INFO",
            "scanning.succeeded",
            "스캔은 완료했지만 새 장비를 캡처하지 못했습니다",
            captured_count=0,
            snapshot_written=False,
        )
        QMessageBox.information(
            self.dialog_parent,
            "스캔 완료",
            "새 장비를 캡처하지 못해 분석할 필요가 없습니다.",
        )


def _on_scan_error(self, err):
    _scan_event(
        self,
        "ERROR",
        "scanning.failed",
        "스캔 캡처 실패",
        stage="capture",
        error=err,
    )
    self._stop_scan_hotkeys()
    self.showNormal()
    self.activateWindow()
    self.btn_run.setEnabled(True)
    self.btn_run.setText("⚡  계산 시작")
    self._pending_parse_only = False
    QMessageBox.critical(
        self.dialog_parent,
        "스캔 실패",
        f"스캔 오류:\n{err}",
    )


