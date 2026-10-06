# 管理首页自动背包同步的进程观察、异步收尾和重新同步请求。
from __future__ import annotations

from threading import Thread

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from src.integrations.game_process_watcher import GameProcessWatcher
from src.ui.controllers.sync_recovery import SyncRecovery


class AutoSyncController(QObject):
    process_changed = Signal(object)
    stop_finished = Signal(object)
    state_changed = Signal(object)
    preparation_changed = Signal(object)

    def __init__(self, *, window, policy, request_check, watcher_factory=GameProcessWatcher,
                 submit_stop=None, request_enable_preflight=None):
        super().__init__(window)
        self.window, self.policy = window, policy
        self._request_check = request_check
        self._request_enable_preflight = request_enable_preflight
        self._watcher_factory = watcher_factory
        self._submit_stop = submit_stop or self._background
        self._closed = False
        self._maintenance = False
        self._started = False
        self._key = None
        self._watcher = None
        self._watch_token = None
        self._process = None
        self._probe = None
        self._probe_context = None
        self._attempt = None
        self._recovery = SyncRecovery()
        self._stopping = None
        self._stop_failed = False
        self._retry_cancelled = False
        self._restarting = False
        self._dialog = None
        self._detail = ""
        self._last_preparation = object()
        self.process_changed.connect(self._process_event)
        self.stop_finished.connect(self._stopped)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)

    @staticmethod
    def _background(job):
        Thread(target=job, name="inventory-sync-teardown", daemon=True).start()

    def _context(self):
        settings = self.policy.settings
        context = self.window.app_context
        return (context.account.active_account_id, context.generation, settings.mode,
                settings.risk_confirmed, settings.paused, settings.auto_sync_enabled, self.policy.operation_revision)

    @property
    def packet_game_running(self):
        """None means the watcher is not the active process observer."""
        if self._closed or self._watch_token is None:
            return None
        return self._process is not None

    @property
    def preparation_state(self):
        if self._stopping:
            return 'stopping'
        if self._stop_failed or self._retry_cancelled:
            return None
        if self.policy.allowed('native_sync'):
            if self.policy.settings.pending_cleanup:
                if self._probe_context != self._context() or self._probe is None:
                    return 'checking_game'
                return 'waiting_game_exit' if self._probe.game_running else 'waiting_deployment'
            if self._probe_context != self._context() or self._probe is None:
                return 'checking_game'
            if self._probe.native_load.files is False:
                return 'waiting_game_exit' if self._probe.game_running else 'waiting_deployment'
            if self._probe.game_running is False:
                return 'waiting_game'
            if not (self._probe.core_available and self._probe.native_inventory.handshake):
                return 'waiting_component'
        elif self.policy.allowed('packet_capture') and self._process is None:
            return 'waiting_game'
        return None

    def start(self):
        if self._closed:
            return
        self._started = True
        self._timer.start()
        self.refresh()

    def observe_probe(self, probe):
        self._probe = probe
        self._probe_context = self._context()
        if self.policy.allowed("native_sync") and probe.game_running is False:
            self._stop_inventory()
        self.refresh()

    def _stop_watcher(self):
        self._watch_token = None
        self._process = None
        if self._watcher is not None:
            self._watcher.stop()

    def _ensure_watcher(self):
        if self._watch_token is not None:
            return
        if self._watcher is not None and getattr(self._watcher, "worker_alive", self._watcher.is_running):
            return
        token = object()
        self._watch_token = token
        frozen = self._context()
        self._watcher = self._watcher_factory(
            on_change=lambda event: self._publish_process((token, frozen, event)),
        )
        self._watcher.start()

    def _publish_process(self, event):
        if not self._closed:
            try:
                self.process_changed.emit(event)
            except RuntimeError:
                pass  # Parent Qt object may already have been destroyed during shutdown.

    def _process_event(self, message):
        token, frozen, event = message
        if self._closed or token is not self._watch_token or frozen != self._context():
            return
        if event.kind == "started":
            self._process = event.process
            self._attempt = None
            self._retry_cancelled = False
            self._detail = "게임을 발견했습니다. 가방 모니터링을 준비하는 중입니다."
        elif event.kind == "exited":
            if self._process != event.process:
                return
            self._process = None
            self._attempt = None
            self._retry_cancelled = False
            self._detail = "게임이 종료되어 다음 시작을 기다립니다. 저장된 가방은 여전히 계산에 사용할 수 있습니다."
            self._stop_inventory()
        elif event.kind == "error":
            self._detail = event.error
            self._process = None
            self._attempt = None
            self._stop_inventory()
        elif event.kind == "waiting" and self._process is None:
            self._detail = "게임 시작 대기 중; 게임이 발견되면 자동으로 가방 모니터링을 시작합니다."
        self.refresh()

    def set_enabled(self, enabled):
        if self._closed:
            return
        if enabled and (not self.policy.settings.auto_sync_enabled or self.policy.settings.paused) and self._request_enable_preflight:
            self.render()  # Keep the persisted off state visible until confirmation.
            self._request_enable_preflight(self._confirm_enable)
            return
        if enabled and not (self.policy.allowed("native_sync") or self.policy.allowed("packet_capture")):
            self.window.operation_entry("game_sync", "자동 동기화")
            self.render()
            return
        if enabled and self.policy.settings.paused:
            self.window.operation_unavailable(
                "자동 동기화", "연결이 일시 중지되었습니다. 검사 상세 정보를 확인하고 동기화 조건 점검을 완료하세요.", target="detection",
            )
            self.render()
            return
        try:
            self.policy.set_auto_sync_enabled(enabled)
        except OSError:
            QMessageBox.warning(self.window, "자동 동기화", "자동 동기화 설정을 저장하지 못했습니다. 설정 디렉터리를 확인한 후 다시 시도하세요.")
        self.refresh()
        if self.policy.settings.auto_sync_enabled:
            self._request_check()

    def _confirm_enable(self) -> bool:
        if self._closed or (self.policy.settings.auto_sync_enabled and not self.policy.settings.paused):
            return False
        if not (self.policy.allowed("native_sync") or self.policy.allowed("packet_capture")):
            self.render()
            return False
        try:
            self.policy.enable_auto_sync_after_preflight(
                resume_paused=self.policy.settings.paused,
            )
        except (OSError, PermissionError):
            self.render()
            return False
        self.refresh()
        return True

    def refresh(self):
        if self._closed or not self._started:
            return
        if self._maintenance:
            self.render()
            return
        key = self._context()
        if key != self._key:
            self._recovery.reset()
            if self._key is not None:
                self._stop_inventory()
            self._key = key
            self._stop_watcher()
            self._attempt = None
            self._retry_cancelled = False
            self._restarting = False
            self._detail = ""
            if self._dialog is not None:
                self._dialog.context_changed()
        native = self.policy.allowed("native_sync", automatic=True)
        packet = not self.policy.allowed("native_sync") and self.policy.allowed("packet_capture", automatic=True)
        if not (native or packet):
            self._stop_watcher()
            self.render()
            return
        if packet:
            self._ensure_watcher()
            ready = self._process is not None
            identity = self._process
        else:
            probe = self._probe
            ready = bool(self._probe_context == key and probe and probe.game_running is not False
                         and not self.policy.settings.pending_cleanup
                         and probe.native_load.files is not False
                         and probe.core_available and probe.native_inventory.handshake)
            identity = "native"
            if not ready:
                self._attempt = None
        service = self.window._inventory_sync_service
        can_start = (ready and self._stopping is None and not self._stop_failed and not self._retry_cancelled
                and not self.window.work_mode_controller.is_transitioning
                and not (native and self.window.battle_report_controller.is_running())
                and not (service and service.is_running))
        if can_start and native and getattr(getattr(service, 'state', None), 'stop_reason', None) == 'connection_lost':
            can_start = self._recovery.ready(service)
            if can_start:
                self._attempt = None
        if can_start and self._attempt != (key, identity):
            self._attempt = key, identity
            self.window._start_inventory_sync(automatic=True)
        self.render()

    def _stop_inventory(self):
        service = self.window._inventory_sync_service
        if service is None or self._stopping is service:
            return
        self.window.invalidate_inventory_sync_notifications()
        service.request_stop()
        self._stopping = service

        def finish():
            error = ""
            try:
                service.stop()
            except Exception:
                error = "이전 가방 동기화가 아직 멈추지 않았습니다. 마무리를 기다리거나 다시 동기화하세요."
            if not self._closed:
                try:
                    self.stop_finished.emit((service, error))
                except RuntimeError:
                    pass  # The UI can be destroyed after cancellation but before join finishes.

        self._submit_stop(finish)

    def suspend_for_plugin_update(self):
        """GUI-thread entry; preserve saved preference and stop the current owner."""
        self._maintenance = True
        self._stop_inventory()
        self.render()

    def resume_after_plugin_update(self, *, restore=True):
        self._maintenance = False
        self._attempt = None
        self._probe = None
        self._probe_context = None
        if restore:
            self.refresh()

    def _stopped(self, result):
        service, error = result
        if service is not self._stopping:
            return
        self._stopping = None
        if self._closed:
            return
        self._stop_failed = bool(error and service.is_running)
        if self._stop_failed:
            self._detail = error
        elif service is self.window._inventory_sync_service:
            self.window._stop_inventory_sync()
        self.refresh()

    def open_restart(self):
        if self._closed:
            return
        if self.policy.settings.paused:
            self.set_enabled(True)
            return
        if not self.policy.settings.auto_sync_enabled:
            self.set_enabled(True)
            return
        if self.window.battle_report_controller.is_running():
            return
        if self._dialog is not None:
            self._dialog.show()
            self._dialog.raise_()
            return
        if not self.policy.allowed("native_sync") and not self.policy.allowed("packet_capture"):
            self.window.operation_entry("game_sync", "동기화 재시작")
            return
        from src.features.home.sync_retry_dialog import SyncRetryDialog
        dialog = SyncRetryDialog(self.window, controller=self, native=self.policy.allowed("native_sync"))
        self._dialog = dialog
        dialog.finished.connect(lambda _result: self._clear_dialog(dialog))
        dialog.show()

    def _clear_dialog(self, dialog):
        if self._dialog is dialog:
            self._dialog = None
        dialog.deleteLater()

    def restart(self):
        if self._closed or self._maintenance or self.window.battle_report_controller.is_running():
            return
        capability = "native_sync" if self.policy.allowed("native_sync") else "packet_capture"
        if not self.policy.allowed(capability, automatic=True):
            return
        self._recovery.reset()
        self._attempt = None
        self._retry_cancelled = False
        self._stop_failed = False
        self._restarting = True
        self._detail = "이전 세션을 중지하고 동기화를 다시 설정하는 중입니다. 저장된 가방은 계속 사용할 수 있습니다."
        if self._watcher is not None and not self._watcher.is_running:
            self._stop_watcher()
        self._stop_inventory()
        self.refresh()

    def cancel_restart(self):
        if not self._restarting:
            return
        self._restarting = False
        self._retry_cancelled = True
        self._detail = '이번 재시작 동기화가 취소되었습니다. "재시작 동기화"를 클릭해 다시 시도할 수 있습니다. 저장된 가방은 계속 사용할 수 있습니다.'
        self._stop_inventory()
        self.render()

    def inventory_state_changed(self, state):
        if self._closed:
            return
        if state.source_snapshot_ready and state.phase == "listening":
            self._restarting = False
            self._recovery.reset()
        self.state_changed.emit(state)
        self.render()

    def render(self):
        preparation = self.preparation_state
        if preparation != self._last_preparation:
            self._last_preparation = preparation
            self.preparation_changed.emit(preparation)
        toggle = getattr(self.window, "home_auto_sync_toggle", None)
        if toggle is None:
            return
        settings = self.policy.settings
        toggle.blockSignals(True)
        toggle.setChecked(settings.auto_sync_enabled and not settings.paused)
        toggle.blockSignals(False)
        native = self.policy.allowed("native_sync")
        online = native or self.policy.allowed("packet_capture")
        source = ("출처: 게임 내 컴포넌트" if native else
                  "출처: 패킷 캡처 (캐릭터 육성은 수동 관리 필요)")
        source_tip = (
            "가방, 현재 장비 및 캐릭터 육성(레벨, 돌파, 스킬, 호감도, 아크)을 동기화하고 변경 사항을 계속 모니터링합니다."
            if native else
            "가방과 현재 장비를 동기화하고 변경 사항을 계속 모니터링합니다; 캐릭터 육성은 수동으로 관리해야 합니다."
        )
        if not online:
            source = "오프라인 모드: 저장된 데이터 사용"
            source_tip = "게임에 연결하지 않으며, 새 데이터도 수집하지 않습니다."
        self.window.home_sync_source_label.setText(source)
        self.window.home_sync_source_label.setToolTip(source_tip)
        self.window.home_sync_source_label.hide()
        title = getattr(self.window, "home_sync_title", None)
        if title is not None:
            title.setText("가방 동기화" if online and not native else "게임 데이터 동기화")
        service = self.window._inventory_sync_service
        state = service.state if service is not None else None
        role_detail = getattr(self.window, "home_character_sync_detail", None)
        if role_detail is not None:
            error = getattr(state, "character_sync_error", None)
            role_detail.setVisible(bool(native and error))
            if native and error:
                role_detail.setText(error)
        battle = self.window.battle_report_controller.is_running()
        button = self.window.home_restart_sync_button
        button.setText("자동 동기화 재개" if settings.paused else
                       "동기화 재시작" if settings.auto_sync_enabled else "자동 동기화 켜기")
        button.setToolTip(
            "먼저 동기화 조건을 대조합니다. 구성 요소가 준비되고 정리가 완료되면 동기화를 재개하며, 작업 모드를 다시 선택할 필요가 없습니다."
            if settings.paused else
            "현재 동기화 연결을 중지하고 다시 연결합니다. 동기화 이상 또는 가방이 갱신되지 않을 때 사용합니다. 저장된 데이터는 삭제되지 않습니다."
            if settings.auto_sync_enabled else
            "먼저 환경 검사를 표시하고, 준비 확인 후 게임 장면에 들어가면 자동으로 데이터를 읽어 저장합니다." if native else
            "먼저 패킷 캡처 조건 검사를 표시하고, 확인 후 게임에 로그인하면 데이터가 자동으로 저장됩니다."
        )
        button.setEnabled(not self._maintenance and not self._stopping and (not battle or not settings.auto_sync_enabled))
        hint = "먼저 전투 리포트를 종료한 후 동기화를 다시 시작하세요." if battle and settings.auto_sync_enabled else ""
        self.window.home_sync_action_hint.setText(hint)
        self.window.home_sync_action_hint.setVisible(bool(hint))
        detail = None
        if self._maintenance:
            detail = '플러그인 업데이트 중이라 동기화를 잠시 멈췄습니다; 저장된 가방은 계속 사용할 수 있습니다.'
        elif not online:
            detail = "오프라인 모드입니다. 저장된 가방을 사용합니다."
        elif settings.paused:
            detail = "연결이 일시 중지되었습니다; 「자동 동기화 재개」를 클릭해 조건을 점검하면 되며, 작업 모드를 다시 선택할 필요는 없습니다."
        elif not settings.auto_sync_enabled:
            detail = "자동 동기화가 꺼져 있습니다. 저장된 가방은 계속 계산에 사용할 수 있습니다."
        elif self._stopping or self._stop_failed or self._retry_cancelled:
            detail = self._detail or "지난 동기화를 마무리하는 중입니다. 저장된 가방은 계속 계산에 사용할 수 있습니다."
        elif native and battle:
            detail = "전투 리포트 수집 중에는 가방과 캐릭터 새로 고침이 잠시 대기하며, 종료 후 자동으로 재개됩니다."
        elif native and preparation == 'checking_game':
            detail = "게임 실행 여부를 감지하는 중입니다. 저장된 가방은 계속 계산에 사용할 수 있습니다."
        elif native and preparation == 'waiting_game_exit':
            detail = "컴포넌트의 배포 또는 업데이트가 아직 완료되지 않았으니, 게임을 완전히 종료하고 배포가 완료된 후 다시 시작하여 게임 장면에 진입하세요."
        elif native and preparation == 'waiting_deployment':
            detail = "컴포넌트의 배포 또는 업데이트가 아직 완료되지 않았으니 당분간 게임을 시작하지 마세요; 검사 상세 정보를 확인하고, 배포를 완료한 후 게임을 시작하여 게임 장면에 진입하세요."
        elif native and preparation == 'waiting_game':
            detail = "게임 시작 대기 중; 로그인하여 게임 장면에 진입한 후, 가방과 캐릭터 데이터 동기화가 완료될 때까지 기다려 주세요."
        elif not native and self._process is None:
            detail = self._detail or "게임 시작 대기 중; 게임이 발견되면 자동으로 가방 모니터링을 시작합니다."
        elif self._recovery.waiting:
            detail = "네이티브 연결이 끊어져 가방 모니터링의 자동 복구를 기다리는 중입니다; 저장된 가방은 계속 사용할 수 있습니다."
        elif state is not None and state.phase == "stopped" and not service.is_running:
            detail = "가방 동기화 모니터링이 중지되어 이번에는 더 이상 데이터를 받지 않습니다. “동기화 재시작”을 클릭하세요; 저장된 가방은 계속 계산에 사용할 수 있습니다."
        elif state is None or not service.is_running:
            detail = (state.message if state and state.phase == "error" else
                      "게임 내 컴포넌트 준비 대기 중; 게임 장면에 진입하여 동기화가 완료될 때까지 기다려 주세요." if native else self._detail or "가방 모니터링을 준비하는 중입니다.")
        elif state.capturing and not state.source_snapshot_ready:
            detail = (
                state.message + "\n게임 장면에 진입해 동기화가 끝날 때까지 기다리세요; 현재 표시된 것은 여전히 마지막으로 저장된 가방입니다." if native else
                "패킷 캡처 수신 대기가 준비되었습니다. 전체 가방을 가져오려면 게임에 로그인하세요; 현재 표시되는 것은 여전히 마지막으로 저장된 가방입니다."
            )
        if detail is not None:
            self.window.home_sync_detail.setText(detail)
        badge = getattr(self.window, "home_sync_badge", None)
        if badge is not None:
            if self._maintenance:
                title, tone = '플러그인 업데이트 중', 'active'
            elif not online or not settings.auto_sync_enabled or settings.paused:
                title, tone = "동기화 꺼짐", "neutral"
            elif self._stopping:
                title, tone = "마무리 중", "active"
            elif self._stop_failed:
                title, tone = "동기화 이상", "error"
            elif self._retry_cancelled:
                title, tone = "재시도 대기 중", "warning"
            elif native and battle:
                title, tone = "전투 리포트 종료 대기 중", "warning"
            elif native and preparation == 'checking_game':
                title, tone = "게임 감지", "active"
            elif native and preparation == 'waiting_game_exit':
                title, tone = "게임 종료 대기 중", "warning"
            elif native and preparation == 'waiting_deployment':
                title, tone = "배포 대기 중", "warning"
            elif native and preparation == 'waiting_game':
                title, tone = "게임 대기 중", "warning"
            elif not native and self._process is None:
                title, tone = "게임 대기 중", "warning"
            elif self._recovery.waiting:
                title, tone = "복구 대기 중", "warning"
            elif state and state.phase == "error":
                title, tone = "동기화 이상", "error"
            elif native and preparation == 'waiting_component':
                title, tone = "컴포넌트 대기 중", "warning"
            elif state and state.phase == "stopped" and not service.is_running:
                title, tone = "동기화 중지됨", "warning"
            elif state and state.phase == "listening" and state.source_snapshot_ready:
                title, tone = "지속 수신 대기", "success"
            elif state and state.phase in {"collecting", "saving"}:
                title, tone = "동기화 중", "active"
            else:
                title, tone = "가방 대기 중", "warning"
            if badge.text() != title:
                from src.ui.dashboard_widgets import set_status_badge
                set_status_badge(badge, title, tone)
            header = getattr(self.window, "status_lbl", None)
            if header is not None:
                header.setText(title)
                color = {"error": "#f85149", "success": "#3fb950", "neutral": "#8b949e"}.get(tone, "#d2991d")
                header.setStyleSheet(f"color:{color};font-size:12px")

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._stop_watcher()
        if self._dialog is not None:
            self._dialog.context_changed()
        self._stop_inventory()
