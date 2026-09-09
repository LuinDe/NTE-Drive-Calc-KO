# 管理单个战斗抓包进程并发布不可变摘要。
"""Own one combat-profile nte-core process and publish immutable summaries."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from src.domain.battle_report import (
    BattleCaptureState,
    BattleSummary,
    BattleSummaryPersistenceOutcome,
    EMPTY_BATTLE_CAPTURE_STATE,
)
from src.integrations.nte_core_battle import (
    parse_battle_axis,
    parse_battle_record,
    parse_battle_summary,
    parse_battle_summary_event,
)
from src.integrations.nte_core import nte_core_error_has_domain_code
from src.observability import OperationContext
from src.observability.operation import log_event
from src.observability.redaction import safe_exception
from src.services.raw_capture_retention import prune_raw_capture_files


BattleStateHandler = Callable[[BattleCaptureState], None]


class BattleCoreClient(Protocol):
    def start(self) -> Any: ...

    def add_event_handler(
        self,
        method: str | None,
        handler: Callable[[dict[str, Any]], None],
    ) -> None: ...

    def remove_event_handler(
        self,
        method: str | None,
        handler: Callable[[dict[str, Any]], None],
    ) -> None: ...

    def start_capture(
        self,
        *,
        profile: Literal["inventory", "combat"],
        device_name: str | None = None,
        include_incoming: bool = True,
        server_damage_calibration: bool = True,
        raw_capture: Literal["enabled", "disabled"] = "disabled",
    ) -> Mapping[str, Any]: ...

    def stop_capture(self) -> Mapping[str, Any]: ...

    def get_battle_summary(
        self, *, subtract_time_stop: bool = True
    ) -> Mapping[str, Any] | None: ...

    def get_battle_record(
        self,
        *,
        battle_record_id: str | None = None,
        subtract_time_stop: bool = True,
    ) -> Mapping[str, Any] | None: ...

    def get_battle_axis(
        self,
        *,
        battle_record_id: str,
        cursor: str | None = None,
        limit: int = 500,
    ) -> Mapping[str, Any] | None: ...

    def close(self) -> None: ...


BattleClientFactory = Callable[[], BattleCoreClient]


class BattleSummaryWriter(Protocol):
    def begin_capture(
        self,
        *,
        capture_operation_id: str,
        captured_at_utc: str,
    ) -> None: ...

    def append_axis_page(
        self,
        *,
        capture_operation_id: str,
        page: Mapping[str, Any],
    ) -> None: ...

    def replace_axis_pages(
        self,
        *,
        capture_operation_id: str,
        pages: Sequence[Mapping[str, Any]],
        source_generation: str,
        incomplete_reason: str | None = None,
    ) -> None: ...

    def discard_capture(self, *, capture_operation_id: str) -> None: ...

    def finalize_summary(
        self,
        *,
        raw_summary_payload: Mapping[str, Any],
        summary: BattleSummary,
        capture_operation_id: str,
        captured_at_utc: str,
        finalized_at_utc: str,
        raw_record_payload: Mapping[str, Any] | None = None,
        nte_core_provenance: Mapping[str, Any] | None = None,
    ) -> BattleSummaryPersistenceOutcome: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _freeze_nte_core_provenance(client: BattleCoreClient) -> dict[str, Any]:
    hello = getattr(client, "hello_result", None)
    hello_payload = dict(hello) if isinstance(hello, Mapping) else {}
    executable_sha256 = str(
        getattr(client, "executable_sha256", None) or ""
    ).strip()
    return {
        "core_version": (
            str(hello_payload.get("core_version") or "").strip() or None
        ),
        "protocol_version": hello_payload.get("protocol_version"),
        "data_version": (
            str(hello_payload.get("data_version") or "").strip() or None
        ),
        "executable_sha256": executable_sha256 or None,
    }


_BATTLE_READ_CONTRACT_VERSION = 5


class BattleCaptureService:
    """Qt-free lifecycle wrapper for a single live battle report session."""

    def __init__(
        self,
        *,
        client_factory: BattleClientFactory,
        operation_context: OperationContext,
        device_name: str | None = None,
        summary_writer: BattleSummaryWriter | None = None,
        raw_capture_enabled: bool = False,
        raw_capture_directory: str | Path | None = None,
        stop_timeout_seconds: float = 12.0,
    ) -> None:
        if raw_capture_enabled and raw_capture_directory is None:
            raise ValueError("전투 리포트 원본 패킷 캡처를 활성화하려면 계정 패킷 캡처 디렉터리를 제공해야 합니다")
        if stop_timeout_seconds <= 0:
            raise ValueError("전투 리포트 중지 시간 초과는 0보다 커야 합니다")
        self._client_factory = client_factory
        self._operation_context = operation_context
        self._device_name = device_name
        self._summary_writer = summary_writer
        self._raw_capture_enabled = bool(raw_capture_enabled)
        self._raw_capture_directory = (
            Path(raw_capture_directory).expanduser().resolve()
            if raw_capture_directory is not None
            else None
        )
        self._stop_timeout_seconds = float(stop_timeout_seconds)
        self._stop_event = threading.Event()
        self._summary_event = threading.Event()
        self._lock = threading.RLock()
        self._handlers: list[BattleStateHandler] = []
        self._thread: threading.Thread | None = None
        self._client: BattleCoreClient | None = None
        self._state = EMPTY_BATTLE_CAPTURE_STATE
        self._latest_summary: BattleSummary | None = None
        self._last_sequence = -1
        self._event_error: Exception | None = None
        self._source_battle_record_id: str | None = None
        self._axis_cursor: str | None = None
        self._discard_requested = False

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def state(self) -> BattleCaptureState:
        with self._lock:
            return self._state

    def add_state_handler(self, handler: BattleStateHandler) -> None:
        with self._lock:
            if handler not in self._handlers:
                self._handlers.append(handler)

    def remove_state_handler(self, handler: BattleStateHandler) -> None:
        with self._lock:
            if handler in self._handlers:
                self._handlers.remove(handler)

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._summary_event.clear()
        with self._lock:
            self._discard_requested = False
        self._publish("starting", "nte-core 전투 수집을 시작하는 중……", running=True)
        self._thread = threading.Thread(
            target=self._run,
            name="battle-capture-service",
            daemon=True,
        )
        self._thread.start()

    def request_stop(self) -> None:
        if not self.is_running:
            return
        with self._lock:
            if self._discard_requested:
                return
        self._publish(
            "stopping",
            "수집을 중지하고 최종 전투 리포트를 읽는 중……",
            running=True,
            summary=self._latest_summary,
        )
        self._stop_event.set()

    def request_discard(self) -> None:
        """Stop this capture and delete its staged rows without finalizing it."""

        if not self.is_running:
            return
        with self._lock:
            self._discard_requested = True
        self._publish(
            "stopping",
            "현재 전투 리포트를 포기하고 다시 수집할 준비를 하는 중……",
            running=True,
            summary=self._latest_summary,
        )
        self._stop_event.set()

    def close(self, *, timeout: float = 12.0) -> None:
        self.request_stop()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def _run(self) -> None:
        captured_at_utc = _utc_now()
        log_event(
            "INFO",
            "battle_report.capture_started",
            "전투 리포트 수집 시작",
            self._operation_context,
            phase="started",
        )
        client: BattleCoreClient | None = None
        capture_started = False
        terminal_error: Exception | None = None
        persistence_outcome: BattleSummaryPersistenceOutcome | None = None
        final_payload_received = False
        capture_staged = False
        capture_finalized = False
        empty_capture_discarded = False
        final_payload: Mapping[str, Any] | None = None
        final_record: dict[str, Any] | None = None
        nte_core_provenance: dict[str, Any] | None = None
        try:
            if self._raw_capture_enabled:
                assert self._raw_capture_directory is not None
                self._raw_capture_directory.mkdir(parents=True, exist_ok=True)
                self._prune_raw_captures()
            if self._summary_writer is not None:
                self._summary_writer.begin_capture(
                    capture_operation_id=self._operation_context.operation_id,
                    captured_at_utc=captured_at_utc,
                )
                capture_staged = True
            client = self._client_factory()
            self._client = client
            client.start()
            nte_core_provenance = _freeze_nte_core_provenance(client)
            client.add_event_handler("event.battle.summary", self._on_summary_event)
            client.start_capture(
                profile="combat",
                device_name=self._device_name,
                include_incoming=True,
                server_damage_calibration=True,
                raw_capture=(
                    "enabled" if self._raw_capture_enabled else "disabled"
                ),
            )
            capture_started = True
            if not self._stop_event.is_set():
                self._publish(
                    "running",
                    "수집 중: 전투에 들어가면 팀 피해가 실시간으로 표시됩니다.",
                    running=True,
                )
            while not self._stop_event.wait(0.5):
                self._poll_axis(client, maximum_pages=8)
            self._stop_client_capture(client)
            capture_started = False
            if (
                not self._discard_was_requested()
                and self._event_error is None
                and not self._has_observed_battle_evidence()
            ):
                self._summary_event.wait(0.25)
            if self._discard_was_requested():
                pass
            elif self._event_error is not None:
                raise self._event_error
            elif not self._has_observed_battle_evidence():
                empty_capture_discarded = True
            else:
                final_record = self._read_final_axis(client)
                final_payload = (
                    final_record.get("summary")
                    if final_record is not None
                    else client.get_battle_summary(subtract_time_stop=True)
                )
            if final_payload is not None:
                final_payload_received = True
                self._latest_summary = parse_battle_summary(
                    final_payload,
                    sequence=max(0, self._last_sequence + 1),
                )
                if self._summary_writer is not None:
                    persistence_outcome = self._summary_writer.finalize_summary(
                        raw_summary_payload=final_payload,
                        summary=self._latest_summary,
                        capture_operation_id=self._operation_context.operation_id,
                        captured_at_utc=captured_at_utc,
                        finalized_at_utc=_utc_now(),
                        raw_record_payload=final_record,
                        nte_core_provenance=nte_core_provenance,
                    )
                    capture_finalized = True
        except Exception as error:
            terminal_error = error
        finally:
            if client is not None:
                try:
                    client.remove_event_handler(
                        "event.battle.summary", self._on_summary_event
                    )
                    if capture_started:
                        self._stop_client_capture(client)
                except Exception:
                    pass
                try:
                    client.close()
                except Exception as close_error:
                    if terminal_error is None:
                        terminal_error = close_error
            if self._raw_capture_enabled:
                self._prune_raw_captures()
            self._client = None
            if (
                capture_staged
                and not capture_finalized
                and self._summary_writer is not None
            ):
                try:
                    self._summary_writer.discard_capture(
                        capture_operation_id=self._operation_context.operation_id
                    )
                except Exception as discard_error:
                    if terminal_error is None:
                        terminal_error = discard_error
        summary = self._latest_summary
        discarded = self._discard_was_requested()
        if terminal_error is None and discarded:
            self._publish(
                "stopped",
                "현재 전투 리포트를 포기했으며 수집을 다시 시작하는 중입니다.",
                running=False,
                persistence_status="discarded_restart",
            )
            log_event(
                "INFO",
                "battle_report.capture_discarded",
                "현재 전투 리포트를 포기했습니다",
                self._operation_context,
                phase="discarded",
                result="discarded",
            )
        elif terminal_error is None:
            persistence_status = (
                persistence_outcome.status
                if persistence_outcome is not None
                else (
                    "skipped_empty"
                    if empty_capture_discarded
                    else (
                        "final_summary_unavailable"
                        if self._summary_writer is not None
                        and not final_payload_received
                        else "not_requested"
                    )
                )
            )
            record_id = (
                persistence_outcome.battle_record_id
                if persistence_outcome is not None
                else None
            )
            retention_kind = (
                persistence_outcome.retention_kind
                if persistence_outcome is not None
                else None
            )
            message = {
                "saved": "전투 리포트 수집이 끝나 자동 저장되었습니다.",
                "skipped_empty": "전투 리포트 수집이 끝났지만 유효한 피해가 없어 기록을 저장하지 않았습니다.",
                "discarded_stale": "계정 컨텍스트가 바뀌어 이전 전투 리포트를 저장하지 않았습니다.",
                "final_summary_unavailable": (
                    "전투 리포트 수집이 끝났지만 최종 요약을 얻지 못해 기록을 저장하지 않았습니다."
                ),
            }.get(persistence_status, "전투 리포트 수집이 끝났습니다.")
            if persistence_outcome is not None and persistence_outcome.warning_message:
                message += persistence_outcome.warning_message
            self._publish(
                "stopped",
                message,
                running=False,
                summary=summary,
                persistence_status=persistence_status,
                battle_record_id=record_id,
                retention_kind=retention_kind,
            )
            log_event(
                "INFO",
                "battle_report.capture_succeeded",
                "전투 리포트 수집 종료",
                self._operation_context,
                phase="succeeded",
                result="succeeded",
                character_count=len(summary.characters) if summary is not None else 0,
                skill_count=len(summary.skills) if summary is not None else 0,
                total_hits=summary.total_hits if summary is not None else 0,
                persistence_status=persistence_status,
                battle_record_id=record_id,
            )
        else:
            self._publish(
                "error",
                "전투 리포트 수집에 실패했습니다.",
                running=False,
                summary=summary,
                error=str(terminal_error),
                error_code=type(terminal_error).__name__,
            )
            log_event(
                "ERROR",
                "battle_report.capture_failed",
                "전투 리포트 수집 실패",
                self._operation_context,
                phase="failed",
                result="failed",
                error=safe_exception(terminal_error),
            )

    def _discard_was_requested(self) -> bool:
        with self._lock:
            return self._discard_requested

    def _has_observed_battle_evidence(self) -> bool:
        with self._lock:
            summary = self._latest_summary
            return self._source_battle_record_id is not None or bool(
                summary is not None
                and (summary.total_damage > 0 or summary.total_hits > 0)
            )

    def _stop_client_capture(self, client: BattleCoreClient) -> None:
        completed = threading.Event()
        outcome: list[Mapping[str, Any] | Exception] = []

        def stop_capture() -> None:
            try:
                outcome.append(client.stop_capture())
            except Exception as error:
                outcome.append(error)
            finally:
                completed.set()

        threading.Thread(
            target=stop_capture,
            name="battle-capture-stop",
            daemon=True,
        ).start()
        if not completed.wait(self._stop_timeout_seconds):
            abort = getattr(client, "abort", None)
            if callable(abort):
                abort()
            raise RuntimeError(
                f"nte-core 중지 시간 초과 ({self._stop_timeout_seconds:g}초)"
            )
        if not outcome:
            raise RuntimeError("nte-core 중지 스레드가 결과를 반환하지 않았습니다")
        result = outcome[0]
        if isinstance(result, Exception):
            raise result

    def _prune_raw_captures(self) -> None:
        """Best-effort cleanup without exposing the account log path."""
        directory = self._raw_capture_directory
        if directory is None:
            return
        try:
            result = prune_raw_capture_files(directory)
        except Exception as error:
            log_event(
                "WARNING",
                "battle_report.raw_capture_prune_failed",
                "전투 리포트 원본 패킷 캡처 정리 실패, 다음 수집 때 다시 시도합니다",
                self._operation_context,
                error=safe_exception(error),
            )
            return
        if result.deleted_count:
            log_event(
                "INFO",
                "battle_report.raw_capture_pruned",
                "이전 전투 리포트 원본 패킷 캡처를 정리했습니다",
                self._operation_context,
                deleted_count=result.deleted_count,
                deleted_bytes=result.deleted_bytes,
                retained_count=result.retained_count,
            )

    def _poll_axis(
        self,
        client: BattleCoreClient,
        *,
        maximum_pages: int,
    ) -> dict[str, Any] | None:
        raw_record = client.get_battle_record(
            battle_record_id=self._source_battle_record_id,
            subtract_time_stop=True,
        )
        if raw_record is None:
            return None
        record = parse_battle_record(raw_record)
        self._require_contract_v5(record)
        source_record_id = str(record["battle_record_id"])
        if self._source_battle_record_id is None:
            self._source_battle_record_id = source_record_id
        elif source_record_id != self._source_battle_record_id:
            raise RuntimeError("같은 수집에서 서로 다른 nte-core 전투 기록이 나타났습니다")

        for _page_index in range(maximum_pages):
            try:
                raw_page = client.get_battle_axis(
                    battle_record_id=source_record_id,
                    cursor=self._axis_cursor,
                    limit=500,
                )
            except Exception as error:
                if nte_core_error_has_domain_code(
                    error,
                    frozenset({"BATTLE_AXIS_CURSOR_EXPIRED"}),
                ):
                    self._axis_cursor = None
                    continue
                raise
            if raw_page is None:
                break
            page = parse_battle_axis(raw_page)
            self._require_contract_v5(page)
            writer = self._summary_writer
            if writer is not None:
                writer.append_axis_page(
                    capture_operation_id=self._operation_context.operation_id,
                    page=page,
                )
            next_cursor = page.get("next_cursor")
            if next_cursor is None or next_cursor == self._axis_cursor:
                break
            self._axis_cursor = str(next_cursor)
            if not page["rows"]:
                break
        return record

    def _read_final_axis(
        self,
        client: BattleCoreClient,
    ) -> dict[str, Any] | None:
        raw_record = client.get_battle_record(
            battle_record_id=self._source_battle_record_id,
            subtract_time_stop=True,
        )
        if raw_record is None:
            return None
        record = parse_battle_record(raw_record)
        self._require_contract_v5(record)
        source_record_id = str(record["battle_record_id"])
        generation = str(record["generation"])
        incomplete_reason: str | None = None
        pages: list[dict[str, Any]] = []

        if str(record.get("state") or "") != "finalized":
            incomplete_reason = "final_record_not_finalized"
        else:
            cursor: str | None = None
            for _page_index in range(120):
                try:
                    raw_page = client.get_battle_axis(
                        battle_record_id=source_record_id,
                        cursor=cursor,
                        limit=500,
                    )
                except Exception as error:
                    if nte_core_error_has_domain_code(
                        error,
                        frozenset({"BATTLE_AXIS_CURSOR_EXPIRED"}),
                    ):
                        incomplete_reason = "final_axis_cursor_expired"
                        break
                    raise
                if raw_page is None:
                    break
                page = parse_battle_axis(raw_page)
                self._require_contract_v5(page)
                if (
                    str(page["generation"]) != generation
                    or str(page["battle_record_id"]) != source_record_id
                ):
                    incomplete_reason = "final_axis_generation_changed"
                    break
                pages.append(page)
                next_cursor = page.get("next_cursor")
                if next_cursor is None or next_cursor == cursor:
                    break
                cursor = str(next_cursor)
                if not page["rows"]:
                    break

            if incomplete_reason is None and (
                not pages or pages[-1].get("next_cursor") is not None
            ):
                incomplete_reason = "final_axis_not_drained"
            if incomplete_reason is None and (
                not bool(pages[-1].get("finalized"))
                or not bool(pages[-1].get("complete"))
            ):
                incomplete_reason = "final_axis_incomplete"

        verified: dict[str, Any] | None = None
        if incomplete_reason is None:
            verify_raw = client.get_battle_record(
                battle_record_id=source_record_id,
                subtract_time_stop=True,
            )
            if verify_raw is None:
                incomplete_reason = "final_record_disappeared"
            else:
                verified = parse_battle_record(verify_raw)
                self._require_contract_v5(verified)
                if (
                    str(verified["generation"]) != generation
                    or str(verified["battle_record_id"]) != source_record_id
                    or str(verified.get("state") or "") != "finalized"
                ):
                    incomplete_reason = "final_axis_generation_changed"

        writer = self._summary_writer
        if incomplete_reason is None:
            if writer is not None:
                writer.replace_axis_pages(
                    capture_operation_id=self._operation_context.operation_id,
                    pages=pages,
                    source_generation=generation,
                )
            return verified

        if writer is not None:
            writer.replace_axis_pages(
                capture_operation_id=self._operation_context.operation_id,
                pages=(),
                source_generation=generation,
                incomplete_reason=incomplete_reason,
            )
        incomplete_record = dict(record)
        incomplete_record["axis_complete"] = False
        incomplete_record["finalization_incomplete_reason"] = incomplete_reason
        return incomplete_record

    @staticmethod
    def _require_contract_v5(payload: Mapping[str, Any]) -> None:
        if int(payload.get("contract_version") or 0) < _BATTLE_READ_CONTRACT_VERSION:
            raise RuntimeError(
                "현재 nte-core 전투 계약이 v5 미만이라 새 전투 리포트 수집을 시작할 수 없습니다"
            )

    def _on_summary_event(self, event: dict[str, object]) -> None:
        if self._discard_was_requested():
            return
        try:
            summary = parse_battle_summary_event(event)
        except Exception as error:
            self._event_error = error
            self._summary_event.set()
            self._stop_event.set()
            return
        with self._lock:
            if summary.sequence and summary.sequence <= self._last_sequence:
                return
            self._last_sequence = max(self._last_sequence, summary.sequence)
            self._latest_summary = summary
        self._summary_event.set()
        current_phase = self.state.phase
        self._publish(
            "stopping" if current_phase == "stopping" else "running",
            (
                "수집을 중지하고 최종 전투 리포트를 읽는 중……"
                if current_phase == "stopping"
                else "수집 중: 실시간 피해 데이터를 받았습니다."
            ),
            running=True,
            summary=summary,
        )

    def _publish(
        self,
        phase: str,
        message: str,
        *,
        running: bool,
        summary: BattleSummary | None = None,
        error: str | None = None,
        error_code: str | None = None,
        persistence_status: str = "not_requested",
        battle_record_id: int | None = None,
        retention_kind: Literal["auto", "manual"] | None = None,
    ) -> None:
        state = BattleCaptureState(
            phase=phase,
            message=message,
            running=running,
            summary=summary,
            error=error,
            error_code=error_code,
            persistence_status=persistence_status,
            battle_record_id=battle_record_id,
            retention_kind=retention_kind,
        )
        with self._lock:
            self._state = state
            handlers = tuple(self._handlers)
        for handler in handlers:
            try:
                handler(state)
            except Exception:
                # UI observers must not terminate the capture lifecycle.
                continue
