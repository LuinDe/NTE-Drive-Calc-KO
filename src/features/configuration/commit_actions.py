# 编排基础权重页面的后台提交、草稿保留和提交后的异步刷新。
from __future__ import annotations

from copy import deepcopy

from PySide6.QtWidgets import QInputDialog, QMessageBox

from .dependencies import BasicWeightDependencies


_DIRTY_FIELDS = (
    "_config_dirty_character_ids", "_config_dirty_shape_bonus_ids",
    "_config_dirty_board_ids", "_config_dirty_target_suit_ids",
)


def _clear_draft(window, *, discard=False):
    window._config_dirty = False
    for field in _DIRTY_FIELDS:
        getattr(window, field, set()).clear()
    if discard:
        window._config_form_data = None
        window._config_loaded_model = None


def _submit(window, work, done, *, title, completion=None):
    from .page import _basic_weight_controller
    controller = _basic_weight_controller(window)
    if controller.is_writing():
        return False
    page = getattr(window, "config_page_view", None)
    status = getattr(window, "config_load_status", None)
    if page is not None:
        page.setEnabled(False)
    if status is not None:
        status.setText(f"{title} 중…")

    def current():
        return (controller is getattr(window, "_basic_weight_controller", None)
                and controller.dependencies == BasicWeightDependencies.from_app_context(window.app_context))

    def unlock():
        if current():
            if page is not None:
                page.setEnabled(True)
            if status is not None:
                status.clear()

    def committed(value):
        unlock()
        if current():
            done(value)
            if completion is not None:
                completion(True)
        elif completion is not None:
            completion(False)

    def failed(error):
        unlock()
        if current():
            QMessageBox.warning(window, f"{title} 미완료", f"편집 내용은 유지되었습니다. 저장된 내용을 확인한 뒤 다시 시도하세요.\n{error}")
            if completion is not None:
                completion(False)
        elif completion is not None:
            completion(False)

    def application_failed(error):
        unlock()
        if current():
            QMessageBox.warning(window, "제출 후 새로 고침 실패", f"데이터가 제출되었습니다. 페이지에 다시 들어가 새로 고침하세요. 중복 저장하지 마세요.\n{error}")
            if completion is not None:
                completion(False)

    return controller.submit_change(work, committed, failed, application_failed)


def save_config_form(window, config_dir, json_edit_dialog_cls, *, completion=None, show_message=True):
    del config_dir, json_edit_dialog_cls
    from .page import _basic_weight_controller
    if getattr(window, "_current_config_name", None) != "account_weights":
        return False
    controller = _basic_weight_controller(window)
    data = deepcopy(getattr(window, "_config_form_data", {}) or {})
    fields = tuple(set(getattr(window, field, set())) for field in _DIRTY_FIELDS)

    def done(_value):
        _clear_draft(window)
        # The submitted model remains the display baseline, not a fresh database read.
        if show_message:
            QMessageBox.information(window, "저장", "캐릭터 가중치 설정이 저장되었습니다.")

    return _submit(window, lambda: controller.save_changes(data, *fields), done,
                   title="저장", completion=completion)


def _reload_after_weight_reset(window, config_dir, active_role):
    from .page import switch_config_form
    _clear_draft(window, discard=True)
    switch_config_form(window, config_dir=config_dir, active_role=active_role)


