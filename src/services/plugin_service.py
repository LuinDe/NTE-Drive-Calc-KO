# 将全局插件偏好同步到既有原生会话，显示状态与战报采集独立。
from dataclasses import replace
from threading import RLock

from src.domain.plugin_settings import PluginSettings


_PLUGIN_FIELDS = {
    "cooldown": frozenset({"cooldown", "ready_cue"}),
    "enemy_bars": frozenset({"enemy_bars", "hp", "unbalance"}),
}


class PluginService:
    def __init__(self, *, store, policy, session):
        self.store, self.policy, self.session = store, policy, session
        self._lock = RLock()
        self._apply_lock = RLock()
        self.status = "미적용"
        self._statuses = {key: "미적용" for key in _PLUGIN_FIELDS}
        self.load_error = ""
        try:
            self.settings = store.load()
        except (ValueError, OSError):
            self.settings = PluginSettings()
            self.load_error = "플러그인 설정 읽기에 실패하여 현재 모두 꺼져 있습니다; 설정을 다시 저장하면 복구됩니다."

    @staticmethod
    def _plugin_enabled(settings: PluginSettings, key: str) -> bool:
        return bool(settings.cooldown) if key == "cooldown" else bool(
            settings.enemy_bars and (settings.hp or settings.unbalance)
        )

    def _refresh_aggregate_status(self, settings: PluginSettings) -> None:
        enabled = [
            self._statuses[key] for key in _PLUGIN_FIELDS
            if self._plugin_enabled(settings, key)
        ]
        values = enabled or list(self._statuses.values())
        self.status = values[0] if values and len(set(values)) == 1 else "일부 플러그인 상태가 다름"

    def status_for(self, key: str) -> str:
        if key not in _PLUGIN_FIELDS:
            raise KeyError(key)
        with self._lock:
            return self._statuses[key]

    def update(self, **changes):
        if any(not isinstance(value, bool) for value in changes.values()):
            raise ValueError("플러그인 스위치는 불리언 값이어야 함")
        with self._lock:
            updated = replace(self.settings, **changes)
            if updated.enabled and not self.policy.allowed("native_load"):
                # Keep editable display preferences in low risk; only enabling a plugin is gated.
                if any(changes.get(key) is True for key in ("cooldown", "enemy_bars")):
                    raise PermissionError("먼저 설정에서 중위험 또는 개발 모드로 전환한 후 플러그인을 켜세요.")
            self.store.save(updated)
            self.settings = updated
            self.load_error = ""
            changed = set(changes)
            for key, fields in _PLUGIN_FIELDS.items():
                if changed & fields:
                    self._statuses[key] = (
                        "설정 저장됨, 적용 대기 중"
                        if self._plugin_enabled(updated, key) else "표시 끄기 확인 대기"
                    )
            self._refresh_aggregate_status(updated)

    def observe(self, probe):
        with self._apply_lock:
            self._apply(game_running=probe.game_running, connect=True)

    def apply_current(self):
        """Apply UI changes on the existing connection without a full environment probe."""
        with self._apply_lock:
            self._apply(game_running=None, connect=False)

    def _apply(self, *, game_running, connect):
        self.apply_mode_policy()
        if getattr(self.session, 'maintenance_active', False):
            with self._lock:
                self._statuses = {key: '플러그인 업데이트 중이라 표시를 일시 중지했습니다' for key in _PLUGIN_FIELDS}
                self._refresh_aggregate_status(self.settings)
            return
        with self._lock:
            settings = self.settings
        allowed = self.policy.allowed("native_load") and not self.policy.settings.paused
        statuses = {key: "미적용" for key in _PLUGIN_FIELDS}
        if not allowed or not settings.enabled:
            try:
                self.session.configure_hud(PluginSettings().payload(), connect=False)
                status = "미적용" if allowed else "현재 모드에서 활성화 불가" if not self.policy.allowed("native_load") else "연결 일시 중지됨"
            except Exception:
                status = "표시 끄기 미확인, 연결 복구 대기 중"
            for key in statuses:
                if (status == "표시 끄기 미확인, 연결 복구 대기 중" or self._plugin_enabled(settings, key)
                        or not self.policy.allowed("native_load")):
                    statuses[key] = status
        elif game_running is False:
            status = "게임 대기 중"
            for key in statuses:
                if self._plugin_enabled(settings, key):
                    statuses[key] = status
        else:
            try:
                result = self.session.configure_hud(settings.payload(), connect=connect)
                status = ("현재 게임 버전은 이 표시 컴포넌트를 지원하지 않습니다" if result.get("rejected") else
                          "실행 중" if result.get("installed") else "조작 가능한 장면 대기 중")
            except Exception as error:
                status = str(error) or "플러그인 연결 실패, 컴포넌트를 다시 검사하세요"
                for key in statuses:
                    if not self._plugin_enabled(settings, key):
                        statuses[key] = "표시 끄기 미확인, 연결 복구 대기 중"
            for key in statuses:
                if self._plugin_enabled(settings, key):
                    statuses[key] = status
        with self._lock:
            if settings == self.settings:
                self._statuses = statuses
                self._refresh_aggregate_status(settings)
        if not settings.enabled and not self.policy.allowed("native_sync", automatic=True):
            self.session.close_if_idle()

    def apply_mode_policy(self):
        """Revoke saved switches on downgrade, while retaining display options."""
        with self._lock:
            if self.policy.allowed("native_load"):
                return
            if not (self.settings.cooldown or self.settings.enemy_bars):
                return
            self.settings = replace(self.settings, cooldown=False, enemy_bars=False)
            self._statuses = {key: "현재 모드에서 활성화 불가" for key in _PLUGIN_FIELDS}
            self._refresh_aggregate_status(self.settings)
            try:
                self.store.save(self.settings)
            except OSError:
                self.load_error = "플러그인은 꺼졌지만 꺼짐 상태 저장에 실패했습니다. 구성 디렉터리 권한을 확인하세요."
                raise
            self.load_error = ""
