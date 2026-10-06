# 通过核对过的采集 Core 控制常驻宿主，超时只报结果未知，不重发变更。
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import time

from src.integrations.native_plugin_bundle import HOT_PLUGIN_LAYOUTS, inspect_native_plugin_bundle


class NativeHostError(RuntimeError):
    pass


class NativeHostOutcomeUnknown(NativeHostError):
    pass


class NativeHostClient:
    def __init__(self, application_root: Path, game_pid: int):
        bundle = inspect_native_plugin_bundle(application_root)
        if not bundle.ready or bundle.layout not in HOT_PLUGIN_LAYOUTS:
            raise NativeHostError('현재 컴포넌트 패키지는 아직 제한된 플러그인 호스트를 제공하지 않습니다.')
        if type(game_pid) is not int or game_pid <= 0:
            raise NativeHostError('이번 게임 프로세스 식별 정보가 없습니다.')
        self.executable = application_root / bundle.roles['core']
        self.digest = bundle.files[bundle.roles['core']]
        self.pid = game_pid
        self.session = None

    def call(self, action: str, plugin: str = ''):
        with self.executable.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != self.digest:
                raise NativeHostError('수집 Core가 이번 작업 중에 변경되었습니다.')
        command = [str(self.executable), 'native-host', str(self.pid), action]
        if plugin or self.session is not None:
            command.append(plugin)
        if self.session is not None:
            command.append(str(self.session))
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                    timeout=9, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as error:
            raise NativeHostOutcomeUnknown('호스트 요청 시간 초과로 결과를 알 수 없습니다; 후속 작업을 중지하며 자동으로 재전송하지 않습니다.') from error
        if result.returncode:
            # A failed transport can follow a dispatched mutation; preserve uncertainty.
            raise NativeHostOutcomeUnknown('호스트 제어 미완료: ' + result.stderr.strip()[:500])
        try:
            envelope = json.loads(result.stdout)
            created = envelope['hostCreated']
            if type(created) is not int or created <= 0 or (self.session is not None and self.session != created):
                raise ValueError('host generation mismatch')
            self.session = created
            return envelope['result']
        except (ValueError, KeyError, TypeError) as error:
            raise NativeHostOutcomeUnknown('호스트 응답 형식이 올바르지 않아 작업 결과를 확인할 수 없습니다.') from error

    def change(self, action: str, plugin: str, *, timeout: float = 15):
        accepted = self.call(action, plugin)
        operation_id = accepted.get('operationId')
        if type(operation_id) is not int or operation_id <= 0:
            raise NativeHostError('호스트가 플러그인 작업을 수락하지 않았습니다.')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.call('operation')
            if status.get('operationId') != operation_id:
                raise NativeHostOutcomeUnknown('플러그인 작업 ID가 바뀌었습니다. 다른 작업을 이번 작업의 성공으로 간주할 수 없습니다.')
            if status.get('state') == 'completed':
                return status
            if status.get('state') == 'failed':
                raise NativeHostError('플러그인 작업 실패: ' + str(status.get('error', '')))
            if status.get('state') not in {'queued', 'pending'}:
                raise NativeHostOutcomeUnknown('플러그인 작업 상태를 알 수 없습니다.')
            time.sleep(.1)
        raise NativeHostOutcomeUnknown('플러그인이 아직 남은 작업을 비우는 중이라 언로드가 확인되지 않았습니다; 후속 작업을 중지합니다.')