def _confirm_weight_reset(window, message):
    from .page import _basic_weight_controller
    if (getattr(window, "_current_config_name", None) != "account_weights"
            or _basic_weight_controller(window).is_writing()):
        return False
    return QMessageBox.question(window, "가중치 초기화 확인", message,
                                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Yes


def reset_current_config_weights(window, config_dir):
    from .page import _basic_weight_controller
    name = str(getattr(window, "_config_active_role", "") or "")
    role = (getattr(window, "_config_form_data", {}) or {}).get(name) or {}
    if not name or not role:
        QMessageBox.information(window, "현재 초기화", "먼저 캐릭터를 선택하세요.")
        return
    if role.get("is_custom"):
        QMessageBox.information(window, "현재 초기화", "사용자 정의 캐릭터에는 배포 기본 가중치가 없어 현재 사용자 값을 유지합니다.")
        return
    if not _confirm_weight_reset(window,
        f"현재 계정에서 [{name}]의 사용자 정의 카트리지 메인 스탯·드라이브 서브 스탯 가중치를 지우고, 현재 이환 공방 기본값으로 복원합니다.\n\n"
        "추가 형태 태그와 추가 형태 보너스는 바뀌지 않으며, 저장되지 않은 편집은 버려집니다.\n가중치가 적용되지 않으면 계산기를 다시 시작하세요."):
        return
    controller, ids = _basic_weight_controller(window), (int(role["character_id"]),)

    def done(_value):
        _reload_after_weight_reset(window, config_dir, name)
        QMessageBox.information(window, "현재 초기화", f"[{name}]을(를) 기본 가중치로 복원했습니다. 이후 새 버전에 따라 갱신됩니다.")

    _submit(window, lambda: controller.reset_weights(ids), done, title="초기화")


def reset_all_config_weights(window, config_dir):
    from .page import _basic_weight_controller
    data = getattr(window, "_config_form_data", {}) or {}
    ids = tuple(int(role["character_id"]) for role in data.values()
                if isinstance(role, dict) and role.get("character_id") is not None and not role.get("is_custom"))
    if not ids or not _confirm_weight_reset(window,
        f"현재 계정의 캐릭터 {len(ids)}명 전체의 사용자 정의 카트리지 메인 스탯·드라이브 서브 스탯 가중치를 지우고, 현재 이환 공방 기본값으로 복원합니다.\n\n"
        "이 작업은 되돌릴 수 없습니다. 추가 형태 태그와 추가 형태 보너스는 바뀌지 않으며, 저장되지 않은 편집은 버려집니다.\n가중치가 적용되지 않으면 계산기를 다시 시작하세요."):
        return
    controller, active = _basic_weight_controller(window), str(getattr(window, "_config_active_role", "") or "")

    def done(restored):
        _reload_after_weight_reset(window, config_dir, active)
        QMessageBox.information(window, "전체 초기화", f"캐릭터 {len(restored)}명의 기본 가중치를 복원했습니다. 이후 새 버전에 따라 갱신됩니다.")

    _submit(window, lambda: controller.reset_weights(ids), done, title="초기화")


def reset_config_form(window, config_dir, bundled_config_dir):
    del bundled_config_dir
    _reload_after_weight_reset(window, config_dir, None)


def create_custom_role(window):
    from .page import _basic_weight_controller, confirm_pending_config_changes, switch_config_form
    controller = _basic_weight_controller(window)
    if controller.is_writing():
        return
    if getattr(window, "_config_dirty", False):
        if not confirm_pending_config_changes(window, window.app_context.paths.config_dir,
                completion=lambda success: create_custom_role(window) if success else None):
            return
    name, accepted = QInputDialog.getText(window, "새 캐릭터 생성", "캐릭터 이름 (게임 내 이름으로도 사용):")
    if not accepted:
        return

    def done(role):
        _clear_draft(window, discard=True)
        switch_config_form(window, active_role=str(role["name_zh"]))

    _submit(window, lambda: controller.create_custom_role(str(name)), done, title="새 캐릭터 생성")


def delete_custom_role(window, role_name, role_data, rebuild_all_tabs):
    from .page import _basic_weight_controller
    controller = _basic_weight_controller(window)
    if controller.is_writing() or QMessageBox.question(window, "캐릭터 삭제",
        f"[{role_name}]과(와) 해당 계산 선호도·장비 세팅 슬롯을 삭제할까요?",
        QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
        return
    character_id = int(role_data["character_id"])

    def done(_value):
        # Match the existing deletion rule: remove this draft, retain other staged values.
        for field in _DIRTY_FIELDS:
            getattr(window, field, set()).discard(character_id)
        window._config_dirty = any(getattr(window, field, set()) for field in _DIRTY_FIELDS)
        (getattr(window, "_config_form_data", {}) or {}).pop(role_name, None)
        window._config_loaded_model = None
        rebuild_all_tabs()

    _submit(window, lambda: controller.delete_custom_role(character_id), done, title="캐릭터 삭제")
