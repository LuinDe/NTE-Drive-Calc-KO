# 将仓库稳定快照按状态管理规则计算并通过本地核心组件写回游戏。
"""Official SQLite warehouse state management.

This service reuses the full-scan discard/lock rules, but evaluates a pinned
SQLite snapshot and applies the resulting state changes through the already
running nte-core inventory session.  It never relies on screenshot ordering.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

from src.domain.post_actions import summarize_state_changes
from src.integrations.warehouse_state_writer import (
    LiveInventorySync,
    WarehouseStateWriteError,
    WarehouseStateWriter,
)
from src.services.post_action_evaluator import PostActionEvaluator
from src.models.equipment import Drive, Tape
from src.observability import OperationContext, operation_scope
from src.services.sqlite_allocation_inventory import SqliteAllocationInventory
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


class WarehouseStateManagementError(RuntimeError):
    """仓库一键弃置/锁定未满足安全条件或本地核心组件调用失败。"""


@dataclass(frozen=True)
class WarehouseStateManagementPlan:
    snapshot_id: int
    changes: tuple[dict[str, Any], ...]
    filter_summary: dict[str, int]
    command_projection_allowed: bool = False


@dataclass(frozen=True)
class WarehouseStateManagementResult:
    before_snapshot_id: int
    summary: dict[str, int]
    # Keep accepted changes for the timeout fallback.  When a later stable
    # snapshot arrives, after_snapshot_id and verified describe the
    # authoritative reconciliation result.
    changes: tuple[dict[str, Any], ...] = ()
    after_snapshot_id: int | None = None
    verified: bool = False
    verification_error: str | None = None
    inventory_reduction_observed: bool = False


def _compat_uid(row: Mapping[str, Any]) -> str:
    prefix = "module" if row.get("kind") == "module" else "core"
    return f"nte-{prefix}-{row['uid_slot']}-{row['uid_serial']}"


def _current_state(row: Mapping[str, Any]) -> str:
    if row.get("discarded"):
        return "discarded"
    if row.get("locked"):
        return "locked"
    return "normal"


def _equipment_uid(row: Mapping[str, Any]) -> dict[str, int]:
    slot, serial = row.get("uid_slot"), row.get("uid_serial")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (slot, serial)):
        raise WarehouseStateManagementError("안정 스냅샷에 잘못된 장비 UID가 있습니다")
    return {"slot": slot, "serial": serial}


class WarehouseStateManagementService:
    """Evaluate and apply discard/lock rules for one immutable inventory snapshot."""

    def __init__(
        self,
        database_path: str | Path,
        sync_service: LiveInventorySync,
        *,
        dao_factory=UserDataDao,
        static_dao_factory=StaticGameDataDao,
        state_writer_factory=WarehouseStateWriter,
        config_dir: str | Path | None = None,
        operation_context: OperationContext | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.sync_service = sync_service
        self.state_writer = state_writer_factory(sync_service)
        self.dao_factory = dao_factory
        self.static_dao_factory = static_dao_factory
        self.config_dir = config_dir
        self.operation_context = operation_context or OperationContext.create(
            "warehouse"
        )

    def evaluate(self, config: dict, selected_roles: list[str] | None = None) -> WarehouseStateManagementPlan:
        """Build state changes from the current snapshot without changing game state."""
        with operation_scope(
            self.operation_context,
            started_event="warehouse.state_evaluate_started",
            succeeded_event="warehouse.state_evaluate_succeeded",
            failed_event="warehouse.state_evaluate_failed",
            message="창고 상태 관리 대상 계산",
            selected_role_count=len(selected_roles or ()),
        ) as span:
            plan = self._evaluate(config, selected_roles)
            span.annotate(
                snapshot_id=plan.snapshot_id,
                change_count=len(plan.changes),
                filter_summary=plan.filter_summary,
            )
            return plan

    def _evaluate(
        self,
        config: dict,
        selected_roles: list[str] | None,
    ) -> WarehouseStateManagementPlan:
        with self.dao_factory(self.database_path) as user_dao, self.static_dao_factory() as static_dao:
            snapshot_id = user_dao.current_inventory_snapshot_id()
            if snapshot_id is None:
                raise WarehouseStateManagementError("아직 안정 가방 스냅샷이 없어 창고를 관리할 수 없습니다")
            projection = SqliteAllocationInventory(user_dao, static_dao).build(snapshot_id)
            snapshot_id = projection.snapshot_id
            source_rows = user_dao.list_inventory_items(snapshot_id)

        source_by_uid = {_compat_uid(row): row for row in source_rows}
        inventory = []
        parsed_items = []
        for index, payload in enumerate(projection.items, 1):
            source = source_by_uid.get(payload["uid"])
            if source is None:
                raise WarehouseStateManagementError("안정 스냅샷 투영이 원본 장비 UID와 일치하지 않습니다")
            item = Drive(**payload) if payload["item_type"] == "drive" else Tape(**payload)
            inventory.append(item)
            parsed_items.append((index, item, _current_state(source)))

        evaluation = PostActionEvaluator(
            post_actions_config=config,
            selected_roles=selected_roles,
            config_dir=self.config_dir,
            user_database_path=self.database_path,
        ).evaluate(parsed_items, inventory)
        changes: list[dict[str, Any]] = []
        for change in evaluation.state_changes:
            source = source_by_uid.get(str(change.get("uid") or ""))
            if source is None:
                raise WarehouseStateManagementError("상태 관리 대상이 고정된 안정 스냅샷에 없습니다")
            enriched = dict(change)
            enriched["equipment"] = _equipment_uid(source)
            changes.append(enriched)
        return WarehouseStateManagementPlan(
            snapshot_id=snapshot_id,
            changes=tuple(changes),
            filter_summary=dict(evaluation.filter_summary),
            command_projection_allowed=True,
        )

    def plan_manual_changes(
        self,
        snapshot_id: int,
        targets: Mapping[str, str],
    ) -> WarehouseStateManagementPlan:
        """Prepare user-selected card edits against one fixed official snapshot.

        ``targets`` is keyed by the presentation UID (``nte-module-slot-serial``
        or ``nte-core-slot-serial``).  The UI only stores this small local diff;
        the authoritative current state remains the SQLite snapshot until save.
        """
        context = self.operation_context.with_values(snapshot_id=snapshot_id)
        with operation_scope(
            context,
            started_event="warehouse.manual_plan_started",
            succeeded_event="warehouse.manual_plan_succeeded",
            failed_event="warehouse.manual_plan_failed",
            message="창고 수동 상태 계획 생성",
            target_count=len(targets),
        ) as span:
            plan = self._plan_manual_changes(snapshot_id, targets)
            span.annotate(change_count=len(plan.changes))
            return plan

    def _plan_manual_changes(
        self,
        snapshot_id: int,
        targets: Mapping[str, str],
    ) -> WarehouseStateManagementPlan:
        if not isinstance(snapshot_id, int) or snapshot_id <= 0:
            raise WarehouseStateManagementError("저장할 수 있는 안정 가방 스냅샷이 없습니다")
        with self.dao_factory(self.database_path) as user_dao:
            if user_dao.current_inventory_snapshot_id() != snapshot_id:
                raise WarehouseStateManagementError("게임 가방이 갱신되었습니다. 창고가 자동으로 새로 고쳐진 뒤 다시 편집하세요")
            rows = user_dao.list_inventory_items(snapshot_id)
        by_uid = {_compat_uid(row): row for row in rows}
        changes: list[dict[str, Any]] = []
        for uid, target_state in targets.items():
            if target_state not in {"normal", "locked", "discarded"}:
                raise WarehouseStateManagementError(f"창고에 알 수 없는 대상 상태가 있습니다: {target_state}")
            row = by_uid.get(str(uid))
            if row is None:
                raise WarehouseStateManagementError("편집한 장비가 현재 안정 가방 스냅샷에 없습니다")
            if _current_state(row) != target_state:
                changes.append(
                    {
                        "uid": str(uid),
                        "target_state": target_state,
                        "equipment": _equipment_uid(row),
                    }
                )
        return WarehouseStateManagementPlan(
            snapshot_id=snapshot_id,
            changes=tuple(changes),
            filter_summary={},
            command_projection_allowed=True,
        )

    def apply(
        self,
        plan: WarehouseStateManagementPlan,
        *,
        confirmation_timeout: float = 20.0,
        progress_callback: Callable[[str], None] | None = None,
    ) -> WarehouseStateManagementResult:
        """Apply a reviewed plan and reconcile it against a later stable snapshot."""
        context = self.operation_context.with_values(snapshot_id=plan.snapshot_id)
        with operation_scope(
            context,
            started_event="warehouse.state_apply_started",
            succeeded_event="warehouse.state_apply_succeeded",
            failed_event="warehouse.state_apply_failed",
            message="창고 장비 상태 기록",
            change_count=len(plan.changes),
        ) as span:
            result = self._apply(
                plan,
                confirmation_timeout=confirmation_timeout,
                progress_callback=progress_callback,
            )
            span.annotate(
                summary=result.summary,
                after_snapshot_id=result.after_snapshot_id,
                verified=result.verified,
                verification_error=result.verification_error,
            )
            return result

    def _apply(
        self,
        plan: WarehouseStateManagementPlan,
        *,
        confirmation_timeout: float,
        progress_callback: Callable[[str], None] | None,
    ) -> WarehouseStateManagementResult:
        self._report_progress(
            progress_callback,
            "가방 동기화 상태와 코어 구성 요소 기능을 확인하는 중…",
        )
        try:
            self.state_writer.ensure_ready()
        except WarehouseStateWriteError as exc:
            raise WarehouseStateManagementError(str(exc)) from exc

        guard_token: object | None = None
        with self.dao_factory(self.database_path) as user_dao:
            current_snapshot_id = user_dao.current_inventory_snapshot_id()
            if current_snapshot_id != plan.snapshot_id:
                raise WarehouseStateManagementError("가방 스냅샷이 갱신되었습니다. 창고를 새로 고치고 관리 대상을 다시 확인하세요")
            current_rows = {
                (row["uid_slot"], row["uid_serial"]): row
                for row in user_dao.list_inventory_items(plan.snapshot_id)
            }
            action_snapshot_cursor: int | None = None
            # State RPCs can produce a scoped inventory response.  Keep the
            # current pointer pinned to snapshots containing this frozen full
            # UID set until the write-result reconciliation has completed.
            # This is the same session-local protection used by fast assembly;
            # the partial response is ignored rather than imported as current.
            begin_guard = getattr(self.sync_service, "begin_full_inventory_guard", None)
            if plan.changes and callable(begin_guard):
                frozen_uids = frozenset(current_rows)
                try:
                    guard_token = begin_guard(
                        frozen_uids,
                        source_snapshot_id=plan.snapshot_id,
                    )
                except TypeError:
                    # Keep old test doubles and older sync adapters usable;
                    # the production service receives the source snapshot and
                    # can persist only the observed state overlay.
                    guard_token = begin_guard(frozen_uids)
                cursor_reader = getattr(
                    self.sync_service, "scoped_equipment_snapshot_cursor", None,
                )
                if callable(cursor_reader):
                    action_snapshot_cursor = int(cursor_reader())
            applied_changes: list[dict[str, Any]] = []
            total_changes = len(plan.changes)
            try:
                for index, change in enumerate(plan.changes, 1):
                    equipment = dict(change["equipment"])
                    row = current_rows.get((equipment["slot"], equipment["serial"]))
                    if row is None:
                        raise WarehouseStateManagementError("대상 장비가 더 이상 현재 안정 스냅샷에 없습니다")
                    self._report_progress(
                        progress_callback,
                        f"게임에 {index}/{total_changes}번째 장비 상태를 제출하는 중…",
                    )
                    try:
                        self.state_writer.apply_one(
                            row,
                            str(change["target_state"]),
                            equipment,
                        )
                    except WarehouseStateWriteError as exc:
                        raise WarehouseStateManagementError(str(exc)) from exc
                    # Rule-generated changes already have the presentation UID,
                    # while manually-created plans do not.  Return one consistent
                    # form so the warehouse can update the affected card at once.
                    applied_change = dict(change)
                    applied_change["uid"] = str(applied_change.get("uid") or _compat_uid(row))
                    applied_changes.append(applied_change)
                if plan.command_projection_allowed and applied_changes:
                    projector = getattr(
                        user_dao, "apply_inventory_command_state_projection", None,
                    )
                    if callable(projector):
                        projector(
                            plan.snapshot_id,
                            self._command_state_projection(current_rows, applied_changes),
                        )
            except BaseException:
                if guard_token is not None:
                    finish_guard = getattr(self.sync_service, "finish_full_inventory_guard", None)
                    if callable(finish_guard) and finish_guard(
                        guard_token,
                        grace_seconds=90.0,
                    ):
                        # A timed-out state RPC can still yield a late partial
                        # event.  Keep filtering it from the current snapshot
                        # while allowing the runtime-state overlay to absorb
                        # only the observed known equipment rows.
                        raise
                    end_guard = getattr(self.sync_service, "end_full_inventory_guard", None)
                    if callable(end_guard):
                        end_guard(guard_token)
                raise

        if not plan.changes:
            self._report_progress(
                progress_callback,
                "현재 상태는 수정할 필요가 없습니다.",
            )
            return WarehouseStateManagementResult(
                before_snapshot_id=plan.snapshot_id,
                summary=summarize_state_changes([]),
                changes=(),
                after_snapshot_id=plan.snapshot_id,
                verified=True,
            )
        try:
            self._report_progress(
                progress_callback,
                "수정 명령을 모두 제출했으며 게임이 새 완전 가방 스냅샷을 생성하기를 기다리는 중…",
            )
            after_snapshot_id, verified, verification_error = (
                self._wait_for_confirmation(
                    plan.snapshot_id,
                    tuple(applied_changes),
                    timeout=confirmation_timeout,
                    progress_callback=progress_callback,
                )
            )
            if (
                plan.command_projection_allowed
                and after_snapshot_id is None
                and action_snapshot_cursor is not None
            ):
                replaced_snapshot_id = self._replace_with_action_snapshot(
                    plan,
                    tuple(applied_changes),
                    after_cursor=action_snapshot_cursor,
                )
                if replaced_snapshot_id is not None:
                    after_snapshot_id = replaced_snapshot_id
                    with self.dao_factory(self.database_path) as user_dao:
                        rows = user_dao.list_inventory_items(replaced_snapshot_id)
                    verified = self._count_state_mismatches(rows, tuple(applied_changes)) == 0
                    verification_error = None if verified else verification_error
        finally:
            reduction_reader = getattr(
                self.sync_service, "guard_observed_inventory_reduction", None,
            )
            inventory_reduction_observed = bool(
                guard_token is not None
                and callable(reduction_reader)
                and reduction_reader(guard_token)
            )
            if guard_token is not None:
                end_guard = getattr(self.sync_service, "end_full_inventory_guard", None)
                if callable(end_guard):
                    end_guard(guard_token)
        return WarehouseStateManagementResult(
            before_snapshot_id=plan.snapshot_id,
            summary=summarize_state_changes(list(plan.changes)),
            changes=tuple(applied_changes),
            after_snapshot_id=after_snapshot_id,
            verified=verified,
            verification_error=verification_error,
            inventory_reduction_observed=inventory_reduction_observed,
        )

    def _wait_for_confirmation(
        self,
        before_snapshot_id: int,
        changes: tuple[dict[str, Any], ...],
        *,
        timeout: float,
        progress_callback: Callable[[str], None] | None,
    ) -> tuple[int | None, bool, str | None]:
        """Wait through intermediate snapshots until every accepted change matches."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        cursor = before_snapshot_id
        latest_snapshot_id: int | None = None
        remaining_mismatches = len(changes)

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if latest_snapshot_id is None:
                    message = (
                        "제한 시간 내에 수정 후의 새 가방 스냅샷을 받지 못했습니다."
                        "현재 nte-core 프로토콜은 게임에 새로 고침을 요청할 수 없어"
                        "게임이 이후에 완전한 가방 데이터를 생성할 때까지 기다려야 합니다"
                    )
                else:
                    message = (
                        f"새 스냅샷 #{latest_snapshot_id}을(를) 받았지만 아직"
                        f"{remaining_mismatches}개 상태가 확인되지 않았습니다"
                    )
                return latest_snapshot_id, False, message
            try:
                state = self.sync_service.wait_for_snapshot(
                    after_snapshot_id=cursor,
                    timeout=remaining,
                )
            except TimeoutError:
                if latest_snapshot_id is None:
                    message = (
                        "제한 시간 내에 수정 후의 새 가방 스냅샷을 받지 못했습니다."
                        "현재 nte-core 프로토콜은 게임에 새로 고침을 요청할 수 없어"
                        "게임이 이후에 완전한 가방 데이터를 생성할 때까지 기다려야 합니다"
                    )
                else:
                    message = (
                        f"새 스냅샷 #{latest_snapshot_id}을(를) 받았지만 아직"
                        f"{remaining_mismatches}개 상태가 확인되지 않았습니다"
                    )
                return latest_snapshot_id, False, message
            except Exception as exc:
                return (
                    latest_snapshot_id,
                    False,
                    f"새 가방 스냅샷 대기 중 동기화 서비스 예외 ({type(exc).__name__})",
                )

            snapshot_id = getattr(state, "last_snapshot_id", None)
            if (
                not isinstance(snapshot_id, int)
                or snapshot_id <= cursor
            ):
                return (
                    latest_snapshot_id,
                    False,
                    "동기화 서비스가 증가하는 새 가방 스냅샷 번호를 반환하지 않았습니다",
                )
            latest_snapshot_id = snapshot_id
            cursor = snapshot_id
            self._report_progress(
                progress_callback,
                f"새 스냅샷 #{snapshot_id}을(를) 받아 수정 결과를 대조하는 중…",
            )
            with self.dao_factory(self.database_path) as user_dao:
                rows = user_dao.list_inventory_items(snapshot_id)
            remaining_mismatches = self._count_state_mismatches(
                rows,
                changes,
            )
            if remaining_mismatches == 0:
                self._report_progress(
                    progress_callback,
                    f"새 스냅샷 #{snapshot_id}에서 모든 수정이 확인되었습니다.",
                )
                return snapshot_id, True, None
            self._report_progress(
                progress_callback,
                f"스냅샷 #{snapshot_id}에 아직 {remaining_mismatches}개가 확인되지 않아 계속 기다립니다…",
            )

    @staticmethod
    def _command_state_projection(
        current_rows: Mapping[tuple[int, int], Mapping[str, Any]],
        changes: list[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Apply only submitted one-key state targets over known snapshot rows."""

        projected: list[dict[str, Any]] = []
        for change in changes:
            equipment = change.get("equipment")
            if not isinstance(equipment, Mapping):
                continue
            pair = (int(equipment.get("slot") or 0), int(equipment.get("serial") or 0))
            row = current_rows.get(pair)
            if row is None:
                continue
            target = str(change.get("target_state") or "")
            if target not in {"normal", "locked", "discarded"}:
                continue
            item = dict(row)
            item["uid"] = {"slot": pair[0], "serial": pair[1]}
            item["locked"] = target == "locked"
            item["discarded"] = target == "discarded"
            projected.append(item)
        return projected

    def _replace_with_action_snapshot(
        self,
        plan: WarehouseStateManagementPlan,
        changes: tuple[dict[str, Any], ...],
        *,
        after_cursor: int,
    ) -> int | None:
        """Replace inventory only from this action's packet covering all targets."""

        waiter = getattr(self.sync_service, "wait_for_action_inventory_snapshot", None)
        if not callable(waiter):
            return None
        required_uids = frozenset(
            (int(change["equipment"]["slot"]), int(change["equipment"]["serial"]))
            for change in changes
            if isinstance(change.get("equipment"), Mapping)
        )
        if not required_uids:
            return None
        try:
            packet = waiter(required_uids, after_cursor=after_cursor, timeout=0.0)
        except TimeoutError:
            return None
        with self.dao_factory(self.database_path) as user_dao:
            if user_dao.current_inventory_snapshot_id() != plan.snapshot_id:
                return None
            return int(user_dao.import_inventory_snapshot(packet, source="nte_core"))

    @staticmethod
    def _report_progress(
        callback: Callable[[str], None] | None,
        message: str,
    ) -> None:
        if callback is not None:
            callback(message)

    @staticmethod
    def _count_state_mismatches(
        rows: list[dict[str, Any]],
        changes: tuple[dict[str, Any], ...],
    ) -> int:
        by_uid = {
            (row.get("uid_slot"), row.get("uid_serial")): row
            for row in rows
        }
        mismatches = 0
        for change in changes:
            equipment = change.get("equipment")
            if not isinstance(equipment, Mapping):
                mismatches += 1
                continue
            row = by_uid.get(
                (equipment.get("slot"), equipment.get("serial"))
            )
            if (
                row is None
                or _current_state(row) != str(change.get("target_state") or "")
            ):
                mismatches += 1
        return mismatches
