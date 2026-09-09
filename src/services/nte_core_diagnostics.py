# 收集并格式化可独立查看的 nte-core 抓包诊断报告。
"""Collect a compact, actionable nte-core capture diagnostic report."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from src.integrations.nte_core import (
    NteCoreClient,
    NteCoreError,
    NteCoreRpcError,
    resolve_nte_core_executable,
)
from src.integrations.windows_capture_diagnostics import collect_windows_capture_support


DiagnosticResult = dict[str, Any]


def collect_nte_core_diagnostics(
    *,
    cwd: str | Path | None = None,
    client_factory: Callable[[Path], NteCoreClient] | None = None,
) -> DiagnosticResult:
    """Inspect the bundled core and Windows capture prerequisites without capture.

    ``capture.detect`` is the sole core RPC. Windows support facts are read-only and
    contain no addresses, MACs, endpoints or network-configuration changes.
    """

    result: DiagnosticResult = {"ok": False}
    client: NteCoreClient | None = None
    try:
        executable = resolve_nte_core_executable()
        client = (
            client_factory(executable)
            if client_factory is not None
            else NteCoreClient(executable=executable, cwd=cwd, timeout=10.0)
        )
        client.start()
        hello = client.hello_result or {}
        result["core"] = {
            "version": hello.get("core_version"),
            "protocol_version": hello.get("protocol_version"),
        }
        result["windows_capture_support"] = collect_windows_capture_support(
            core_executable=executable
        )
        detected = client.detect_capture_environment()
        if not isinstance(detected, Mapping):
            raise TypeError("capture.detect가 잘못된 결과를 반환했습니다")
        result["capture_detect"] = dict(detected)
        result["ok"] = True
    except NteCoreError as exc:
        result["error"] = str(exc)
        result["error_type"] = type(exc).__name__
        if isinstance(exc, NteCoreRpcError):
            result["domain_code"] = exc.domain_code
            result["rpc_code"] = exc.code
            result["rpc_data"] = _compact_rpc_data(exc.data)
        _copy_recent_stderr(result, client)
    except Exception as exc:
        result["error"] = str(exc)
        result["error_type"] = type(exc).__name__
        _copy_recent_stderr(result, client)
    finally:
        if client is not None:
            client.close()
    return result


def format_nte_core_diagnostics(result: Mapping[str, Any]) -> str:
    """Produce a copyable support report containing only actionable capture facts."""

    lines = ["NTE Drive Calc · nte-core 패킷 캡처 진단"]
    core = result.get("core")
    if isinstance(core, Mapping):
        lines.append(
            "코어:"
            f"v{core.get('version', '未知')} (프로토콜 {core.get('protocol_version', '未知')})"
        )

    detected = result.get("capture_detect")
    if not isinstance(detected, Mapping):
        lines.extend(_format_core_error(result))
        return "\n".join(lines)

    support = result.get("windows_capture_support")
    lines.extend(_format_capture_summary(detected))
    if isinstance(support, Mapping):
        lines.extend(_format_windows_support(support))
    lines.extend(_format_next_step(detected, support if isinstance(support, Mapping) else {}))
    return "\n".join(lines)


def capture_device_names(detected: Mapping[str, Any]) -> list[str]:
    """Return unique capture device names that nte-core can accept manually."""

    devices = detected.get("devices")
    if not isinstance(devices, list):
        return []

    names: list[str] = []
    seen: set[str] = set()
    for device in devices:
        name = device if isinstance(device, str) else device.get("name") if isinstance(device, Mapping) else None
        if not isinstance(name, str):
            continue
        name = name.strip()
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return names


def _copy_recent_stderr(result: DiagnosticResult, client: NteCoreClient | None) -> None:
    if client is not None and client.recent_stderr:
        result["stderr"] = list(client.recent_stderr[-3:])


def _format_capture_summary(detected: Mapping[str, Any]) -> list[str]:
    devices = capture_device_names(detected)
    lines = [
        "",
        "패킷 캡처 열거",
        f"게임 프로세스: {_yes_no_unknown(detected.get('game_process_detected'))}",
        f"게임 네트워크 연결: {_yes_no_unknown(detected.get('local_ip_detected'))}",
        f"Npcap 열거 장치: {len(devices)}개",
        f"자동 선택: {_format_compact_value(detected.get('recommended_device'))}",
    ]
    if devices:
        lines.append("선택 가능한 네트워크 어댑터를 발견했습니다. 기본적으로 nte-core가 자동 선택합니다.")
    else:
        lines.extend(
            [
                "코어 열거 오류 상세: 프로토콜이 제공하지 않음 (코어가 장치 0개만 반환).",
                "어댑터별 필터 원인: 프로토콜이 제공하지 않음.",
            ]
        )
    return lines


def _format_windows_support(support: Mapping[str, Any]) -> list[str]:
    if support.get("supported") is False:
        return ["", "Windows 패킷 캡처 환경", str(support.get("reason", "현재 시스템은 Windows 탐지를 제공하지 않습니다"))]
    installation = support.get("npcap_installation")
    service = support.get("driver_service")
    adapters = support.get("network_adapters")
    libraries = support.get("runtime_libraries")
    lines = ["", "Windows 패킷 캡처 환경"]
    if isinstance(installation, Mapping):
        installed = bool(installation.get("directory_present"))
        tool_ready = bool(installation.get("installer_tool_present"))
        lines.append("Npcap 설치:" + ("감지됨" if installed and tool_ready else "디렉터리 또는 드라이버 도구가 불완전함"))
        if installation.get("driver_log_present"):
            lines.append("Npcap 드라이버 로그: 사용 가능 (NPFInstall.log)")
    if isinstance(service, Mapping):
        lines.append("Npcap 드라이버 서비스:" + _service_text(service))
    if isinstance(adapters, Mapping):
        lines.extend(_adapter_lines(adapters))
    if isinstance(libraries, list):
        lines.append("패킷 캡처 DLL 후보:" + _library_text(libraries))
    return lines


def _format_next_step(detected: Mapping[str, Any], support: Mapping[str, Any]) -> list[str]:
    devices = capture_device_names(detected)
    if devices:
        if detected.get("recommended_device") is None:
            return [
                "",
                "다음 단계",
                "자동 추천을 받지 못했습니다. 우선 자동 선택을 유지한 채 가방 동기화를 다시 시작하세요. 계속 실패하면 고급 문제 해결을 사용하세요.",
            ]
        return ["", "다음 단계", "패킷 캡처 환경이 열거 조건을 충족합니다. 동기화가 계속 실패하면 동기화 단계 오류를 제출하세요."]

    installation = support.get("npcap_installation")
    service = support.get("driver_service")
    adapters = support.get("network_adapters")
    if isinstance(installation, Mapping) and not installation.get("installer_tool_present"):
        action = "Npcap 설치가 불완전합니다: 관리자 권한으로 Npcap을 복구 또는 재설치한 뒤 Windows를 재시작하세요."
    elif isinstance(service, Mapping) and service.get("state") == "missing":
        action = "Npcap 드라이버 서비스를 찾지 못했습니다: Npcap을 복구 또는 재설치한 뒤 Windows를 재시작하세요."
    elif isinstance(service, Mapping) and _service_state(service).casefold() != "running":
        action = "Npcap 드라이버가 실행 중이 아닙니다: 먼저 서비스 오류와 NPFInstall.log를 확인한 뒤 Npcap을 복구 또는 재설치하세요."
    elif isinstance(adapters, Mapping) and adapters.get("state") == "ok" and not adapters.get("active_count"):
        action = "Windows에서 활성화된 네트워크 어댑터를 감지하지 못했습니다: 실제로 연결된 Wi-Fi 또는 이더넷을 연결·활성화한 뒤 다시 시도하세요."
    else:
        action = (
            "Windows에 Npcap/어댑터 단서는 있지만 코어 열거가 0입니다: VPN, 가속기, 가상 어댑터, 엔드포인트 보호의 네트워크 필터 드라이버를 확인하고"
            "이 보고서와 NPFInstall.log를 제출하세요."
        )
    return ["", "다음 단계", action]


def _format_core_error(result: Mapping[str, Any]) -> list[str]:
    lines = [
        "",
        "코어 호출 실패",
        f"오류 범주: {result.get('error_type', '未知')}",
        f"오류 코드: {result.get('domain_code') or result.get('rpc_code') or '未提供'}",
        f"정보: {result.get('error', '未知')}",
    ]
    stderr = result.get("stderr")
    if isinstance(stderr, list) and stderr:
        lines.append("코어 출력:" + " | ".join(str(item) for item in stderr[-3:]))
    return lines


def _service_state(service: Mapping[str, Any]) -> str:
    details = service.get("details")
    return str(details.get("State") or "未知") if isinstance(details, Mapping) else "未知"


def _service_text(service: Mapping[str, Any]) -> str:
    if service.get("state") == "missing":
        return "발견되지 않음"
    if service.get("state") != "ok":
        return "조회 실패:" + str(service.get("error", "원인 미제공"))
    details = service.get("details")
    if not isinstance(details, Mapping):
        return "조회가 잘못된 결과를 반환함"
    return f"{_service_state(service)} (시작 방식 {details.get('StartMode', '未知')})"


def _adapter_lines(adapters: Mapping[str, Any]) -> list[str]:
    if adapters.get("state") != "ok":
        return ["Windows 네트워크 어댑터: 조회 실패:" + str(adapters.get("error", "원인 미제공"))]
    active_hardware = adapters.get("active_hardware_count", 0)
    active_total = adapters.get("active_count", 0)
    lines = [
        f"Windows 활성 실제 네트워크 어댑터: {active_hardware}개"
        f"(활성 어댑터 총 {active_total}개, 전체 {adapters.get('adapter_count', 0)}개)"
    ]
    items = adapters.get("adapters")
    if isinstance(items, list):
        for item in items:
            if (
                isinstance(item, Mapping)
                and item.get("hardware")
                and str(item.get("status", "")).casefold() == "up"
            ):
                lines.append(f"  - {item.get('name', '未知')}: {item.get('description', '未知')}")
    return lines


def _library_text(libraries: list[Any]) -> str:
    if not libraries:
        return "일반적인 위치에서 발견되지 않음"
    values = []
    for library in libraries:
        if isinstance(library, Mapping):
            values.append(f"{library.get('location')}\\{library.get('file')}#{library.get('sha256_prefix')}")
    return "；".join(values) if values else "조회 결과가 유효하지 않음"


def _yes_no_unknown(value: object) -> str:
    if value is True:
        return "감지됨"
    if value is False:
        return "감지되지 않음"
    return "코어가 제공하지 않음"


def _format_compact_value(value: object) -> str:
    if value is None:
        return "없음"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _compact_rpc_data(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in ("domain_code", "retryable") if key in value}
