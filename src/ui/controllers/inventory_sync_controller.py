# 从 MainWindow 抽离的控制器方法。
"""Compatibility-installed MainWindow controller."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from src.app.workers import WorkerThread
from src.features.home.page import inventory_sync_error_guidance
from src.observability import OperationContext
from src.services.inventory_sync_service import InventorySyncService, InventorySyncState
from src.integrations.nte_core import NteCoreClient
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger


def _start_inventory_sync(self):
    service=self._inventory_sync_service
    if service is not None and service.is_running:
        return
    account = self.app_context.account
    raw_capture_directory = account.log_dir / "nte_core" / "raw_capture"
    service=InventorySyncService(
        account.user_database_path,
        account_id=account.active_account_id,
        account_name=account.active_account_name,
        client_factory=lambda: NteCoreClient(
            data_dir=raw_capture_directory,
            cwd=self.app_context.paths.app_dir,
        ),
        raw_capture_directory=raw_capture_directory,
        operation_context=OperationContext.create(
            "inventory_sync",
            account_id=account.active_account_id,
            context_generation=self.app_context.generation,
        ),
    )
    service.add_state_handler(self.inventory_sync_state_signal.emit)
    self._inventory_sync_service=service
    service.start()

def _get_sync_settings(self):
    return self._account_settings.load("sync")


def _open_raw_capture_directory(self):
    directory = (
        self.app_context.account.log_dir / "nte_core" / "raw_capture"
    )
    try:
        directory.mkdir(parents=True, exist_ok=True)
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
    except OSError as exc:
        QMessageBox.warning(self, "진단 패킷 캡처", f"패킷 캡처 디렉터리를 열 수 없습니다: {exc}")
        return
    if not opened:
        QMessageBox.information(self, "진단 패킷 캡처", f"패킷 캡처 디렉터리:\n{directory}")

def _save_sync_settings(self):
    try:
        was_running=bool(self._inventory_sync_service and self._inventory_sync_service.is_running)
        values=self._account_settings.load("sync")
        values.update(
            {
                "inventory_sync_method":self._sync_inventory_method_combo.currentData(),
                "capture_device_id":self._sync_capture_device_edit.text(),
                "raw_capture_enabled":self._sync_raw_capture_toggle.isChecked(),
                "inventory_settle_seconds":self._sync_settle_spin.value(),
                "auto_start_inventory_sync":self._sync_auto_start_toggle.isChecked(),
                "inventory_snapshot_retention_count":self._snapshot_retention_spin.value(),
            }
        )
        settings=self._account_settings.save("sync",values)
        if was_running:
            self._stop_inventory_sync()
            self._start_inventory_sync()
        QMessageBox.information(self,"동기화 설정","동기화 설정을 저장했습니다.")
        return settings
    except Exception as exc:
        QMessageBox.warning(self,"동기화 설정",f"저장 실패: {exc}")
        return None

def _prune_inventory_snapshots(self):
    current_worker = getattr(self, "_snapshot_prune_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "스냅샷 유지 관리", "기록 스냅샷을 정리하는 중입니다. 현재 작업이 끝날 때까지 기다리세요.")
        return
    retain_recent = self._snapshot_retention_spin.value()
    message = (
        f"최근 안정 가방 스냅샷 {retain_recent}개를 남깁니다.\n\n"
        "현재 스냅샷과 저장된 모든 장착 방안이 참조하는 스냅샷은 항상 유지됩니다."
        "그 외 기록 스냅샷과 그 가방 아이템·스탯 기록은 삭제됩니다.\n\n"
        "이 작업은 장착 방안을 수정하지 않습니다. 계속할까요?"
    )
    if QMessageBox.question(
        self,
        "기록 스냅샷 정리 확인",
        message,
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    ) != QMessageBox.Yes:
        return

    database_path = self.app_context.account.user_database_path
    if hasattr(self, "_prune_snapshots_button"):
        self._prune_snapshots_button.setEnabled(False)
    worker = WorkerThread(
        target=lambda: self._prune_inventory_snapshots_task(
            database_path, retain_recent
        ),
        parent=self,
    )
    self._snapshot_prune_worker = worker
    worker.result_ready.connect(self._on_inventory_snapshots_pruned)
    worker.error.connect(self._on_inventory_snapshot_prune_error)
    worker.start()

def _prune_inventory_snapshots_task(database_path, retain_recent):
    with UserDataDao(database_path) as dao:
        return dao.prune_inventory_snapshots(retain_recent=retain_recent)

def _on_inventory_snapshots_pruned(self, result):
    if hasattr(self, "_prune_snapshots_button"):
        self._prune_snapshots_button.setEnabled(True)
    self._refresh_home()
    QMessageBox.information(
        self,
        "스냅샷 유지 관리 완료",
        "정리 완료:"
        f"기록 스냅샷 {result['deleted_snapshot_count']}개를 정리했고,"
        f"현재 {result['total_after']}개를 보관 중입니다.\n\n"
        "현재 스냅샷과 장착 방안이 참조하는 스냅샷은 삭제되지 않았습니다."
        "SQLite 데이터베이스 파일 크기는 바로 줄지 않을 수 있지만, 공간은 이후 동기화에 재사용됩니다.",
    )

def _on_inventory_snapshot_prune_error(self, error):
    if hasattr(self, "_prune_snapshots_button"):
        self._prune_snapshots_button.setEnabled(True)
    QMessageBox.warning(self, "스냅샷 유지 관리", f"정리 실패: {error}")

def _maybe_auto_start_inventory_sync(self):
    try:
        settings=self._get_sync_settings()
    except Exception as exc:
        logger.debug(f"자동 동기화 설정 읽기 실패: {exc}")
        return
    if (
        settings.get("inventory_sync_method")=="nte_core"
        and settings.get("auto_start_inventory_sync")
    ):
        self._start_inventory_sync()

def _stop_inventory_sync(self):
    service=self._inventory_sync_service
    if service is None:
        return
    service.remove_state_handler(self.inventory_sync_state_signal.emit)
    if service.is_running:
        service.stop()
    self._inventory_sync_service=None
    if hasattr(self,"home_sync_badge"):
        from src.ui.dashboard_widgets import set_status_badge
        set_status_badge(self.home_sync_badge,"중지됨","neutral")
        self.home_sync_detail.setText("백그라운드 가방 동기화가 중지되었습니다. 데이터베이스의 안정 스냅샷은 계속 계산에 사용할 수 있습니다.")
        self.home_start_sync_button.setEnabled(True)
        self.home_stop_sync_button.setEnabled(False)

def _on_inventory_sync_state(self,state):
    if not isinstance(state,InventorySyncState):
        return
    refresh_warehouse = getattr(self, "_on_warehouse_sync_state", None)
    if callable(refresh_warehouse):
        refresh_warehouse(state)
    if not hasattr(self,"home_sync_badge"):
        return
    from src.ui.dashboard_widgets import set_status_badge
    tone={
        "starting":"active","waiting":"warning","collecting":"active",
        "saving":"active","listening":"success","error":"error","stopped":"neutral",
    }.get(state.phase,"neutral")
    label={
        "starting":"시작 중","waiting":"게임 접속 대기 중","collecting":"수신 중",
        "saving":"저장 중","listening":"백그라운드 감시","error":"동기화 이상","stopped":"중지됨",
    }.get(state.phase,state.phase)
    set_status_badge(self.home_sync_badge,label,tone)
    detail=state.message
    if state.pending_item_count is not None:
        detail+=f" · 현재 {state.pending_item_count}개"
    if state.error:
        detail+=f"\n\n{inventory_sync_error_guidance(state.error_code, state.error)}"
        detail+=f"\n\n기술 상세: {state.error}"
    self.home_sync_detail.setText(detail)
    self.home_start_sync_button.setEnabled(not state.running)
    self.home_stop_sync_button.setEnabled(state.running)
    self.status_lbl.setText(label)
    self.status_lbl.setStyleSheet(
        "color:#f85149;font-size:12px" if state.phase=="error"
        else "color:#3fb950;font-size:12px" if state.phase=="listening"
        else "color:#d2991d;font-size:12px"
    )
    if state.phase=="listening" and state.last_snapshot_id is not None:
        self._refresh_home()

# ── Page: Execute

# ── Page: Equipment

# ── Page: Identify

# ── Page: Blueprint

# ── Page: Config


class InventorySyncControllerMixin:
    _start_inventory_sync = _start_inventory_sync
    _get_sync_settings = _get_sync_settings
    _save_sync_settings = _save_sync_settings
    _open_raw_capture_directory = _open_raw_capture_directory
    _prune_inventory_snapshots = _prune_inventory_snapshots
    _prune_inventory_snapshots_task = staticmethod(
        _prune_inventory_snapshots_task
    )
    _on_inventory_snapshots_pruned = _on_inventory_snapshots_pruned
    _on_inventory_snapshot_prune_error = _on_inventory_snapshot_prune_error
    _maybe_auto_start_inventory_sync = _maybe_auto_start_inventory_sync
    _stop_inventory_sync = _stop_inventory_sync
    _on_inventory_sync_state = _on_inventory_sync_state
