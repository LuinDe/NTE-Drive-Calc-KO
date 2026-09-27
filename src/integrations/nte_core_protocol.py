# 定义 nte-core 错误类型、领域错误码和库存协议校验。
"""Protocol types and inventory DTO validation for nte-core."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


JsonObject = dict[str, Any]
MODS_PLUGIN_UNAVAILABLE_CODES = frozenset(
    {"MODS_PLUGIN_UNAVAILABLE", "EQUIPMENT_PLUGIN_UNAVAILABLE"}
)
MODS_PLUGIN_BUSY_CODES = frozenset(
    {"MODS_PLUGIN_BUSY", "EQUIPMENT_PLUGIN_BUSY"}
)


class NteCoreError(RuntimeError):
    """Base nte-core integration error."""


class NteCoreNotFoundError(NteCoreError):
    """Raised when nte-core.exe cannot be resolved."""


class NteCoreProcessError(NteCoreError):
    def __init__(
        self,
        message: str,
        *,
        return_code: int | None = None,
        stderr_lines: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.return_code = return_code
        self.stderr_lines = tuple(stderr_lines)


class NteCoreProtocolError(NteCoreError):
    """Raised when stdout violates the JSON-RPC/NDJSON contract."""


class NteCoreTimeoutError(NteCoreError):
    def __init__(self, method: str, timeout: float) -> None:
        super().__init__(
            f"nte-core request timed out: {method} ({timeout:.1f}s)"
        )
        self.method = method
        self.timeout = timeout


class NteCoreRpcError(NteCoreError):
    def __init__(self, error: Mapping[str, Any]) -> None:
        self.code = int(error.get("code", -32603))
        self.message = str(error.get("message", "Core error"))
        data = error.get("data")
        self.data = dict(data) if isinstance(data, Mapping) else {}
        domain_code = self.data.get("domain_code")
        self.domain_code = (
            str(domain_code) if domain_code is not None else None
        )
        suffix = f" [{self.domain_code}]" if self.domain_code else ""
        super().__init__(
            f"nte-core RPC error {self.code}{suffix}: {self.message}"
        )


def nte_core_error_has_domain_code(
    error: object, codes: frozenset[str]
) -> bool:
    domain_code = getattr(error, "domain_code", None)
    if isinstance(domain_code, str):
        return domain_code in codes
    message = str(error)
    return any(f"[{code}]" in message for code in codes)


def is_native_capture_not_ready(error: object) -> bool:
    """Identify an explicit start rejection; its reason determines retryability."""
    return (
        isinstance(error, NteCoreRpcError)
        and error.code == -32001
        and error.message == "not_ready"
    )


NATIVE_CAPTURE_TRANSIENT_REASONS = frozenset({
    "sdk_initializing", "hook_initializing", "game_thread_pending", "world_unavailable",
    "controller_unavailable", "pawn_unavailable", "scene_transition",
})


def native_capture_readiness_message(error: NteCoreRpcError) -> str:
    """Describe only declared readiness facts, without exposing arbitrary provider text."""
    messages = {
        "sdk_initializing": "수집 DLL이 게임 데이터 인터페이스를 초기화하는 중입니다",
        "hook_initializing": "수집 DLL이 히트별 Hook을 설치하는 중입니다",
        "game_thread_pending": "수집 DLL이 아직 게임 메인 스레드 콜백을 인식하지 못했습니다",
        "world_unavailable": "수집 DLL이 아직 유효한 게임 월드를 읽지 못했습니다",
        "controller_unavailable": "수집 DLL이 아직 로컬 캐릭터 컨트롤러를 읽지 못했습니다",
        "pawn_unavailable": "수집 DLL이 아직 조작 가능한 캐릭터를 읽지 못했습니다",
        "scene_transition": "수집 DLL이 새 씬 준비를 기다리는 중입니다",
        "sdk_unavailable": "수집 DLL이 게임 데이터 인터페이스를 초기화할 수 없습니다. 현재 게임 버전과 SDK를 점검해야 합니다",
        "hook_unavailable": "수집 DLL이 히트별 Hook을 설치할 수 없습니다. 수집 컴포넌트를 확인해야 합니다",
        "provider_stopping": "수집 DLL이 중지되는 중이라 수집을 시작할 수 없습니다",
    }
    reason = error.data.get("reason")
    return messages.get(reason if isinstance(reason, str) else "",
                        "수집 DLL이 준비되지 않았고 식별 가능한 원인도 제공되지 않았습니다. 컴포넌트 버전을 점검하고 게임을 재시작하세요")


def translate_native_start_error(error: BaseException, stderr_lines: Sequence[str]) -> BaseException:
    """Translate known startup failures after the process streams have drained."""
    if any(line.strip() == 'error: native capture native_resources_unavailable' for line in stderr_lines):
        return NteCoreProcessError('수집 Core에 필수 내장 리소스가 없습니다. 완전한 수집 컴포넌트로 업데이트하세요.')
    if any(line.strip() == 'error: native capture native_context_capability_required_restart_game'
           for line in stderr_lines):
        return NteCoreProcessError('게임 내 수집 DLL 버전이 너무 오래되었습니다. 수집 컴포넌트를 업데이트하고 게임을 재시작하세요.')
    if any(line.strip() == 'error: native capture peer_identity_mismatch' for line in stderr_lines):
        return NteCoreProcessError('Core와 게임의 Windows 로그인 계정 또는 권한이 일치하지 않습니다. 게임과 같은 사용자와 권한으로 Calc를 실행하세요.')
    return error


def is_mods_plugin_unavailable_error(error: object) -> bool:
    return nte_core_error_has_domain_code(
        error, MODS_PLUGIN_UNAVAILABLE_CODES
    )


def is_mods_plugin_busy_error(error: object) -> bool:
    return nte_core_error_has_domain_code(error, MODS_PLUGIN_BUSY_CODES)


def equipment_request_failure_kind(error: object) -> str:
    """Return a stable UI/report category without discarding the original error."""

    if is_mods_plugin_busy_error(error):
        return "plugin_busy"
    if is_mods_plugin_unavailable_error(error):
        return "plugin_unavailable"
    if isinstance(error, NteCoreTimeoutError):
        return "core_request_timeout"
    if (getattr(error, "domain_code", None) == "EQUIPMENT_OUTCOME_UNKNOWN"
            or (isinstance(error, NteCoreRpcError) and error.code == -32001
                and error.message == "control_timeout")):
        return "outcome_unknown"
    if getattr(error, "domain_code", None) == "EQUIPMENT_REQUEST_REJECTED":
        return "request_rejected"
    return "apply_error"


def inventory_item_placement(
    item: Mapping[str, Any],
) -> tuple[int, int] | None:
    placement = item.get("equipped_placement")
    if placement is None:
        return None
    if not isinstance(placement, Mapping):
        raise NteCoreProtocolError(
            "inventory equipped_placement must be an object or null"
        )
    row = placement.get("row")
    column = placement.get("column")
    if (
        isinstance(row, bool)
        or not isinstance(row, int)
        or isinstance(column, bool)
        or not isinstance(column, int)
        or not 1 <= row <= 5
        or not 1 <= column <= 5
    ):
        raise NteCoreProtocolError(
            "inventory equipped_placement row and column "
            "must be integers in 1..5"
        )
    return row, column


def group_inventory_items_by_character(
    snapshot: Mapping[str, Any],
) -> dict[int, list[JsonObject]]:
    items = snapshot.get("items")
    if not isinstance(items, list):
        raise NteCoreProtocolError("inventory snapshot items must be an array")
    grouped: dict[int, list[JsonObject]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise NteCoreProtocolError(
                "inventory snapshot item must be an object"
            )
        inventory_item_placement(item)
        character_id = item.get("equipped_character_id")
        if character_id is None:
            continue
        if (
            isinstance(character_id, bool)
            or not isinstance(character_id, int)
            or character_id <= 0
        ):
            raise NteCoreProtocolError(
                "inventory equipped_character_id must be "
                "a positive integer or null"
            )
        grouped.setdefault(character_id, []).append(dict(item))
    return grouped
