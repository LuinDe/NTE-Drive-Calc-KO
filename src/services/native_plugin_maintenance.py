# 协调原生功能暂停、连接排空与插件更新，未知结果保留维护门禁。
from src.integrations.native_host_control import NativeHostOutcomeUnknown
from src.services.native_plugin_update import update_native_plugins
from src.observability import OperationContext, log_event


def active_feature_update_hint(owner):
    return {
        'battle_report': '전투 리포트를 녹화 중이거나 마무리하는 중입니다. 전투 리포트 페이지에서 녹화를 중지하고 저장이 끝날 때까지 기다린 뒤 플러그인을 업데이트하세요.',
        'automatic_equipment_apply': '자동 장착이 게임 화면을 조작하는 중입니다. 장착이 끝나기를 기다리거나 장착을 중지한 뒤 플러그인을 업데이트하세요.',
        'rewind_execution': '되감기가 게임 조작을 실행하는 중입니다. 이번 실행이 끝나기를 기다리거나 되감기를 중지한 뒤 플러그인을 업데이트하세요.',
        'scanning': '가방 스캔이 진행 중입니다. 스캔이 끝나기를 기다리거나 스캔을 중지한 뒤 플러그인을 업데이트하세요.',
        'character_profile_sync': '캐릭터 육성 동기화가 진행 중입니다. 동기화가 끝난 뒤 플러그인을 업데이트하세요.',
    }.get(owner, '아직 끝나지 않은 게임 조작이 감지되었지만 어떤 기능인지 식별하지 못했습니다. 실행 중인 작업을 확인하세요. 플러그인은 아직 업데이트하지 않았습니다.')


class NativePluginMaintenance:
    """UI owns begin/finish; the update worker owns run and bounded draining."""

    def __init__(self, *, session, sync, performance, check_blockers, reconnect):
        self.session, self.sync, self.performance = session, sync, performance
        self.check_blockers, self.reconnect = check_blockers, reconnect
        self.lease = None
        self.uncertain = False

    def begin(self):
        self.check_blockers()
        self.lease = self.session.reserve_plugin_maintenance()
        self.context = OperationContext.create('native_plugin_update')
        log_event('INFO', 'native_plugin_update.pausing', '플러그인 업데이트: 네이티브 기능 일시 중지', self.context)
        try:
            self.performance.suspend_for_plugin_update()
            self.sync.suspend_for_plugin_update()
        except Exception:
            self.finish(restore=True)
            raise

    def run(self, *, application_root, directory, game_pid, operation_guard,
            update=update_native_plugins):
        def check():
            operation_guard('native_load')

        check()
        if not self.performance.wait_plugin_update_idle():
            raise RuntimeError('성능 샘플링이 아직 마무리되지 않아 플러그인 업데이트를 실행하지 않았습니다.')
        self.lease.disconnect(check=check)
        log_event('INFO', 'native_plugin_update.disconnected', '플러그인 업데이트: 수집 연결 해제됨', self.context)
        check()
        try:
            result = update(application_root=application_root, directory=directory,
                            game_pid=game_pid, operation_guard=operation_guard)
            log_event('INFO', 'native_plugin_update.result', '플러그인 업데이트: 호스트 결과 확인됨',
                      self.context, state=result.state)
            return result
        except NativeHostOutcomeUnknown:
            self.uncertain = True
            log_event('WARNING', 'native_plugin_update.unknown', '플러그인 업데이트 결과를 알 수 없음, 유지 관리 일시 중지 유지', self.context)
            raise

    def finish(self, *, restore):
        if self.lease is None or self.uncertain:
            return False
        self.lease.close()
        self.lease = None
        self.performance.resume_after_plugin_update(restore=restore)
        self.sync.resume_after_plugin_update(restore=restore)
        if restore:
            self.reconnect()
        log_event('INFO', 'native_plugin_update.released', '플러그인 유지 관리 구간 종료됨', self.context,
                  reconnect_requested=restore)
        return True
