# 编排极速装配及其与游戏界面自动装配之间的入口路由。
"""Fast equipment apply controller and assembly-mode routing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMessageBox, QProgressBar, QProgressDialog

from src.app.workers import WorkerThread
from src.observability.context import OperationContext
from src.integrations.nte_core import is_mods_plugin_unavailable_error
from src.services.dwmapi_diagnostics import probe_equipment_pipe
from src.features.inventory.equipment_assembly_dialogs import (
    assembly_report_dialog as _assembly_report_dialog,
)
from src.features.inventory.fast_apply_completion_summary import (
    build_fast_apply_completion_summary,
)
from src.services.equipment_apply_service import EquipmentApplyService
from src.services.bulk_equipment_apply_service import BulkEquipmentApplyService
from src.services.inventory_source_capabilities import is_visual_inventory_source
from src.services.loadout_slot_selection_service import LoadoutSlotSelectionService
from src.storage.sqlite.user_data_dao import UserDataDao
from .equipment_automatic_assembly_controller import (
    _account_database_path,
    _preview_automatic_assemble_all_roles,
    _preview_automatic_assemble_role,
    _return_to_equipment_after_assembly,
)
from .equipment_slot_selection_dialog import select_assembly_slot_ids


__all__ = [
    "_preview_assemble_role",
    "_preview_fast_assemble_all_roles",
    "_preview_automatic_assemble_all_roles",
    "_assembly_report_dialog",
    "_return_to_equipment_after_assembly",
]


def _is_equipment_plugin_unavailable_error(error: object) -> bool:
    """识别核心已启动但游戏内装备插件桥接不可用的不可重试错误。"""

    return is_mods_plugin_unavailable_error(error)


def _equipment_failure_details(
    failure_kind: str,
    error: object,
    *,
    pipe_probe: dict[str, Any] | None = None,
) -> str:
    """Render one concrete failure category without conflating pipe states."""

    message = str(error or "알 수 없는 오류")
    if failure_kind == "plugin_unavailable":
        probe = pipe_probe if pipe_probe is not None else probe_equipment_pipe()
        state = str(probe.get("state") or "error")
        if state == "missing":
            return (
                "현재 탐지 결과 장비 플러그인 명명된 파이프가 없습니다."
                "보통 DLL/스크립트 로드 미완료, Viewport Tick 미실행 또는 IPC 버전 불일치를 뜻합니다."
            )
        if state == "busy":
            return "현재 탐지 결과 명명된 파이프는 있지만 연결 인스턴스가 여전히 사용 중입니다."
        if state == "available":
            return (
                "현재 탐지 결과 명명된 파이프가 있습니다. 이전 요청은 파이프가 잠시 사용 불가였거나 응답 대기 시간 초과였을 가능성이 높으며,"
                "지속적인 파이프 누락은 아닙니다."
            )
        if state == "access_denied":
            return "현재 탐지 결과 명명된 파이프 접근이 거부되었습니다. 프로그램과 게임의 권한 수준을 확인하세요."
        return f"장비 플러그인 채널을 사용할 수 없습니다. 현재 파이프 탐지 결과: {probe.get('message') or message}"
    if failure_kind == "plugin_busy":
        return "장비 플러그인 큐가 6회 순차 백오프 후에도 바쁜 상태라 이번 요청은 실행 큐에 들어가지 않았습니다."
    if failure_kind == "core_request_timeout":
        return "nte-core 요청 응답 대기가 시간 초과되었습니다. 명명된 파이프 누락 탐지 결과가 아닙니다."
    if failure_kind == "request_rejected":
        return f"장비 플러그인이 요청을 받았지만 실행을 거부했습니다: {message}"
    if failure_kind == "snapshot_timeout":
        return "장착 요청은 전송되었지만 대기 시간 내에 새 안정 가방 스냅샷을 얻지 못했습니다."
    if failure_kind == "snapshot_error":
        return f"장착 요청은 전송되었지만 가방 동기화 재검토에 실패했습니다: {message}"
    if failure_kind == "loadout_mismatch":
        return message
    return message


def _equipment_assembly_is_running(window: Any) -> bool:
    """Report whether an account-bound assembly worker is still active."""

    for attribute in (
        "_equipment_apply_worker",
        "_automatic_equipment_apply_worker",
    ):
        worker = getattr(window, attribute, None)
        if worker is not None and worker.isRunning():
            return True
    return False


def _run_nte_core_equipment_apply(
    self: Any,
    role_names: list[str],
    *,
    slot_ids: list[int] | None = None,
    identity_overrides: dict[str, dict[str, Any]] | None = None,
    job_id: int | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    sync_service = getattr(self, "_inventory_sync_service", None)
    if sync_service is None:
        raise RuntimeError("가방 동기화 서비스가 아직 시작되지 않았습니다. 먼저 홈에서 백그라운드 동기화를 시작하세요")
    app_context = getattr(self, "app_context", None)
    database_path = (
        app_context.account.user_database_path if app_context is not None else getattr(self, "user_database_path", None)
    )
    if database_path is None:
        raise RuntimeError("고속 장착에 현재 계정 데이터베이스 의존성이 없습니다")
    return BulkEquipmentApplyService(
        database_path,
        sync_service,
        dao_factory=UserDataDao,
        apply_service_factory=EquipmentApplyService,
        operation_context=OperationContext.create(
            "equipment_apply",
            account_id=(
                app_context.account.active_account_id
                if app_context is not None
                else None
            ),
            context_generation=(app_context.generation if app_context is not None else None),
            job_id=job_id,
        ),
    ).run(
        role_names,
        slot_ids=slot_ids,
        identity_overrides=identity_overrides,
        job_id=job_id,
        progress_callback=progress_callback,
    )


def _is_missing_character_instance_request(request: dict) -> bool:
    reason = str(request.get("reason") or "")
    return "캐릭터 인스턴스 캐시 모두 해당 캐릭터 UID를 포함하지 않" in reason or "현재 안정 가방 스냅샷에" in reason


def _show_fast_apply_identity_gaps(
    self: Any,
    requests: list[dict[str, Any]],
    applied: list[dict[str, Any]],
) -> None:
    """Report unresolved UIDs only after every independently runnable role ran."""
    missing_instances = [request for request in requests if _is_missing_character_instance_request(request)]
    ambiguous_instances = [request for request in requests if request not in missing_instances]
    lines = []
    if applied:
        lines.append(f"캐릭터 인스턴스를 가져올 수 있는 {len(applied)}명의 고속 장착을 먼저 완료했습니다.")
    if missing_instances:
        lines.append("다음 캐릭터는 아직 사용 가능한 캐릭터 인스턴스 UID를 가져오지 못해 고속 장착을 실행하지 않았습니다:")
        lines.extend(f"• {request['role_name']}" for request in missing_instances)
    if ambiguous_instances:
        lines.append("다음 캐릭터는 캐릭터 인스턴스 UID를 안전하게 확정할 수 없어 고속 장착을 실행하지 않았습니다:")
        lines.extend(f"• {request['role_name']}" for request in ambiguous_instances)
    lines.extend(
        (
            "",
            "게임을 온라인 상태로 유지한 채 가방 동기화를 다시 시작하고 새 안정 스냅샷을 기다린 뒤 미완료 캐릭터를 다시 시도하세요.",
            "여러 번 동기화해도 이 캐릭터들의 인스턴스 UID를 얻을 수 없다면 자동 장착으로 바꾸거나, 게임 안에서 직접 장비 세팅을 완료하세요.",
        )
    )
    QMessageBox.warning(self, "일부 캐릭터 고속 장착 안 됨", "\n".join(lines))


def _start_nte_core_equipment_apply(
    self: Any,
    role_names: list[str],
    *,
    slot_ids: list[int] | None = None,
    identity_overrides: dict[str, dict[str, Any]] | None = None,
    job_id: int | None = None,
) -> None:
    current_worker = getattr(self, "_equipment_apply_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "장착 중", "이미 장착 작업이 실행 중입니다. 명령 전송이 끝날 때까지 기다리세요.")
        return

    progress_state: dict[str, Any] = {
        "current": 0,
        "total": max(1, len(slot_ids or role_names)),
        "message": "고속 장착 준비 중…",
        "show_progress_bar": True,
    }
    progress_dialog = QProgressDialog(
        progress_state["message"],
        "",
        0,
        progress_state["total"],
        self,
    )
    progress_dialog.setWindowTitle("고속 장착 진행률")
    progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
    progress_dialog.setCancelButton(None)
    progress_dialog.setAutoClose(False)
    progress_dialog.setAutoReset(False)
    progress_dialog.setMinimumDuration(0)
    progress_dialog.setValue(0)
    progress_dialog.show()

    progress_timer = QTimer(progress_dialog)

    def update_progress_dialog() -> None:
        total = max(1, int(progress_state.get("total", 1)))
        progress_dialog.setMaximum(total)
        progress_dialog.setValue(min(total, max(0, int(progress_state.get("current", 0)))))
        progress_dialog.setLabelText(str(progress_state.get("message") or "고속 장착 중…"))
        progress_bar = progress_dialog.findChild(QProgressBar)
        if progress_bar is not None:
            progress_bar.setVisible(bool(progress_state.get("show_progress_bar", True)))

    progress_timer.timeout.connect(update_progress_dialog)
    progress_timer.start(80)

    def update_progress(payload: dict) -> None:
        progress_state.update(payload)

    def close_progress_dialog() -> None:
        progress_timer.stop()
        progress_dialog.close()
        progress_dialog.deleteLater()

    worker = WorkerThread(
        target=lambda: _run_nte_core_equipment_apply(
            self,
            role_names,
            slot_ids=slot_ids,
            identity_overrides=identity_overrides,
            job_id=job_id,
            progress_callback=update_progress,
        ),
        parent=self,
    )
    self._equipment_apply_worker = worker

    def on_result(report: dict) -> None:
        close_progress_dialog()
        preflight_errors = report.get("preflight_errors") or []
        if preflight_errors:
            details = "\n".join(
                f"• [{row.get('role_name', '未知角色')}]: {row.get('error', '方案不可用')}" for row in preflight_errors
            )
            QMessageBox.warning(
                self,
                "고속 장착이 시작되지 않음",
                "게임에 어떤 장착 명령도 보내지 않았습니다. 다음 저장된 방안은 고속 장착에 사용할 수 없습니다:\n\n"
                f"{details}\n\n다시 계산하고 완전한 방안을 저장한 뒤 다시 시도하세요.",
            )
            return
        applied = report.get("applied") or []
        requests = report.get("identity_requests") or []
        summary, role_details = build_fast_apply_completion_summary(applied)
        if report.get("failed_role"):
            error_message = str(report.get("error") or "알 수 없는 오류")
            failure_kind = str(report.get("failure_kind") or "apply_error")
            if (
                failure_kind == "plugin_unavailable"
                or _is_equipment_plugin_unavailable_error(error_message)
            ):
                reason = _equipment_failure_details(
                    "plugin_unavailable",
                    error_message,
                )
                QMessageBox.warning(
                    self,
                    "장비 플러그인을 사용할 수 없음",
                    f"작업 #{report.get('job_id')}이(가) [{report['failed_role']}]에서 중지되었습니다.\n"
                    f"{reason}\n\n"
                    "먼저 확인하세요:\n"
                    "1. “설정 → 환경 설정”에서 현재 nte-core와 맞는"
                    "nte-mods-plugin과 equipment.nte를 다시 배포했는지;\n"
                    "2. 게임 로그인을 유지한 채 홈에서 가방 동기화를 다시 시작하고 “백그라운드 감시”를 기다렸는지;\n"
                    "3. 위 확인을 마친 뒤 우상단 “고속 장착”을 클릭해 다시 실행하세요.\n\n"
                    f"이전에 {len(applied)}명을 확인했으며 작업 로그를 저장했습니다. 이번에는 바로 재시도하지 않습니다.",
                )
                return
            reason = _equipment_failure_details(failure_kind, error_message)
            retry = QMessageBox.question(
                self,
                "장착 일시 중지",
                f"작업 #{report.get('job_id')}이(가) [{report['failed_role']}]에서 중지되었습니다.\n{reason}\n\n"
                f"이전에 {len(applied)}명을 확인했으며 작업 로그를 저장했습니다. 실패한 캐릭터를 재시도하고 계속할까요?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if retry == QMessageBox.Yes:
                _start_nte_core_equipment_apply(self, [], job_id=report["job_id"])
            return
        if requests:
            _show_fast_apply_identity_gaps(self, requests, applied)
            refresh = getattr(self, "_refresh_equip", None)
            if callable(refresh):
                refresh()
            return
        snapshot_failure = report.get("snapshot_wait_failure")
        if isinstance(snapshot_failure, dict):
            attempt = int(snapshot_failure.get("attempt") or 1)
            reason = _equipment_failure_details(
                str(snapshot_failure.get("kind") or "snapshot_error"),
                snapshot_failure.get("error"),
            )
            QMessageBox.warning(
                self,
                "장착 재검토 미완료",
                f"{summary}.\n\n{attempt}번째 장착 후 재검토가 완료되지 않았습니다: {reason}\n\n"
                "신뢰할 수 있는 새 스냅샷을 얻지 못해 이번에는 후속 장착 요청을 보내지 않았습니다.",
            )
            return
        message = (
            f"{summary}.\n작업 #{report.get('job_id')}의 로그를 저장했습니다."
            f"\n\n{role_details}"
            "\n\n고속 장착은 장착 누락이 있을 수 있으니 직접 한 번 확인하고, 누락된 캐릭터는 개별로 고속 장착해 보완하세요."
        )
        completion_box = QMessageBox(QMessageBox.Icon.Information, "장착 완료", message, QMessageBox.StandardButton.Ok, self)
        completion_box.setMinimumWidth(560)
        completion_box.exec()
        refresh = getattr(self, "_refresh_equip", None)
        if callable(refresh):
            refresh()

    def on_error(message: str) -> None:
        close_progress_dialog()
        QMessageBox.critical(
            self,
            "장착 실패",
            f"로컬 구성 요소가 장착을 완료하지 못했습니다:\n{message}\n\n게임 로그인, 플러그인 로드, 홈의 가방 동기화가 “백그라운드 감시” 상태인지 확인하세요.",
        )

    worker.result_ready.connect(on_result)
    worker.error.connect(on_error)
    worker.start()


def _confirm_automatic_assembly_fallback(
    self: Any,
    detail: str,
) -> bool:
    """Ask before falling back from native-UID fast assembly to UI automation."""

    result = QMessageBox.question(
        self,
        "자동 장착으로 전환",
        f"{detail}\n\n단계별 자동 장착으로 바꿀까요?",
        QMessageBox.Yes | QMessageBox.Cancel,
        QMessageBox.Cancel,
    )
    return result == QMessageBox.Yes


def _preview_nte_core_assemble_role(
    self: Any,
    role_name: str,
    *,
    slot_id: int | None = None,
    confirmed: bool = False,
) -> None:
    """确认后通过装备插件极速装配一个已保存角色方案。"""

    try:
        with UserDataDao(_account_database_path(self)) as user_dao:
            slot = user_dao.get_loadout_slot(int(slot_id)) if slot_id is not None else None
            plan = slot.get("current_plan") if slot is not None else user_dao.get_active_loadout_plan_for_role(role_name)
            if slot is not None and plan is not None:
                role_name = str((plan.get("payload") or {}).get("source_role_name") or role_name)
            source_snapshot_id = plan.get("source_snapshot_id") if plan else None
            source_summary = (
                user_dao.inventory_snapshot_summary(int(source_snapshot_id)) if source_snapshot_id is not None else None
            )
            source = source_summary.get("source") if source_summary else None
    except Exception as exc:
        QMessageBox.warning(self, "고속 장착", f"저장된 방안을 읽을 수 없습니다: {exc}")
        return
    if is_visual_inventory_source(source):
        if _confirm_automatic_assembly_fallback(
            self,
            "현재 저장된 방안은 비전 스캔 스냅샷에서 왔으며, 장비 UID는 비전 스캔이 생성한 임시 식별자입니다."
            "고속 장착은 패킷 캡처 동기화(nte_core)가 제공하는 게임 원본 UID만 기록할 수 있습니다.\n\n"
            "잘못된 장비 기록을 피하려면 단계별 자동 장착을 사용할 수 있습니다. 고속 장착을 사용하려면 가방 동기화를 한 번 완료한 뒤"
            "이 캐릭터의 방안을 다시 계산하고 저장하세요.",
        ):
            _preview_automatic_assemble_role(
                self,
                role_name,
                slot_id=slot_id,
                confirmed=confirmed,
            )
        return

    if confirmed:
        _start_nte_core_equipment_apply(
            self,
            [role_name] if slot_id is None else [],
            slot_ids=[int(slot_id)] if slot_id is not None else None,
        )
        return
    ret = QMessageBox.question(
        self,
        "고속 장착",
        f"게임 내 장비 플러그인을 통해 [{role_name}]의 저장된 방안을 게임에 바로 장착합니다.\n\n"
        "이미 목표 세팅이면 즉시 완료되고, 아니면 명령을 보내고 안정 가방 스냅샷 확인을 기다립니다."
        "게임 장비 세팅 페이지로 전환할 필요는 없습니다. 계속할까요?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret == QMessageBox.Yes:
        _start_nte_core_equipment_apply(
            self,
            [role_name] if slot_id is None else [],
            slot_ids=[int(slot_id)] if slot_id is not None else None,
        )


def _preview_nte_core_assemble_all_roles(
    self: Any,
    *,
    confirmed: bool = False,
    role_names: list[str] | None = None,
) -> None:
    requested_roles = tuple(dict.fromkeys(str(name) for name in (role_names or ())))
    try:
        with UserDataDao(_account_database_path(self)) as user_dao:
            selection_service = LoadoutSlotSelectionService(user_dao)
            current_slots = selection_service.list_current()
            if requested_roles:
                available_roles = {selection.role_name for selection in current_slots}
                missing = [role_name for role_name in requested_roles if role_name not in available_roles]
                if missing:
                    QMessageBox.information(
                        self,
                        "고속 장착",
                        f"다음 캐릭터는 아직 현재 방안을 저장하지 않았습니다: {'、'.join(missing)}",
                    )
                    return
                current_slots = tuple(
                    selection
                    for selection in current_slots
                    if selection.role_name in requested_roles
                )
            selected_slot_ids = select_assembly_slot_ids(self, current_slots)
            if selected_slot_ids is None:
                return
            selections = selection_service.resolve(
                selected_slot_ids,
            )
            nte_slot_ids = []
            visual_roles = []
            for selection in selections:
                role_name = selection.role_name
                plan = selection.plan
                snapshot_id = plan.get("source_snapshot_id")
                summary = user_dao.inventory_snapshot_summary(int(snapshot_id)) if snapshot_id is not None else None
                if summary and summary.get("source") == "nte_core":
                    nte_slot_ids.append(selection.slot_id)
                elif summary and is_visual_inventory_source(summary.get("source")):
                    visual_roles.append(role_name)
    except Exception as exc:
        QMessageBox.warning(self, "고속 장착", f"공식 SQLite 방안을 읽을 수 없습니다: {exc}")
        return
    if nte_slot_ids:
        selected_slot_ids = list(nte_slot_ids)
    elif visual_roles:
        if _confirm_automatic_assembly_fallback(
            self,
            "현재 저장된 방안은 비전 스캔 스냅샷에서 왔으며, 장비 UID는 비전 스캔이 생성한 임시 식별자입니다."
            "고속 장착은 패킷 캡처 동기화(nte_core)가 제공하는 게임 원본 UID만 기록할 수 있습니다.\n\n"
            "잘못된 장비 기록을 피하려면 단계별 자동 장착을 사용할 수 있습니다. 고속 장착을 사용하려면 가방 동기화를 한 번 완료한 뒤"
            "방안을 다시 계산하고 저장하세요.",
        ):
            _preview_automatic_assemble_all_roles(
                self,
                role_names=list(requested_roles) if requested_roles else None,
            )
        return
    else:
        QMessageBox.information(self, "고속 장착", "현재 공식 가방 스냅샷에서 온 저장된 방안이 없습니다. 먼저 다시 계산하고 저장하세요.")
        return
    if confirmed:
        _start_nte_core_equipment_apply(self, [], slot_ids=selected_slot_ids)
        return
    ret = QMessageBox.question(
        self,
        "고속 장착",
        f"로컬 구성 요소에 캐릭터 {len(selected_slot_ids)}명의 장착 명령을 순서대로 보냅니다."
        "이미 올바르게 장착된 캐릭터는 바로 건너뛰고, 나머지는 안정 가방 스냅샷 확인 후 다음 캐릭터를 처리합니다."
        "\n\n계속할까요?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret == QMessageBox.Yes:
        _start_nte_core_equipment_apply(self, [], slot_ids=selected_slot_ids)


def _preview_fast_assemble_all_roles(
    self: Any,
    role_names: list[str] | None = None,
) -> None:
    """从配装页右上角启动全部角色的极速装配。"""

    _preview_nte_core_assemble_all_roles(self, role_names=role_names)


def _select_single_role_assembly_mode(
    self: Any,
    role_name: str,
) -> str | None:
    """让用户为一个角色显式选择极速或自动装配。"""

    dialog = QMessageBox(self)
    dialog.setWindowTitle("장착 방식 선택")
    dialog.setIcon(QMessageBox.Question)
    # QMessageBox 会根据标签内容重新收缩；同时设置标签最小宽度和初始尺寸，
    # 确保两种装配方式的说明不会挤在窄弹窗里。
    dialog.setMinimumSize(720, 400)
    dialog.setStyleSheet("QLabel#qt_msgbox_label,QLabel#qt_msgbox_informativelabel{min-width:620px;}")
    dialog.setText(f"[{role_name}]의 장착 방식 선택")
    dialog.setInformativeText(
        "고속 장착: 게임 내 장비 플러그인으로 방안을 바로 기록합니다. 빠르고 장비 세팅 페이지를 열 필요가 없습니다.\n\n"
        "자동 장착: 게임 내 조작을 모사해 단계별로 완료합니다. 장비 플러그인은 필요 없지만 캐릭터 상세 페이지에 머물러야 하며 시간이 더 걸립니다."
    )
    fast_button = dialog.addButton("고속 장착", QMessageBox.ActionRole)
    automatic_button = dialog.addButton("자동 장착", QMessageBox.ActionRole)
    dialog.addButton(QMessageBox.Cancel)
    dialog.resize(720, 400)
    dialog.exec()
    if dialog.clickedButton() is fast_button:
        return "fast"
    if dialog.clickedButton() is automatic_button:
        return "automatic"
    return None


def _preview_assemble_role(
    self: Any,
    role_name: str,
    *,
    slot_id: int | None = None,
) -> None:
    """为单个角色或指定配装槽位显示装配方式选择。"""

    mode = _select_single_role_assembly_mode(self, role_name)
    if mode == "fast":
        if slot_id is None:
            _preview_nte_core_assemble_role(self, role_name, confirmed=True)
        else:
            _preview_nte_core_assemble_role(
                self,
                role_name,
                slot_id=slot_id,
                confirmed=True,
            )
    elif mode == "automatic":
        if slot_id is None:
            _preview_automatic_assemble_role(self, role_name, confirmed=True)
        else:
            _preview_automatic_assemble_role(
                self,
                role_name,
                slot_id=slot_id,
                confirmed=True,
            )


def request_equipment_assembly(
    self: Any,
    *,
    role_names: list[str],
    method: str,
) -> None:
    """Public feature boundary used by calculated-result actions."""

    names = list(dict.fromkeys(str(role_name) for role_name in role_names if str(role_name).strip()))
    if not names:
        return
    if method not in {"nte_core", "gamepad"}:
        raise ValueError(f"unsupported equipment assembly method: {method}")
    if len(names) == 1:
        if method == "nte_core":
            _preview_nte_core_assemble_role(self, names[0])
        else:
            _preview_automatic_assemble_role(self, names[0])
        return
    if method == "nte_core":
        _preview_fast_assemble_all_roles(self, role_names=names)
    else:
        _preview_automatic_assemble_all_roles(
            self,
            role_names=names,
        )


class EquipmentAssemblyControllerMixin:
    """Explicit MainWindow surface for fast and UI-driven equipment assembly."""

    _equipment_assembly_is_running = _equipment_assembly_is_running
    _preview_assemble_role = _preview_assemble_role
    _preview_fast_assemble_all_roles = _preview_fast_assemble_all_roles
    _preview_automatic_assemble_all_roles = _preview_automatic_assemble_all_roles
    request_equipment_assembly = request_equipment_assembly
