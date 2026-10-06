# 仅为明确的原生连接中断安排有限自动恢复，不恢复主动停止或未知故障。
from time import monotonic


class SyncRecovery:
    delays = (2.0, 5.0, 15.0)

    def __init__(self, clock=monotonic):
        self.clock = clock
        self.reset()

    def reset(self):
        self.attempts = 0
        self.service = None
        self.due = None

    @property
    def waiting(self):
        return self.due is not None

    def ready(self, service):
        state = getattr(service, 'state', None)
        if (service is None or service.is_running or state is None
                or state.phase not in {'stopped', 'error'}
                or getattr(state, 'stop_reason', None) != 'connection_lost'):
            return False
        if service is not self.service:
            self.service = service
            self.due = (self.clock() + self.delays[self.attempts]
                        if self.attempts < len(self.delays) else None)
        if self.due is None or self.clock() < self.due:
            return False
        self.due = None
        self.attempts += 1
        return True
