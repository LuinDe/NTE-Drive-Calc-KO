# 承担 InventorySyncService 的后台捕获、稳定化与持久化运行循环。
"""Runtime loop extracted from the public inventory sync service."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from src.observability import log_event
from src.integrations.nte_core_protocol import NteCoreProcessError
from src.services.account_settings_service import AccountSettingsService
from src.services.all_item_snapshot_storage import store_all_item_snapshot
from src.services.raw_capture_retention import prune_raw_capture_files
from src.storage.sqlite.inventory_save_error import InventorySnapshotSaveError
from src.utils.logger import logger

from .inventory_sync_contracts import InventoryCoreClient
from .inventory_capture_wait import CaptureStartError, InventorySyncCancelled, require_inventory_operation, wait_capture_ready
from .inventory_sync_logging import (
    InventorySyncDiagnostics,
    inventory_core_log_fields,
    inventory_payload_log_fields,
    stored_snapshot_log_fields,
)
from .inventory_snapshot_stabilizer import InventorySnapshotStabilizer, SnapshotOfferResult
from .inventory_source_capabilities import has_native_inventory_uids, is_visual_inventory_source
from .packet_item_observation_sync import PacketItemObservationSync


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _snapshot_listening_message(
    summary: Mapping[str, Any] | None,
    has_character_list: bool,
    *,
    received: bool = False,
) -> str:
    if summary is None:
        return "게임 접속 후 완전한 가방 수신 대기 중"
    source = summary.get("source")
    if is_visual_inventory_source(source):
        return "현재는 비전 스캔 인벤토리입니다. 원본 가방 동기화를 기다리는 중이며, 비전 스캔은 캐릭터 인스턴스를 제공하지 않습니다"
    if not has_native_inventory_uids(source):
        return "현재 인벤토리 출처는 아직 원본 캐릭터 식별을 지원하지 않습니다. 원본 가방 동기화를 기다리는 중"
    prefix = "원본 가방을 받았습니다" if received else "현재 원본 가방이 저장되어 있습니다"
    if not has_character_list:
        return f"{prefix}. 이 스냅샷에는 독립 캐릭터 목록이 없습니다. 백그라운드에서 업데이트를 감시하는 중"
    if not summary.get("character_instance_count"):
        return f"{prefix}. 아직 캐릭터 인스턴스를 관측하지 못했습니다. 백그라운드에서 업데이트를 감시하는 중"
    return "가방이 동기화되었으며 백그라운드에서 변화를 감시하는 중"


def _snapshot_waiting_message(summary, has_character_list):
    if summary is None:
        return "수신 대기가 준비되었습니다. 게임 진입과 가방 데이터 수신을 기다립니다."
    source = summary.get("source")
    if is_visual_inventory_source(source):
        previous = "현재는 시각 스캔 기반 인벤토리로, 캐릭터 인스턴스를 제공하지 않습니다"
    elif has_native_inventory_uids(source) and not has_character_list:
        previous = "마지막 네이티브 가방에 독립된 캐릭터 목록이 첨부되지 않았습니다"
    elif not has_native_inventory_uids(source):
        previous = "마지막 인벤토리 출처는 아직 네이티브 캐릭터 식별 정보를 지원하지 않습니다"
    else:
        previous = "마지막으로 저장한 가방은 여전히 계산에 사용할 수 있습니다"
    return f"수신 대기가 준비되었습니다. 이번 가방 데이터를 기다립니다; {previous}."


def run_inventory_sync(service: Any) -> None:
    client: InventoryCoreClient | None = None
    packet_items: PacketItemObservationSync | None = None
    fatal_error: Exception | None = None
    stop_reason = "stop_requested"
    diagnostics = InventorySyncDiagnostics(service._operation_context)
    sync_stage = "loading_settings"
    stabilizer: InventorySnapshotStabilizer | None = None
    current_id: int | None = None
    try:
        require_inventory_operation(service)
        with service._open_dao() as dao:
            settings = AccountSettingsService(service.database_path).load("sync")
            native = service.capture_source == "native"
            settle_seconds = (
                service._settle_seconds
                if service._settle_seconds is not None
                else (0.2 if native else float(settings["inventory_settle_seconds"]))
            )
            stabilizer = InventorySnapshotStabilizer(settle_seconds)
            current_id = dao.current_inventory_snapshot_id()
            current_summary = (
                dao.inventory_snapshot_summary(current_id) if current_id is not None else None
            )
            current_has_character_instances = (
                dao.snapshot_has_independent_character_instances(current_id)
                if current_id is not None
                else False
            )
            # 视觉库存不是原生同步的去重基线；即使内容相同，也必须保存原生来源。
            if (
                current_id is not None
                and current_summary is not None
                and has_native_inventory_uids(current_summary.get("source"))
            ):
                previous = dao.raw_snapshot(current_id)
                if previous:
                    try:
                        stabilizer.seed_committed(previous)
                    except ValueError:
                        log_event(
                            "WARNING", "inventory_sync.baseline_invalid",
                            "저장된 스냅샷을 중복 제거 기준선으로 사용할 수 없어 새 완전 스냅샷을 기다립니다",
                            service._operation_context.with_values(snapshot_id=current_id),
                        )

            log_event(
                "INFO", "inventory_sync.baseline_selected", "이번 동기화의 중복 제거 기준선을 확정했습니다",
                service._operation_context.with_values(snapshot_id=current_id),
                baseline_seeded=stabilizer.committed_fingerprint is not None,
                settle_seconds=settle_seconds,
                source=current_summary.get("source") if current_summary else None,
                item_count=current_summary.get("stored_item_count") if current_summary else None,
                character_instances_independent=current_has_character_instances,
            )

            sync_stage = "starting_core"
            require_inventory_operation(service)
            client = service._client_factory()
            service._client = client
            client.start()
            client.add_event_handler("event.inventory.snapshot", service._on_inventory_event)
            client.add_event_handler("event.capture.status", service._on_capture_status_event)
            if not native and "inventory_observed_items_v1" in (client.hello_result or {}).get("capabilities", ()):
                packet_items = PacketItemObservationSync(service, dao, settle_seconds)
                client.add_event_handler("event.inventory.items_observed", packet_items.on_event)
            log_event(
                "INFO",
                "inventory_sync.core_connected",
                "가방 동기화가 nte-core에 연결되었습니다",
                service._operation_context,
                protocol_version=service._protocol_version(client),
                capabilities=list((client.hello_result or {}).get("capabilities") or ()),
                **inventory_core_log_fields(client.hello_result or {}),
            )
            capture_device = service._capture_device_id
            if capture_device is None:
                capture_device = settings.get("capture_device_id")
            raw_enabled = service._raw_capture_enabled
            if raw_enabled is None:
                raw_enabled = bool(settings.get("raw_capture_enabled"))
            if native:
                capture_device, raw_enabled = None, False
            if raw_enabled:
                require_inventory_operation(service, "diagnostics")
            if raw_enabled and service._raw_capture_directory is not None:
                service._raw_capture_directory.mkdir(parents=True, exist_ok=True)
                service._prune_raw_captures()
                log_event(
                    "DEBUG",
                    "inventory_sync.raw_capture_enabled",
                    "nte-core 원본 패킷 캡처 진단을 켰습니다",
                    service._operation_context,
                    directory=service._raw_capture_directory,
                )
            sync_stage = "starting_capture"
            require_inventory_operation(service)
            supports_wait = not native and "capture_wait_v1" in (client.hello_result or {}).get("capabilities", ())
            capture_result = client.start_capture(
                profile="inventory",
                device_name=capture_device,
                raw_capture="enabled" if raw_enabled else "disabled",
                **({"wait_for_game": True} if supports_wait else {}),
            )
            service._capture_monitor.update(capture_result)
            sync_stage = "waiting_capture_ready"
            service._publish(
                "starting",
                "DLL 가방 소스에 연결하는 중" if native else "패킷 캡처를 초기화하는 중이며 네트워크 어댑터 준비를 기다립니다",
                running=True,
                capturing=False,
                last_snapshot_id=current_id,
            )
            if not wait_capture_ready(service, client, supports_wait=supports_wait, diagnostics=bool(raw_enabled)):
                return
            log_event(
                "INFO",
                "inventory_sync.capture_started",
                "가방 동기화 소스가 준비되었으며, 완전한 가방 스냅샷을 기다리는 중",
                service._operation_context,
                raw_capture=bool(raw_enabled),
                capture_device_configured=bool(capture_device),
            )
            sync_stage = "listening"
            if current_summary is not None and current_id is not None:
                log_event(
                    "INFO",
                    "inventory_sync.current_snapshot_loaded",
                    "현재 안정 가방 요약을 불러왔습니다",
                    service._operation_context.with_values(snapshot_id=current_id),
                    **stored_snapshot_log_fields(
                        current_summary,
                        character_instances_independent=current_has_character_instances,
                    ),
                )
            service._publish(
                "waiting",
                _snapshot_waiting_message(current_summary, current_has_character_instances),
                running=True,
                capturing=not native,
                last_snapshot_id=current_id,
                last_item_count=(
                    int(current_summary["stored_item_count"])
                    if current_summary is not None
                    else None
                ),
            )

            retry_save_at = 0.0
            next_native_status = 0.0
            native_status_message = None
            guard_generation, _guard_uids = service._full_inventory_guard()
            while not service._stop_requested.is_set():
                service._event_ready.wait(service._poll_seconds)
                require_inventory_operation(service)
                if raw_enabled:
                    require_inventory_operation(service, "diagnostics")
                if native and time.monotonic() >= next_native_status:
                    native_status = client.status()
                    require_inventory_operation(service)
                    service._capture_monitor.update(native_status)
                    next_native_status = time.monotonic() + (
                        0.2 if native_status.get("native_change_pending") else 1.0
                    )
                    _apply_native_profiles(service, native_status)
                    store_all_item_snapshot(service, dao, client, native_status)
                    if not native_status.get("native_snapshot_ready", False):
                        stabilizer.discard_pending()
                        service._take_latest_event()  # Do not re-offer an observation invalidated during this poll.
                        message = str(native_status.get("message") or "DLL이 완전한 가방 스냅샷을 제공하기를 기다리고 있습니다.")
                        if message != native_status_message:
                            service._publish("waiting", message, running=True, capturing=False, source_snapshot_ready=False)
                        native_status_message = message
                    else:
                        native_status_message = None
                capture_status, capture_error = service._capture_monitor.read()
                if capture_status == "failed":
                    raise CaptureStartError(capture_error or "CAPTURE_FAILED")
                diagnostics.summary(
                    phase=service.state.phase, pending_item_count=stabilizer.pending_item_count,
                    snapshot_id=current_id,
                )
                event = service._take_latest_event()
                if packet_items is not None:
                    packet_items.receive_latest()
                if event is not None:
                    sync_stage = "processing_event"
                    for source_snapshot_id, items, observed_at, sequence in (
                        service._take_pending_runtime_state_deltas()
                    ):
                        require_inventory_operation(service)
                        updated_count = dao.apply_inventory_runtime_state_delta(
                            source_snapshot_id,
                            items,
                            observed_at_unix_ms=observed_at,
                            sequence=sequence,
                        )
                        if updated_count:
                            log_event(
                                "DEBUG",
                                "inventory_sync.runtime_state_delta_applied",
                                "부분 장비 상태를 병합했으며 완전한 가방 스냅샷은 바꾸지 않습니다",
                                service._operation_context,
                                updated_count=updated_count,
                            )
                    current_guard_generation, required_uids = service._full_inventory_guard()
                    if current_guard_generation != guard_generation:
                        # A candidate collected before the apply guard must
                        # not settle after the guard is installed (or removed).
                        stabilizer.discard_pending()
                        guard_generation = current_guard_generation
                    # Some transitions from the old capture stream emit a
                    # legacy inventory event immediately after the new
                    # v0.3.5 event.  If the saved snapshot is legacy and a
                    # candidate already has independent character UIDs,
                    # allowing that fallback through would erase the
                    # candidate as a mere "revert" before it can settle.
                    # Only prefer the richer event during this one-time
                    # format upgrade; normal inventory changes remain
                    # governed by the stabilizer.
                    if (
                        not current_has_character_instances
                        and not service._event_has_independent_character_instances(event)
                        and stabilizer.pending_has_independent_character_instances
                    ):
                        diagnostics.record(
                            event,
                            SnapshotOfferResult("ignored", reason_code="legacy_candidate_preferred"),
                            guard_item_count=len(required_uids) if required_uids is not None else None,
                        )
                        log_event(
                            "DEBUG",
                            "inventory_sync.legacy_event_ignored",
                            "캐릭터 인스턴스 스냅샷 직후의 이전 형식 가방 이벤트를 무시합니다",
                            service._operation_context,
                        )
                        sync_stage = "listening"
                        continue
                    result = stabilizer.offer(event, required_uids=required_uids)
                    diagnostics.record(
                        event, result,
                        guard_item_count=len(required_uids) if required_uids is not None else None,
                    )
                    if result.status in {"collecting", "changed"}:
                        candidate_fields = inventory_payload_log_fields(event)
                        log_event(
                            "DEBUG",
                            "inventory_sync.candidate_received",
                            "가방 스냅샷 후보를 받았으며 내용 안정을 기다립니다",
                            service._operation_context,
                            added_count=result.added_count,
                            removed_count=result.removed_count,
                            candidate_status=result.status,
                            **candidate_fields,
                        )
                        service._publish(
                            "collecting",
                            f"{result.item_count}개를 받았으며 가방 내용 안정을 기다립니다",
                            source_snapshot_ready=True,
                            running=True,
                            capturing=True,
                            pending_item_count=result.item_count,
                            added_count=result.added_count,
                            removed_count=result.removed_count,
                            error=None,
                        )
                    elif result.status in {"reverted", "unchanged"}:
                        log_event(
                            "DEBUG",
                            "inventory_sync.candidate_reverted"
                            if result.status == "reverted"
                            else "inventory_sync.snapshot_unchanged",
                            "변경되지 않은 가방 스냅샷을 받아 감시를 계속합니다",
                            service._operation_context,
                        )
                        service._publish(
                            "listening",
                            _snapshot_listening_message(
                                current_summary, current_has_character_instances, received=True,
                            ),
                            source_snapshot_ready=True,
                            running=True,
                            capturing=True,
                            pending_item_count=None,
                            added_count=0,
                            removed_count=0,
                        )
                        if (current_summary and current_summary.get("source") == "nte_core"
                            and (not native or client.confirm_inventory_snapshot(event["params"].get("native_snapshot")))):
                            service._record_inventory_observation(event["params"])

                sync_stage = "listening"
                now = time.monotonic()
                if packet_items is not None:
                    packet_items.save_if_stable(now)
                stable = stabilizer.ready(now=now)
                if stable is None or now < retry_save_at:
                    continue
                if native and not client.confirm_inventory_snapshot(stable.payload.get("native_snapshot")):
                    # Validate only the candidate's revision. Do not start a full
                    # read here or let an unrelated character refresh delay saving.
                    stabilizer.discard_pending()
                    next_native_status = 0.0
                    service._publish("waiting", "가방에 다시 변화가 생겨, 병합하여 업데이트하는 중입니다.", running=True,
                                     capturing=False, source_snapshot_ready=False, pending_item_count=None)
                    continue
                service._publish(
                    "saving",
                    f"가방이 안정되어 {stable.item_count}개를 저장하는 중",
                    running=True,
                    capturing=True,
                    pending_item_count=stable.item_count,
                )
                sync_stage = "saving_snapshot"
                require_inventory_operation(service)
                try:
                    snapshot_id = dao.import_inventory_snapshot(
                        stable.message,
                        source="nte_core",
                        protocol_version=service._protocol_version(client),
                    )
                except Exception as exc:
                    diagnostics.save_failure_count += 1
                    save_diagnostics = (
                        exc.diagnostics if isinstance(exc, InventorySnapshotSaveError) else {}
                    )
                    log_event(
                        "WARNING",
                        "inventory_sync.snapshot_commit_retry",
                        "안정 가방 저장 실패, 자동으로 재시도합니다",
                        service._operation_context,
                        error=exc,
                        retry_delay_seconds=2,
                        save_attempt_count=diagnostics.save_failure_count + diagnostics.committed_count,
                        candidate_item_count=stable.item_count,
                        **save_diagnostics,
                    )
                    retry_save_at = time.monotonic() + 2.0
                    service._publish(
                        "error",
                        "안정 가방 저장 실패, 백그라운드에서 자동으로 재시도합니다",
                        running=True,
                        capturing=True,
                        error=f"{type(exc).__name__}: {exc}",
                        error_code=(
                            exc.error_code
                            if isinstance(exc, InventorySnapshotSaveError)
                            else "SNAPSHOT_SAVE_FAILED"
                        ),
                    )
                    sync_stage = "waiting_save_retry"
                    continue
                stabilizer.mark_committed(stable.fingerprint)
                diagnostics.committed_count += 1
                sync_stage = "finalizing_snapshot"
                previous_item_count = current_summary.get("stored_item_count") if current_summary else None
                previous_source = current_summary.get("source") if current_summary else None
                current_id = snapshot_id
                if packet_items is not None:
                    packet_items.on_inventory_snapshot_committed(snapshot_id)
                current_has_character_instances = dao.snapshot_has_independent_character_instances(snapshot_id)
                committed_summary = dao.inventory_snapshot_summary(snapshot_id) or {}
                current_summary = committed_summary
                committed_context = service._operation_context.with_values(
                    snapshot_id=snapshot_id,
                )
                log_event(
                    "INFO",
                    "inventory_sync.snapshot_committed",
                    "안정 가방 스냅샷을 저장했습니다",
                    committed_context,
                    protocol_version=service._protocol_version(client),
                    previous_item_count=previous_item_count,
                    previous_source=previous_source,
                    stable_seconds=round(now - stable.last_changed_at, 3),
                    **stored_snapshot_log_fields(
                        committed_summary,
                        character_instances_independent=current_has_character_instances,
                    ),
                )
                if service._template_refresh is not None:
                    try:
                        refreshed = service._template_refresh()
                        if isinstance(refreshed, Mapping) and refreshed.get("changed"):
                            log_event(
                                "INFO",
                                "inventory_sync.templates_refreshed",
                                "공용 캐릭터·아크 템플릿을 새로 고쳤습니다",
                                committed_context,
                                role_count=int(refreshed.get("role_count", 0)),
                                fork_count=int(refreshed.get("fork_count", 0)),
                            )
                    except Exception as exc:
                        # 背包快照已经成功提交，模板缓存刷新不能阻断同步监听。
                        log_event(
                            "WARNING",
                            "inventory_sync.template_refresh_failed",
                            "공용 캐릭터·아크 템플릿 새로 고침 실패, 다음 동기화 때 재시도합니다",
                            committed_context,
                            error=exc,
                        )
                try:
                    retention = dao.prune_inventory_snapshots(retain_recent=3 if native else None)
                    if retention["deleted_snapshot_count"]:
                        log_event(
                            "INFO",
                            "inventory_sync.retention_applied",
                            "보관 정책에 따라 기록 가방 스냅샷을 정리했습니다",
                            committed_context,
                            deleted_snapshot_count=retention["deleted_snapshot_count"],
                            retained_snapshot_count=retention["total_after"],
                        )
                except Exception as exc:
                    # 新快照已经安全提交，清理失败不能让同步服务重新导入同一份数据。
                    log_event(
                        "WARNING",
                        "inventory_sync.retention_failed",
                        "기록 가방 스냅샷 정리 실패, 다음 동기화 또는 수동 유지 관리 때 재시도합니다",
                        committed_context,
                        error=exc,
                    )
                retry_save_at = 0.0
                service._publish(
                    "listening",
                    _snapshot_listening_message(
                        current_summary, current_has_character_instances, received=True,
                    ),
                    running=True,
                    capturing=True,
                    pending_item_count=None,
                    added_count=0,
                    removed_count=0,
                    last_snapshot_id=snapshot_id,
                    last_item_count=stable.item_count,
                    last_synced_at_utc=_utc_now(),
                    error=None,
                    error_code=None,
                )
                if current_summary and current_summary.get("source") == "nte_core":
                    service._record_inventory_observation(stable.payload)
                sync_stage = "listening"
    except InventorySyncCancelled as exc:
        # Only classify from bounded owner state; exception text can contain
        # third-party payloads and does not prove who requested cancellation.
        stop_reason = (
            "stop_requested" if service._stop_requested.is_set() else
            "context_changed" if service._context_is_current is not None and not service._context_is_current() else
            exc.reason if exc.reason != "operation_cancelled" else
            "native_session_cancelled" if service.capture_source == "native" else "operation_cancelled"
        )
    except Exception as exc:
        fatal_error = exc
        stop_reason = ("connection_lost" if service.capture_source == "native"
                       and isinstance(exc, NteCoreProcessError) else "operation_failed")
        log_event(
            "ERROR",
            "inventory_sync.failed",
            "가방 동기화 서비스가 비정상 중지되었습니다",
            service._operation_context,
            failure_stage=sync_stage,
            stop_reason=stop_reason,
            error=exc,
            error_code=(
                str(getattr(exc, "domain_code"))
                if getattr(exc, "domain_code", None)
                else type(exc).__name__
            ),
        )
        service._publish(
            "error",
            "가방 동기화 서비스가 중지되었습니다",
            running=False,
            capturing=False,
            error=f"{type(exc).__name__}: {exc}",
            stop_reason=stop_reason,
            error_code=(
                str(getattr(exc, "domain_code"))
                if getattr(exc, "domain_code", None)
                else type(exc).__name__
            ),
        )
    finally:
        if client is not None:
            if packet_items is not None:
                try:
                    client.remove_event_handler("event.inventory.items_observed", packet_items.on_event)
                except Exception:
                    pass
            try:
                client.remove_event_handler("event.inventory.snapshot", service._on_inventory_event)
            except Exception:
                pass
            try:
                client.remove_event_handler("event.capture.status", service._on_capture_status_event)
            except Exception:
                pass
            try:
                client.stop_capture()
            except Exception:
                pass
            service._prune_raw_captures()
            try:
                client.close()
            except Exception:
                pass
        service._client = None
        diagnostics.summary(
            phase="failed" if fatal_error is not None else "stopped",
            pending_item_count=stabilizer.pending_item_count if stabilizer is not None else None,
            snapshot_id=current_id, final=True,
        )
        if fatal_error is None:
            log_event(
                "INFO",
                "inventory_sync.stopped",
                "가방 동기화가 중지되었습니다",
                service._operation_context,
                stop_reason=stop_reason,
                stop_stage=sync_stage,
            )
            service._publish(
                "stopped",
                "가방 동기화가 중지되었습니다",
                running=False,
                capturing=False,
                pending_item_count=None,
                stop_reason=stop_reason,
            )


def _apply_native_profiles(service, status):
    apply = service._native_profiles_apply
    if apply is None:
        return
    snapshot = status.get("native_character_snapshot")
    error = status.get("native_character_error")
    if snapshot is not None:
        try:
            require_inventory_operation(service)
            result = apply(snapshot["profiles"], check=lambda: require_inventory_operation(service))
            require_inventory_operation(service)
        except (InventorySyncCancelled, PermissionError):
            raise
        except Exception as exc:
            log_event("WARNING", "inventory_sync.character_save_failed", "캐릭터 자동 동기화가 저장되지 않았습니다",
                      service._operation_context, error_type=type(exc).__name__)
            error = "캐릭터 자동 동기화가 저장되지 않아 기존 육성을 유지했습니다; 잠시 후 자동으로 재시도합니다."
        else:
            error = result.message if result.warnings else None
            service._publish(service.state.phase, service.state.message,
                             character_sync_revision=service.state.character_sync_revision + bool(result.saved_count),
                             character_sync_error=error)
    if error and error != service.state.character_sync_error:
        service._publish(service.state.phase, service.state.message, character_sync_error=error)


def prune_raw_captures(service: Any) -> None:
    """Best-effort cleanup; packet capture must never fail because pruning did."""
    if service.capture_source == "native" or service._raw_capture_directory is None:
        return
    try:
        result = prune_raw_capture_files(service._raw_capture_directory)
    except Exception as exc:
        logger.warning(f"nte-core .pcapng 진단 파일 정리 실패: {exc}")
        return
    if result.deleted_count:
        logger.info(
            "이전 .pcapng 진단 파일 {}개를 자동 정리해 {:.1f} MiB를 확보했습니다."
            "현재 {}개 보관 중",
            result.deleted_count,
            result.deleted_bytes / (1024 * 1024),
            result.retained_count,
        )
