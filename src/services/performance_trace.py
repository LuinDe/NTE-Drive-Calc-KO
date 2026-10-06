# 独立管理手动细分采样的后台控制、账号代次与停止收尾。
from threading import Event, RLock, Thread
from time import monotonic
from uuid import uuid4

from src.integrations.performance_trace import TraceJournal, validate_trace
from src.integrations.nte_core_protocol import NteCoreRpcError


class PerformanceTrace:
    def __init__(self, control):
        self.control = control
        self.lock = RLock()
        self.cancel = Event()
        self.thread = None
        self.key = None
        self.allowed = False
        self.directory = None
        self.value = {'state': 'off', 'summaries': []}

    def configure(self, key, allowed, directory):
        with self.lock:
            if self.key != key or not allowed:
                self.cancel.set()
            if self.key != key:
                self.value = {'state': 'off', 'summaries': []}
            self.key, self.allowed, self.directory = key, allowed, directory

    def snapshot(self):
        with self.lock:
            return {**self.value, 'allowed': self.allowed,
                    'running': bool(self.thread and self.thread.is_alive())}

    def start(self, services):
        with self.lock:
            if not self.allowed or self.control is None:
                raise PermissionError('현재 모드 또는 컴포넌트에서 세부 성능 샘플링을 허용하지 않습니다')
            if self.thread and self.thread.is_alive():
                return
            if type(services) is not int or not 1 <= services <= 127:
                raise ValueError('샘플링 서비스를 하나 이상 선택하세요')
            self.cancel = Event()
            self.value = {'state': 'starting', 'summaries': []}
            self.thread = Thread(target=self._run, args=(self.key, self.directory, services, self.cancel),
                                 name='performance-trace', daemon=True)
            self.thread.start()

    def stop(self):
        self.cancel.set()

    def wait(self, timeout=6):
        thread = self.thread
        if thread:
            thread.join(timeout)
        return not thread or not thread.is_alive()

    def close(self):
        self.stop()
        self.wait()

    def _run(self, key, directory, services, cancel):
        trace_id = uuid4().hex
        journal = None
        started = False
        start_confirmed = False
        terminal = False
        failure = None
        deadline = monotonic() + 125
        def call(action):
            return validate_trace(self.control({'action': action, 'trace_id': trace_id,
                                                 'services': services}, key), trace_id)
        try:
            with self.lock:
                if self.key != key or cancel.is_set():
                    return
                journal = TraceJournal(directory, trace_id, services)
                self.value['log_path'] = str(journal.path)
            started = True  # A timeout has unknown outcome; teardown still requests stop.
            result = call('start')
            start_confirmed = True
            while True:
                with self.lock:
                    if self.key != key or cancel.is_set():
                        break
                    journal.sample(result)
                    self.value = {**result, 'state': 'collecting' if result['active'] else
                                  'failed' if result['failed'] or not result['complete'] else 'saved',
                                  'log_path': str(journal.path)}
                if not result['active']:
                    terminal = True
                    break
                if monotonic() >= deadline:
                    cancel.set()
                cancel.wait(.5)
                if not cancel.is_set():
                    result = call('status')
        except NotImplementedError as error:
            started = False
            with self.lock:
                if self.key == key:
                    self.value.update(state='failed', error=str(error))
        except Exception as error:
            failure = str(error)
            if not start_confirmed and isinstance(error, NteCoreRpcError) and error.message in {
                'performance_plugin_unavailable', 'performance_busy', 'performance_start_failed',
                'invalid_trace_id', 'invalid_action', 'invalid_services',
            }:
                # A rejected start owns no remote collection to stop.
                started = False
            with self.lock:
                if self.key == key:
                    self.value.update(state='failed', error=failure)
        finally:
            if started and not terminal:
                try:
                    result = call('stop')
                    # Stop acknowledgment only requests draining; wait for the terminal receipt.
                    end = monotonic() + 3
                    while result['active'] and monotonic() < end:
                        Event().wait(.1)
                        result = call('status')
                    with self.lock:
                        if self.key == key:
                            if journal:
                                journal.sample(result)
                            self.value.update(result)
                            self.value['state'] = ('unconfirmed' if result['active'] else
                                                   'saved' if result['complete'] and failure is None else 'failed')
                            if journal:
                                self.value['log_path'] = str(journal.path)
                except Exception as stop_error:
                    with self.lock:
                        if self.key == key:
                            detail = f'중지 미확인: {stop_error}; 리스 갱신을 멈춘 뒤 컴포넌트 리스가 마무리합니다'
                            self.value.update(state='unconfirmed', error=f'{failure}\n{detail}' if failure else detail)
            if journal:
                journal.close()
