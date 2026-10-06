# 在共享原生会话中派发性能显示请求，不启动背包或战报采集。
from src.integrations.nte_core_protocol import NteCoreRpcError


def configure_performance(session, payload):
    enabled = payload["enabled"]
    if enabled:
        session._guard("native_load")
    # The session lock serializes this short control RPC with account close/replacement.
    with session._lock:
        client = session._connect() if enabled else session._client
        if client is None or not client.is_running:
            if enabled:
                raise LookupError("네이티브 컴포넌트 대기 중")
            return {"enabled": False, "overlay": False, "installed": False, "rejected": False}
        if "native_performance_v1" not in (client.hello_result or {}).get("capabilities", ()):
            raise NotImplementedError("현재 컴포넌트는 성능 오버레이를 지원하지 않습니다. 호환 컴포넌트 업데이트가 필요합니다")
        result = client.call("native.performance.configure", payload, timeout=2.0)
        if (not isinstance(result, dict)
                or any(type(result.get(k)) is not bool for k in ("enabled", "overlay", "installed", "rendered", "rejected"))
                or result["enabled"] != enabled or result["overlay"] != payload["overlay"]
                or result.get("cost_coverage") != "native_dispatch_and_hud_v1"
                or result.get("cost_complete") is not False
                or (result.get("cost_us") is not None and
                    (type(result["cost_us"]) is not int or result["cost_us"] < 0))):
            raise ValueError("성능 컴포넌트가 반환한 상태가 확인되지 않았습니다")
        session._performance_active = enabled
        return result


def read_performance_status(self):
    """Read counters on an existing connection; never start capture or connect."""
    self._guard("native_sync")
    with self._lock:
        client, context = self._client, self._current_context()
        if (client is None or self._failed or self._close_requested.is_set()
                or not self._context_matches() or not client.is_running):
            raise LookupError("native_session_unavailable")
    status = self._status_queries.read(client, timeout=2.0)
    self._guard("native_sync")
    if (client is not self._client or context != self._current_context()
            or self._close_requested.is_set()):
        raise LookupError("native_session_changed")
    return client, status


def control_performance_trace(session, payload, context_key):
    if payload['action'] != 'stop':
        session._guard('native_load')
    with session._lock:
        if session._current_context() != context_key or session._close_requested.is_set():
            raise LookupError('계정 또는 세션이 변경되어 새 세션에 샘플링 명령을 보내지 않았습니다')
        client = session._connect() if payload['action'] == 'start' else session._client
        if client is None or not client.is_running:
            raise LookupError('네이티브 세션 연결이 끊어졌습니다')
        if 'native_performance_trace_v1' not in (client.hello_result or {}).get('capabilities', ()):
            raise NotImplementedError('현재 컴포넌트는 세부 성능 샘플링을 지원하지 않습니다. 호환 Core, 호스트, 플러그인을 업데이트해야 합니다')
        if session._current_context() != context_key:
            raise LookupError('계정이 변경되었습니다')
        if payload['action'] == 'start':
            session._performance_trace_active = True  # An unknown result must retain the lease.
            session._performance_trace_owner = (client, payload['trace_id'])
        elif session._performance_trace_owner != (client, payload['trace_id']):
            raise LookupError('샘플링이 속한 연결이 변경되어 새 연결에 이전 샘플링 명령을 보내지 않았습니다')
        try:
            result = client.call('native.performance.trace', payload, timeout=2.0)
        except NteCoreRpcError as error:
            if payload['action'] == 'start' and error.message in {
                'performance_plugin_unavailable', 'performance_busy', 'performance_start_failed',
                'invalid_trace_id', 'invalid_action', 'invalid_services',
            }:
                session._performance_trace_active = False
            raise
        if isinstance(result, dict) and result.get('trace_id') == payload['trace_id'] and result.get('active') is False:
            session._performance_trace_active = False
        return result
