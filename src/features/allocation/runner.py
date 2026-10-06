# 执行分配任务并处理保存和归档。
"""MainWindow methods for allocation."""

from __future__ import annotations

import copy
import shutil
import threading
from concurrent.futures import CancelledError
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout

from src.app.theme import current_style_sheet
from src.app.workers import WorkerThread
from src.integrations.global_hotkeys import GlobalHotkeyManager
from src.services.allocation_comparison_scoring import persist_comparison_diff
from src.services.legacy_allocation_comparison_service import (
    freeze_legacy_slot_comparisons, refresh_legacy_slot_comparisons, single_slot_comparison_diffs,
)
from src.optimizer.contracts import (
    DIFF_CHANGED,
    EQUIP_IS_CHANGED,
    EQUIP_UID,
    PLAN_ASSIGNED_TAPE,
    PLAN_BLUEPRINT,
    PLAN_CHANGED_UIDS,
    PLAN_VALID,
    ROLE_BLUEPRINT_LAYOUT,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    plan_drives,
)
from src.services.sqlite_allocation_inventory import SqliteAllocationInventory
from src.services.allocation_filter_settings import (
    AllocationFilterSettings,
    filter_allocation_candidates,
)
from src.services.allocation_lock_service import (
    AllocationLockSnapshot,
    build_allocation_lock_snapshot,
    filter_allocation_request_for_locks,
    selected_fully_locked_roles,
)
from src.services.saved_state_loadout_bridge import (
    resolve_character_id_for_allocation_role,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.observability.redaction import format_local_exception
from src.utils.logger import logger

__all__ = [
    "_run_allocation",
    "_start_allocation_worker",
    "_confirm_unsaved_allocation_before_recompute",
    "_on_done",
    "_on_exec_error",
    "_save_alloc",
    "_archive_pending_screenshots",
    "AllocationRunResult",
]


@dataclass(frozen=True)
class AllocationRunResult:
    """Worker result bound to the stable snapshot and lock state it consumed."""

    plans: dict[str, Any]
    snapshot_id: int
    lock_snapshot: AllocationLockSnapshot
    selected_locked_role_names: frozenset[str]
    static_database_path: Path
    static_dataset_id: str
    static_file_identity: tuple[int, int]
    context_identity: tuple[int | None, str | None, Path]
    run_id: int | None = None
    comparisons: dict = field(default_factory=dict)


def _allocation_context_identity(window: Any) -> tuple[int | None, str | None, Path]:
    context = getattr(window, "app_context", None)
    if context is None:
        return (None, None, _allocation_paths(window)[0])
    return (
        int(context.generation),
        str(context.account.active_account_id),
        Path(context.account.user_database_path),
    )


def _allocation_paths(window: Any) -> tuple[Path, Path, Path, Path, Path]:
    context = getattr(window, "app_context", None)
    if context is None:
        database_path = getattr(window, "user_database_path", None)
        config_dir = getattr(window, "config_dir", None)
        user_config_dir = getattr(window, "user_config_dir", None)
        screenshot_dir = getattr(window, "screenshot_dir", None)
        static_database_path = getattr(window, "static_database_path", None)
        if any(
            value is None
            for value in (
                database_path,
                config_dir,
                user_config_dir,
                screenshot_dir,
                static_database_path,
            )
        ):
            raise RuntimeError("분배 기능에 AppContext 또는 명시적 경로 의존성이 없습니다")
        assert database_path is not None
        assert config_dir is not None
        assert user_config_dir is not None
        assert screenshot_dir is not None
        assert static_database_path is not None
        return (
            Path(database_path),
            Path(config_dir),
            Path(user_config_dir),
            Path(screenshot_dir),
            Path(static_database_path),
        )
    return (
        Path(context.account.user_database_path),
        Path(context.paths.config_dir),
        Path(context.account.user_config_dir),
        Path(context.account.screenshot_dir),
        Path(context.paths.equipment_allocation_database_path),
    )


def _run_allocation(
    self: Any,
    strat: str,
    sel: list[str],
    cs: dict[str, Any],
    tape_main_filters: dict[str, Any] | None = None,
    crit_priority_modes: dict[str, Any] | None = None,
    set_effect_modes: dict[str, Any] | None = None,
    priority_groups: Any = None,
    crit_rate_caps: dict[str, Any] | None = None,
    crit_rate_baselines: dict[str, Any] | None = None,
    custom_weapons: dict[str, Any] | None = None,
    filter_settings: AllocationFilterSettings | None = None,
    blueprint_combo_limit: int = 2000,
    cancel_check=None,
    *, expected_context_identity: tuple[int | None, str | None, Path] | None = None,
    expected_run_id: int | None = None,
) -> Any:
    try:
        context_identity = expected_context_identity or _allocation_context_identity(self)
        if _allocation_context_identity(self) != context_identity:
            raise CancelledError("계산 계정이 전환되어 이전 작업을 폐기합니다")
        database_path, config_dir, user_config_dir, _, static_database_path = _allocation_paths(self)
        logger.info(f"분배 계산 시작: 전략={strat}, 캐릭터={sel}")
        if not database_path.is_file():
            raise RuntimeError("아직 공식 가방 데이터가 없습니다. 먼저 가방 동기화를 완료하고 안정 스냅샷을 생성하세요.")
        with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
            static_dataset_id = str(static_dao.summary()["dataset"]["dataset_id"])
            static_stat = static_database_path.stat()
            static_file_identity = (static_stat.st_size, static_stat.st_mtime_ns)
            snapshot_id = user_dao.current_inventory_snapshot_id()
            if snapshot_id is None:
                raise RuntimeError("아직 안정 가방 스냅샷이 없습니다. 먼저 홈에서 가방 동기화를 시작하고 게임에 접속하세요.")
            projection = SqliteAllocationInventory(user_dao, static_dao).build(snapshot_id)
            lock_snapshot = build_allocation_lock_snapshot(
                user_dao,
                inventory_snapshot_id=projection.snapshot_id,
            )
        frozen_filter_settings = filter_settings or AllocationFilterSettings()
        filtered_items = filter_allocation_candidates(
            projection.items,
            frozen_filter_settings,
        )
        unlocked_sel, unlocked_priority_groups = filter_allocation_request_for_locks(
            sel,
            priority_groups,
            lock_snapshot,
        )
        selected_locked_role_names = selected_fully_locked_roles(sel, lock_snapshot)
        allocation_options = {
            "tape_main_filters": tape_main_filters or {},
            "crit_priority_modes": crit_priority_modes or {},
            "set_effect_modes": set_effect_modes or {},
            "priority_groups": unlocked_priority_groups,
            "crit_rate_caps": crit_rate_caps or {},
            "crit_rate_baselines": crit_rate_baselines or {},
            "custom_weapons": custom_weapons or {},
            "blueprint_combo_limit": blueprint_combo_limit,
            "cancel_check": cancel_check,
        }
        logger.info(
            f"공식 가방 안정 스냅샷 {projection.snapshot_id}(으)로 계산:"
            f"후보 {len(filtered_items)}/{len(projection.items)}개"
            f"(그중 폐기 표시 {projection.discarded_count}개, 필터 조건에 맞으면 계산에 계속 참여)"
        )
        if selected_locked_role_names:
            logger.info(
                f"세팅 잠금으로 선택 캐릭터 {len(selected_locked_role_names)}명 유지, "
                f"장비 {len(lock_snapshot.reserved_uids)}개 제외:"
                f"{'、'.join(sorted(selected_locked_role_names))}"
            )
        from src.app.facade import NTEAppFacade

        a = NTEAppFacade(
            config_dir=str(config_dir),
            user_config_dir=str(user_config_dir),
            user_database_path=database_path,
            allocation_static_database_path=static_database_path,
        )
        comparisons = {}

        def freeze_comparisons(request, scorer):
            def checkpoint():
                if (cancel_check is not None and cancel_check()) or _allocation_context_identity(self) != context_identity:
                    raise CancelledError("분배 계산이 취소되었거나 계정이 전환되었습니다")
            comparisons.update(freeze_legacy_slot_comparisons(
                database_path, static_database_path, request, scorer,
                snapshot_id=projection.snapshot_id, checkpoint=checkpoint,
            ))

        if unlocked_sel:
            if cancel_check is not None and cancel_check():
                raise CancelledError("분배 계산이 취소되었습니다")
            if _allocation_context_identity(self) != context_identity:
                raise CancelledError("계산 계정이 전환되어 이전 작업을 폐기합니다")
            fp, _ = a.execute_allocation_inventory(
                list(filtered_items),
                unlocked_sel,
                cs,
                strat,
                locked_uids=set(lock_snapshot.reserved_uids),
                allocation_observer=freeze_comparisons,
                **allocation_options,
            )
        else:
            fp = {}
        latest_stat = static_database_path.stat()
        if (latest_stat.st_size, latest_stat.st_mtime_ns) != static_file_identity:
            raise RuntimeError("계산 도중 정적 데이터셋이 업데이트되었습니다. 계산을 다시 실행하세요.")
        if _allocation_context_identity(self) != context_identity:
            raise CancelledError("계산 계정이 전환되어 이전 작업을 폐기합니다")
        logger.info(f"분배 계산 완료: result_type={type(fp).__name__}")
        return AllocationRunResult(
            plans=fp,
            snapshot_id=projection.snapshot_id,
            lock_snapshot=lock_snapshot,
            selected_locked_role_names=selected_locked_role_names,
            static_database_path=static_database_path,
            static_dataset_id=static_dataset_id,
            static_file_identity=static_file_identity,
            context_identity=context_identity,
            run_id=expected_run_id,
            comparisons=refresh_legacy_slot_comparisons(comparisons, fp),
        )
    except Exception as e:
        logger.error(f"allocation.run_failed | {format_local_exception(e)}")
        raise


def _start_allocation_worker(self: Any) -> None:
    logger.info("분배 작업 스레드 시작...")
    context_identity = self._pending_allocation_context_identity
    run_id = self._pending_run_id
    frozen_args = copy.deepcopy((
        self._pending_strat,
        self._pending_sel,
        self._pending_cs,
        self._pending_tape_main_filters,
        self._pending_crit_priority_modes,
        self._pending_set_effect_modes,
        self._pending_priority_groups,
        self._pending_crit_rate_caps,
        self._pending_crit_rate_baselines,
        self._pending_custom_weapons,
        self._pending_filter_settings,
        self._pending_blueprint_combo_limit,
    ))
    cancel_check = self._cancel_event.is_set
    self._worker = WorkerThread(
        target=lambda: self._run_allocation(
            *frozen_args,
            cancel_check=cancel_check,
            expected_context_identity=context_identity,
            expected_run_id=run_id,
        ),
        parent=self,
    )
    self._worker.result_ready.connect(self._on_done)
    self._worker.error.connect(
        lambda error: self._on_exec_error(error)
        if (_allocation_context_identity(self) == context_identity
            and self._pending_run_id == run_id)
        else logger.info("이전 계정의 분배 작업 오류 콜백을 폐기했습니다")
    )
    self._worker.start()
    logger.info("분배 스레드가 시작되었습니다")


def _persistable_plan_diff(
    role_diff: dict[str, Any] | None,
) -> dict[str, Any]:
    """Convert in-memory diff sets to JSON-compatible plan payload data."""

    return persist_comparison_diff(role_diff)


def _plan_changed_uids(
    plan: dict[str, Any],
    role_diff: dict[str, Any] | None,
) -> set[str]:
    """Collect change markers only when replacing a non-empty saved slot."""

    if not bool((role_diff or {}).get(DIFF_CHANGED)):
        return set()
    changed = {
        str(uid)
        for uid in (plan.get(PLAN_CHANGED_UIDS, set()) or ())
        if uid
    }
    for item in [plan.get(PLAN_ASSIGNED_TAPE), *plan_drives(plan)]:
        value = (
            item.get(EQUIP_IS_CHANGED)
            if isinstance(item, dict)
            else getattr(item, EQUIP_IS_CHANGED, False)
        )
        uid = (
            item.get(EQUIP_UID)
            if isinstance(item, dict)
            else getattr(item, EQUIP_UID, "")
        )
        if value and uid:
            changed.add(str(uid))
    return changed


def _plan_assignment_scores(
    role_name: str,
    plan: dict[str, Any],
) -> dict[str, float]:
    """Freeze each selected item's role score beside the aggregate score."""

    result: dict[str, float] = {}
    for item in [plan.get(PLAN_ASSIGNED_TAPE), *plan_drives(plan)]:
        if item is None:
            continue
        uid = str(
            item.get(EQUIP_UID, "")
            if isinstance(item, dict)
            else getattr(item, EQUIP_UID, "")
        )
        role_scores = (
            item.get("role_scores", {})
            if isinstance(item, dict)
            else getattr(item, "role_scores", {})
        ) or {}
        if uid:
            result[uid] = float(role_scores.get(role_name, 0.0) or 0.0)
    return result


def _confirm_unsaved_allocation_before_recompute(self: Any) -> bool:
    if not self.final_plan or not self._allocation_dirty:
        return True
    if self._ui_preferences.get("skip_unsaved_allocation_prompt"):
        self._allocation_dirty = False
        return True
    dlg = QDialog(getattr(self, "dialog_parent", None))
    dlg.setWindowTitle("현재 장비 세팅이 아직 저장되지 않음")
    dlg.setStyleSheet(current_style_sheet())
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(18, 18, 18, 18)
    layout.setSpacing(14)
    msg = QLabel("계산을 다시 실행하면 현재 계산 결과를 덮어씁니다. 먼저 현재 장비 세팅을 저장할까요?")
    msg.setWordWrap(True)
    layout.addWidget(msg)
    row = QHBoxLayout()
    row.setSpacing(10)
    dont_btn = QPushButton("다시 묻지 않기")
    dont_btn.setObjectName("btnDanger")
    skip_btn = QPushButton("저장 안 함")
    save_btn = QPushButton("저장")
    save_btn.setObjectName("btnPrimary")
    row.addWidget(dont_btn)
    row.addWidget(skip_btn)
    row.addWidget(save_btn)
    layout.addLayout(row)
    choice: dict[str, str | None] = {"value": None}

    def select(value: str) -> None:
        choice["value"] = value
        dlg.accept()

    dont_btn.clicked.connect(lambda: select("never"))
    skip_btn.clicked.connect(lambda: select("skip"))
    save_btn.clicked.connect(lambda: select("save"))
    dlg.exec()
    if choice["value"] == "save":
        return self._save_alloc(show_message=False)
    if choice["value"] == "never":
        self._ui_preferences["skip_unsaved_allocation_prompt"] = True
        self._save_ui_preferences()
        self._allocation_dirty = False
        return True
    if choice["value"] == "skip":
        self._allocation_dirty = False
        return True
    return False


def _on_done(self: Any, r: Any) -> None:
    try:
        logger.info(
            f"_on_done 결과 수신: type={type(r).__name__}, keys={list(r.keys()) if isinstance(r, dict) else 'N/A'}"
        )
        if not isinstance(r, AllocationRunResult):
            raise RuntimeError("분배 스레드가 스냅샷에 바인딩되지 않은 결과를 반환했습니다")
        if (r.context_identity != _allocation_context_identity(self)
            or r.run_id != getattr(self, "_pending_run_id", None)):
            logger.info("만료된 분배 작업의 결과 콜백을 폐기했습니다")
            return
        self._hotkey_manager.stop(owner="allocation")
        current_static = _allocation_paths(self)[4]
        current_stat = current_static.stat()
        if current_static != r.static_database_path or (
            current_stat.st_size, current_stat.st_mtime_ns
        ) != r.static_file_identity:
            _on_exec_error(self, "계산 도중 정적 데이터셋이 업데이트되었습니다. 계산을 다시 실행하세요.")
            return
        self.final_plan = r.plans
        self._pending_allocation_snapshot_id = r.snapshot_id
        self._pending_allocation_static_identity = (
            r.static_database_path, r.static_dataset_id, r.static_file_identity
        )
        self._allocation_lock_snapshot = r.lock_snapshot
        self._selected_locked_role_names = r.selected_locked_role_names
        self.btn_run.setEnabled(True)
        self.btn_run.setText("⚡  계산 시작")
        self._allocation_custom_weapons = dict(getattr(self, "_pending_custom_weapons", {}) or {})
        self._allocation_frozen_comparisons = r.comparisons
        self.allocation_plan_diff = single_slot_comparison_diffs(r.comparisons)
        self._allocation_dirty = bool(self.final_plan)
        self._render_results(self.final_plan)
        logger.info("_render_results 완료")
    except Exception as e:
        logger.error(f"allocation.render_failed | {format_local_exception(e)}")
        QMessageBox.critical(self.dialog_parent, "렌더링 실패", f"{e}")


def _on_exec_error(self: Any, err: str) -> None:
    self._hotkey_manager.stop(owner="allocation")
    self.btn_run.setEnabled(True)
    self.btn_run.setText("⚡  계산 시작")
    if err == "작업이 취소되었습니다":
        logger.info("분배 계산이 전역 중지 키로 취소되었습니다")
        return
    QMessageBox.critical(
        self.dialog_parent,
        "계산 실패",
        f"오류 발생:\n{err}",
    )


def _select_allocation_save_slots(
    self: Any,
    user_dao: UserDataDao,
    static_dao: StaticGameDataDao,
    snapshot_id: int,
) -> dict[str, tuple[int, int | None]] | None:
    """Choose slots without creating an empty slot before the save transaction."""

    targets: dict[str, tuple[int, int | None]] = {}
    for role_name, plan in self.final_plan.items():
        if not isinstance(plan, dict) or not plan.get(PLAN_VALID):
            continue
        character_id = resolve_character_id_for_allocation_role(
            role_name, static_dao, user_dao, snapshot_id=snapshot_id
        )
        slots = user_dao.list_loadout_slots(character_id)
        if not slots:
            targets[role_name] = (character_id, None)
            continue
        if len(slots) == 1:
            slot = slots[0]
        else:
            labels = [
                str(slot["slot_name"])
                + ("(잠김)" if (slot.get("current_plan") or {}).get("allocation_locked") else "")
                for slot in slots
            ]
            selected, accepted = QInputDialog.getItem(
                self.dialog_parent,
                "저장 슬롯 선택",
                f"[{role_name}]의 계산 결과 저장 위치:",
                labels,
                0,
                False,
            )
            if not accepted:
                return None
            slot = slots[labels.index(selected)]
        if (slot.get("current_plan") or {}).get("allocation_locked"):
            QMessageBox.warning(self.dialog_parent, "방안 저장", f"[{role_name}]에서 선택한 슬롯이 잠겨 있어 덮어쓸 수 없습니다.")
            return None
        targets[role_name] = (character_id, int(slot["slot_id"]))
    return targets


def _save_alloc(self: Any, show_message: bool = True) -> bool:
    from src.features.allocation.save_workflow import save_allocation
    return save_allocation(self, show_message=show_message)


def _role_state_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """构造仅存在于内存中的转换对象；不读写旧 JSON。"""
    board = []
    for row in plan.get(PLAN_BLUEPRINT, {}).get("board", []) or []:
        board.append(["XX" if cell == -1 else "0" if cell == 0 else str(cell) for cell in row])
    tape = plan.get(PLAN_ASSIGNED_TAPE)
    return {
        ROLE_BLUEPRINT_LAYOUT: board,
        ROLE_EQUIPPED_TAPE: {EQUIP_UID: tape.uid} if tape is not None else None,
        ROLE_EQUIPPED_DRIVES: [{EQUIP_UID: drive.uid, "shape_id": drive.shape_id} for drive in plan_drives(plan)],
    }


def _archive_pending_screenshots(self: Any) -> int:
    paths = list(getattr(self, "_pending_archive_paths", []) or [])
    if not paths:
        return 0
    archive_dir = _allocation_paths(self)[3] / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_count = 0
    for src in paths:
        src_path = Path(src)
        if not src_path.exists():
            continue
        dst = archive_dir / src_path.name
        base = dst.with_suffix("")
        ext = dst.suffix
        suffix = 1
        while dst.exists():
            dst = Path(f"{base}_{suffix}{ext}")
            suffix += 1
        shutil.move(str(src_path), str(dst))
        archived_count += 1
    self._pending_archive_paths = []
    if archived_count:
        logger.success(f"저장된 세팅의 스크린샷 {archived_count}장을 보관했습니다.")
    return archived_count


class AllocationController(QObject):
    """Own one calculation worker, its frozen request, and save state."""

    def __init__(
        self,
        *,
        app_context: Any,
        dialog_parent: QObject,
        equipment_presentation: Any,
        preferences_provider: Callable[[], dict[str, Any]],
        save_preferences: Callable[[], None],
        refresh_roles: Callable[[], None],
        refresh_equipment: Callable[[], None],
        hotkey_manager: GlobalHotkeyManager,
    ) -> None:
        super().__init__(dialog_parent)
        self.app_context = app_context
        self.dialog_parent = dialog_parent
        self._equipment_presentation = equipment_presentation
        self._preferences_provider = preferences_provider
        self._save_preferences_callback = save_preferences
        self._refresh_roles_callback = refresh_roles
        self._refresh_equipment_callback = refresh_equipment
        self._hotkey_manager = hotkey_manager
        self._cancel_event = threading.Event()
        self.btn_run: QPushButton | None = None
        self._worker: WorkerThread | None = None
        self._save_worker: WorkerThread | None = None
        self._saving = False
        self.btn_save: QPushButton | None = None
        self.final_plan: dict = {}
        self._allocation_frozen_comparisons: dict = {}
        self.allocation_plan_diff: dict = {}
        self._allocation_dirty = False
        self._pending_allocation_snapshot_id: int | None = None
        self._pending_allocation_static_identity: tuple[Path, str, tuple[int, int]] | None = None
        self._allocation_lock_snapshot: AllocationLockSnapshot | None = None
        self._selected_locked_role_names: frozenset[str] = frozenset()
        self._pending_archive_paths: list[Path] = []
        self._pending_strat = ""
        self._pending_sel: list[str] = []
        self._pending_cs: dict[str, Any] = {}
        self._pending_tape_main_filters: dict[str, Any] = {}
        self._pending_crit_priority_modes: dict[str, Any] = {}
        self._pending_set_effect_modes: dict[str, Any] = {}
        self._pending_priority_groups: Any = None
        self._pending_crit_rate_caps: dict[str, Any] = {}
        self._pending_crit_rate_baselines: dict[str, Any] = {}
        self._pending_custom_weapons: dict[str, Any] = {}
        self._pending_allocation_context_identity: tuple[int | None, str | None, Path] | None = None
        self._run_sequence = 0
        self._pending_run_id: int | None = None
        self._pending_filter_settings = AllocationFilterSettings()
        self._pending_blueprint_combo_limit = 2000
        self._allocation_custom_weapons: dict[str, Any] = {}
        self._ui_preferences: dict[str, Any] = {}

    def bind_run_button(self, button: QPushButton) -> None:
        self.btn_run = button

    def bind_save_button(self, button: QPushButton) -> None:
        self.btn_save = button

    def stop_save(self) -> None:
        self._cancel_event.set()
        if self._save_worker is not None and self._save_worker.isRunning():
            self._save_worker.wait(5000)

    def start(
        self,
        *,
        strategy: str,
        selected_roles: list[str],
        custom_sets: dict[str, Any],
        tape_main_filters: dict[str, Any],
        crit_priority_modes: dict[str, Any],
        set_effect_modes: dict[str, Any],
        priority_groups: Any,
        crit_rate_caps: dict[str, Any],
        crit_rate_baselines: dict[str, Any],
        custom_weapons: dict[str, Any],
        filter_settings: AllocationFilterSettings,
        blueprint_combo_limit: int = 2000,
    ) -> None:
        if self.btn_run is None:
            raise RuntimeError("allocation run button has not been bound")
        if self.is_running():
            raise RuntimeError("이미 분배 계산 작업이 실행 중입니다")
        self._pending_strat = strategy
        self._pending_sel = selected_roles
        self._pending_cs = custom_sets
        self._pending_tape_main_filters = tape_main_filters
        self._pending_crit_priority_modes = crit_priority_modes
        self._pending_set_effect_modes = set_effect_modes
        self._pending_priority_groups = priority_groups
        self._pending_crit_rate_caps = crit_rate_caps
        self._pending_crit_rate_baselines = crit_rate_baselines
        self._pending_custom_weapons = custom_weapons
        filter_settings.validate()
        self._pending_filter_settings = filter_settings
        self._pending_blueprint_combo_limit = int(blueprint_combo_limit)
        if self._pending_blueprint_combo_limit < 1:
            raise ValueError("청사진 조합 수는 양의 정수여야 합니다")
        self._pending_allocation_context_identity = _allocation_context_identity(self)
        self._run_sequence += 1
        self._pending_run_id = self._run_sequence
        self._cancel_event = threading.Event()
        self._hotkey_manager.start(owner="allocation", on_stop=self.cancel)
        _start_allocation_worker(self)

    def cancel(self) -> None:
        """Handle the shared F12 stop key at optimizer safe points."""

        self._cancel_event.set()

    def confirm_recompute(self) -> bool:
        self._ui_preferences = self._preferences_provider()
        return bool(_confirm_unsaved_allocation_before_recompute(self))

    def save(self, show_message: bool = True) -> bool:
        return self._save_alloc(show_message=show_message)

    def is_running(self) -> bool:
        return self._saving or bool(self._worker is not None and self._worker.isRunning())

    def clear_preview(self) -> None:
        """Discard a displayed calculation without changing persisted plans or inputs."""

        self.final_plan = {}
        self._allocation_frozen_comparisons = {}
        self.allocation_plan_diff = {}
        self._allocation_dirty = False
        self._pending_allocation_snapshot_id = None
        self._pending_allocation_static_identity = None
        self._allocation_lock_snapshot = None
        self._selected_locked_role_names = frozenset()
        self._allocation_custom_weapons = {}
        self._equipment_presentation.clear()

    def reset_account_state(self) -> None:
        self.final_plan = {}
        self._allocation_frozen_comparisons = {}
        self.allocation_plan_diff = {}
        self._allocation_dirty = False
        self._pending_allocation_snapshot_id = None
        self._pending_allocation_static_identity = None
        self._allocation_lock_snapshot = None
        self._selected_locked_role_names = frozenset()
        self._pending_allocation_context_identity = None
        self._pending_run_id = None
        self._pending_filter_settings = AllocationFilterSettings()
        self._cancel_event.set()
        self._hotkey_manager.stop(owner="allocation")
        self._equipment_presentation.clear()

    def _run_allocation(self, *args: Any, **kwargs: Any) -> Any:
        return _run_allocation(self, *args, **kwargs)

    def _on_done(self, result: Any) -> None:
        _on_done(self, result)

    def _on_exec_error(self, error: str) -> None:
        _on_exec_error(self, error)

    def _render_results(self, plan: dict) -> None:
        self._equipment_presentation.set_plan_context(
            final_plan=plan,
            plan_diff=self.allocation_plan_diff,
            snapshot_id=self._pending_allocation_snapshot_id,
            strategy=self._pending_strat,
            custom_weapons=self._pending_custom_weapons,
            locked_role_names=self._selected_locked_role_names,
        )
        self._equipment_presentation.render(plan)

    def _save_alloc(self, show_message: bool = True) -> bool:
        self.allocation_plan_diff = dict(
            self._equipment_presentation.allocation_plan_diff or {}
        )
        return bool(_save_alloc(self, show_message=show_message))

    def _save_ui_preferences(self) -> None:
        self._save_preferences_callback()

    def _refresh_my_role(self) -> None:
        self._refresh_roles_callback()

    def _refresh_equip(self) -> None:
        self._refresh_equipment_callback()
