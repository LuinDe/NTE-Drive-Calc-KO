# 将检测异常转换为可复制的固定诊断，避免泄露外部响应、账号数据和本机路径。
from __future__ import annotations

from concurrent.futures import CancelledError
import math
from pathlib import Path

from src.integrations.nte_core_protocol import (
    NATIVE_CAPTURE_TRANSIENT_REASONS, NteCoreNotFoundError, NteCoreProcessError,
    NteCoreProtocolError, NteCoreRpcError, NteCoreTimeoutError,
    native_capture_readiness_message,
)


_START_FAILURES = {
    'peer_identity_mismatch': (
        'Core와 게임의 Windows 로그인 계정 또는 권한이 일치하지 않습니다. 게임과 같은 사용자와 권한으로 Calc를 실행하세요.',
        'Calc와 게임이 같은 Windows 사용자, 동일한 권한 수준으로 실행되는지 확인한 후 다시 검사하세요.',
    ),
    'native_resources_unavailable': (
        '수집 Core에 필수 내장 리소스가 없습니다. 완전한 수집 컴포넌트로 업데이트하세요.',
        '현재 설치 패키지가 온전한지 확인하고, 같은 배포 패키지의 Core와 수집 DLL을 사용하세요.',
    ),
    'native_context_capability_required_restart_game': (
        '게임 내 수집 DLL 버전이 너무 오래되었습니다. 수집 컴포넌트를 업데이트하고 게임을 재시작하세요.',
        '먼저 구성 컴포넌트 버전을 확인하세요. 게임을 완전히 종료한 뒤 배포하고, 그다음 게임을 시작하세요. 같은 이전 패키지를 반복 배포해도 버전은 업데이트되지 않습니다.',
    ),
}
_METHODS = frozenset({
    'core.hello', 'core.status', 'equipment.status', 'native.snapshot.status',
    'native.snapshot.refresh', 'native.snapshot.page', 'native.snapshot.changes',
    'native.inventory.page', 'native.character.page',
})
_TRANSPORT_FAILURES = {
    'connect_timeout': '게임 내 수집 파이프라인 연결 시간이 초과되었습니다; 다른 도구가 연결을 점유하고 있지 않은지 점검하세요.',
    'pipe_open_failed': '게임 내 수집 파이프를 열 수 없습니다; 프로세스 권한과 연결 점유를 확인하세요.',
    'pipe_disconnected': '게임 내 수집 파이프 연결이 끊어져 이번 검사를 완료하지 못했습니다.',
    'pipe_io_failed': '게임 내 수집 파이프라인 읽기/쓰기에 실패했습니다.',
    'pipe_write_failed': '게임 내 수집 파이프라인에 요청을 쓰지 못했습니다.',
    'pipe_write_timeout': '게임 내 수집 파이프라인에 대한 요청 쓰기가 시간 초과되었습니다.',
    'pipe_server_mismatch': '파이프 서버가 대상 게임 프로세스와 일치하지 않아, 연결을 거부했습니다.',
    'handshake_rejected': '게임 내 수집 컴포넌트가 핸드셰이크 요청을 거부했습니다.',
    'unsupported_provider': '게임 내 수집 컴포넌트가 프로토콜 또는 프로세스 신원 검증을 통과하지 못했습니다.',
}
_PROCESS_MESSAGES = frozenset({
    '게임 프로세스 목록을 읽을 수 없습니다.', '게임 프로세스 목록 읽기에 실패했습니다.',
    '여러 게임 프로세스가 감지되었습니다. 하나만 남긴 후 강화 수집을 시작해 주세요.',
    '게임 프로세스 신원을 확인할 수 없어 이번 수집을 시작하지 않았습니다.',
    '게임 프로세스 생성 시간을 확인할 수 없어 이번 수집을 시작하지 않았습니다.',
    '강화 수집 컴포넌트 상태를 확인할 수 없어 이번 전투 수집을 시작하지 않았습니다.',
})
_COPY_HINT = '「검사 결과 복사」를 클릭해 개발자에게 보내 주세요; 이 안내만으로 재설치나 재시작을 반복할 필요는 없습니다.'


def _failure_location(error: Exception) -> str:
    """Only expose a repository-relative Python location, never an external path."""
    root = Path(__file__).resolve().parents[2]
    location = ''
    trace = error.__traceback__
    while trace is not None:
        try:
            relative = Path(trace.tb_frame.f_code.co_filename).resolve().relative_to(root)
            if relative.parts[0] == 'src' and relative.suffix == '.py':
                location = f'{relative.as_posix()}:{trace.tb_lineno}'
        except (ValueError, OSError):
            pass
        trace = trace.tb_next
    return location


