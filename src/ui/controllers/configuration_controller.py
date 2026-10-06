# 配置页面 MainWindow 兼容转发方法。
"""Configuration controller installed onto MainWindow."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from src.app.context import CallbackAccountLifecycle

from src.features.configuration.page import (
    add_weight as config_add_weight,
    build_config_page,
    confirm_pending_config_changes as config_confirm_pending_config_changes,
    del_weight as config_del_weight,
    refresh_config_forms as config_refresh_config_forms,
    render_roles_form,
    reset_all_config_weights as config_reset_all_config_weights,
    reset_current_config_weights as config_reset_current_config_weights,
    reset_config_form as config_reset_config_form,
    save_config_data as config_save_config_data,
    save_config_form as config_save_config_form,
    save_role_weight_value as config_save_role_weight_value,
    switch_config_form as config_switch_config_form,
)
from src.features.official_role.page import confirm_pending_my_role_changes


def _page_config(self):
    return build_config_page(self)

def _refresh_config_forms(self):
    return config_refresh_config_forms(self, self.app_context.paths.config_dir)


def register_page_configuration_lifecycle(app_context, owners_provider):
    """Register before other owners: account replacement never crosses an accepted commit."""
    def stop():
        owners = tuple(owner for owner in owners_provider() if owner is not None)
        if any(owner.is_writing() for owner in owners):
            raise RuntimeError("계정 설정을 제출하는 중입니다. 저장이 완료된 후 계정을 전환하세요.")
        for owner in owners:
            owner.close()

    return app_context.register_account_lifecycle(CallbackAccountLifecycle(
        is_running=lambda: False, stop=stop, rebuild=lambda _account: None, start=lambda: None,
    ))

def defer_page_transition(window, continuation, *, pages=("config", "my_role")) -> bool:
    """Return true while a dirty/committing page owns the requested transition."""
    owners = tuple(owner for owner in (
        getattr(window, "_basic_weight_controller", None),
        getattr(window, "_official_role_controller", None),
    ) if owner is not None and hasattr(owner, "is_writing"))
    fields = {"config": "_config_dirty", "my_role": "_my_role_dirty"}
    if getattr(window, "_pending_page_transition", None) is not None:
        return True
    if not any(owner.is_writing() for owner in owners) and not any(
        getattr(window, fields[page], False) for page in pages if page in fields
    ):
        return False
    token = object()
    context = window.app_context
    identity = context.account.active_account_id, context.generation
    window._pending_page_transition = token

    def current():
        return (getattr(window, "_pending_page_transition", None) is token
                and (window.app_context.account.active_account_id, window.app_context.generation) == identity)

    def cancel():
        if getattr(window, "_pending_page_transition", None) is token:
            window._pending_page_transition = None

    def continue_at(index):
        if not current():
            cancel()
            return
        writing = next((owner for owner in owners if owner.is_writing()), None)
        if writing is not None:
            writing.when_commit_settled(lambda success: continue_at(index) if success else cancel())
            return
        if index >= len(pages):
            def finish():
                if current():
                    cancel()
                    continuation()
                else:
                    cancel()
            QTimer.singleShot(0, finish)
            return
        page = pages[index]
        callback = lambda success: continue_at(index + 1) if success else cancel()
        if page == "config":
            allowed = config_confirm_pending_config_changes(window, context.paths.config_dir, completion=callback)
        elif page == "my_role":
            allowed = confirm_pending_my_role_changes(window, completion=callback)
        else:
            allowed = True
        if allowed:
            continue_at(index + 1)
        elif not any(owner.is_writing() for owner in (
            getattr(window, "_basic_weight_controller", None), getattr(window, "_official_role_controller", None),
        ) if owner is not None and hasattr(owner, "is_writing")):
            cancel()

    continue_at(0)
    return True

def _switch_config_form(self,name):
    return config_switch_config_form(
        self,
        name,
        self.app_context.paths.config_dir,
    )

def _build_roles_form(self,data):
    return render_roles_form(self,data)

def _add_weight(self,rn,data,cb,weight_field="weights"):
    return config_add_weight(
        self,
        rn,
        data,
        cb,
        self.app_context.paths.config_dir,
        weight_field,
    )

def _save_role_weight_value(self,rn,key,value,data,weight_field="weights"):
    return config_save_role_weight_value(
        self,
        rn,
        key,
        value,
        data,
        self.app_context.paths.config_dir,
        weight_field,
    )

def _del_weight(self,rn,key,data,cb,weight_field="weights"):
    return config_del_weight(
        self,
        rn,
        key,
        data,
        cb,
        self.app_context.paths.config_dir,
        weight_field,
    )

def _save_config_form(self):
    return config_save_config_form(
        self,
        self.app_context.paths.config_dir,
        None,
    )

def _reset_config_form(self):
    return config_reset_config_form(
        self,
        self.app_context.paths.config_dir,
        self.app_context.paths.bundled_config_dir,
    )

def _reset_current_config_weights(self):
    return config_reset_current_config_weights(
        self,
        self.app_context.paths.config_dir,
    )

def _reset_all_config_weights(self):
    return config_reset_all_config_weights(
        self,
        self.app_context.paths.config_dir,
    )

def _save_config_data(self,data):
    return config_save_config_data(
        self,
        data,
        self.app_context.paths.config_dir,
    )


class ConfigurationControllerMixin:
    _page_config = _page_config
    _refresh_config_forms = _refresh_config_forms
    _switch_config_form = _switch_config_form
    _build_roles_form = _build_roles_form
    _add_weight = _add_weight
    _save_role_weight_value = _save_role_weight_value
    _del_weight = _del_weight
    _save_config_form = _save_config_form
    _reset_config_form = _reset_config_form
    _reset_current_config_weights = _reset_current_config_weights
    _reset_all_config_weights = _reset_all_config_weights
    _save_config_data = _save_config_data