def detection_failure_detail(error: Exception, *, record: bool = False) -> str:
    """Keep typed evidence and known reasons; never forward raw error text or stderr."""
    evidence = [f'예외 유형: {type(error).__name__}']
    location = _failure_location(error)
    if location:
        evidence.append(f'코드 위치: {location}')
    reason = '검사 중 식별되지 않은 예외가 발생했습니다. 연결, 컴포넌트, 데이터 중 어느 쪽 문제인지 아직 확정할 수 없습니다.'
    next_step = _COPY_HINT
    cause = error.__cause__ if isinstance(error.__cause__, OSError) else error
    winerror = getattr(cause, 'winerror', None)
    if isinstance(winerror, int):
        evidence.append(f'Windows 오류 코드: {winerror}')

    if isinstance(error, NteCoreProcessError):
        reason = '수집 Core 시작 또는 실행에 실패하여 이번 검사를 완료하지 못했습니다.'
        if isinstance(error.return_code, int):
            evidence.append(f'Core 종료 코드: {error.return_code}')
        if str(error) in _PROCESS_MESSAGES:
            reason = str(error)
        for code, (message, action) in _START_FAILURES.items():
            if str(error) == message or any(
                line.strip() == f'error: native capture {code}' for line in error.stderr_lines
            ):
                reason, next_step = message, action
                evidence.append(f'원인 코드: {code}')
                break
        else:
            for code, message in _TRANSPORT_FAILURES.items():
                if any(line.strip() == f'error: native capture {code}' for line in error.stderr_lines):
                    reason = message
                    evidence.append(f'원인 코드: {code}')
                    break
    elif isinstance(error, NteCoreTimeoutError):
        reason = 'Core 응답 수집을 기다리다 시간 초과되어 이번 검사는 완료되지 않았습니다; 시간 초과 자체가 컴포넌트가 로드되지 않았음을 증명하지는 않습니다.'
        method = error.method if error.method in _METHODS else '인식되지 않은 인터페이스'
        evidence.append(f'시간 초과 인터페이스: {method}')
        if isinstance(error.timeout, (int, float)) and math.isfinite(error.timeout):
            evidence.append(f'대기 상한: {error.timeout:g}초')
        next_step = '캐릭터를 조작할 수 있는 장면에 진입했는지 확인하고, 다른 도구의 수집 연결을 종료하세요; 그래도 실패하면, ' + _COPY_HINT
    elif isinstance(error, NteCoreRpcError):
        reason = '수집 Core가 비즈니스 오류를 반환하여 이번 검사가 완료되지 않았습니다.'
        evidence.append(f'RPC 오류 코드: {error.code}')
        known_reason = error.data.get('reason')
        if isinstance(known_reason, str) and known_reason in (
            NATIVE_CAPTURE_TRANSIENT_REASONS | {'sdk_unavailable', 'hook_unavailable', 'provider_stopping'}
        ):
            reason = native_capture_readiness_message(error)
            evidence.append(f'원인 코드: {known_reason}')
        elif error.domain_code in {'MODS_PLUGIN_BUSY', 'EQUIPMENT_PLUGIN_BUSY'}:
            reason = '게임 내 컴포넌트가 다른 요청을 처리하고 있습니다.'
            next_step = '다른 도구의 수집이나 조작 작업을 종료한 후 다시 검사하세요; 그래도 실패하면,' + _COPY_HINT
            evidence.append(f'원인 코드: {error.domain_code}')
        elif error.code == -32601:
            reason = '수집 Core가 이번 검사 인터페이스를 지원하지 않습니다. Calc, Core, DLL이 서로 매칭되는지 점검해야 합니다.'
    elif isinstance(error, NteCoreNotFoundError):
        reason = '사용 가능한 수집 Core 실행 파일을 찾지 못했습니다.'
        next_step = 'Calc 설치가 온전한지, 그리고 보안 소프트웨어가 nte-core.exe를 격리하지 않았는지 확인하세요.'
    elif isinstance(error, NteCoreProtocolError):
        reason = '수집 응답이 프로토콜 또는 데이터 형식 검증을 통과하지 못해 유효한 비즈니스 결과로 사용할 수 없습니다.'
        next_step = 'Calc, Core, DLL이 같은 배포 패키지에서 함께 제공된 것인지 확인하고, 현재 게임 버전을 지원하는지 확인하세요;' + _COPY_HINT
    elif isinstance(error, CancelledError):
        reason = '검사가 취소되어 이번에는 완전한 결과가 없습니다.'
        next_step = '현재 작업이 끝나기를 기다린 후 다시 검사하세요.'
    elif isinstance(error, PermissionError):
        reason = '검사에 필요한 권한 또는 접근 권한이 거부되었습니다.'
        next_step = '확인된 작업 모드, 일시 정지 상태, 그리고 Calc와 게임의 실행 권한을 확인하세요.'
    if winerror == 5 or (isinstance(cause, OSError) and cause.errno == 13):
        reason = 'Windows가 검사에 필요한 프로세스, 파일 또는 연결 접근을 거부했습니다.'
        next_step = 'Calc와 게임의 Windows 사용자 및 권한, 그리고 파일 접근 권한을 확인하세요.'

    detail = f'원인: {reason}\n다음 단계: {next_step}\n진단:' + '；'.join(evidence)
    if record:
        from src.utils.logger import logger
        logger.warning('environment.detection_failed | {}', detail.replace('\n', ' | '))
    return detail
