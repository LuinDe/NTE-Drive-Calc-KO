# 提供扫描页面的本地规则编辑入口。
from src.features.scanning.dependencies import current_scanning_dependencies as _current_scanning_dependencies
from src.features.scanning.post_action_dialog import show_scan_post_action_dialog
from src.domain.work_mode import WorkMode
from src.features.input_operation_entry import confirm_operation_recommendation


def open_scan_post_action_manager(self):
    if self.work_mode_provider() == WorkMode.MEDIUM:
        if confirm_operation_recommendation(
            self.dialog_parent, title="폐기/잠금 관리 안내",
            message="현재 중위험 작업 모드입니다. 창고 우상단의 관리 기능 사용을 권장합니다. 이동해서 사용할까요?",
            action_text="이동",
        ):
            self.navigate("warehouse")
        return
    dependencies = _current_scanning_dependencies(self)
    show_scan_post_action_dialog(
        self.dialog_parent,
        dependencies.user_config_dir,
        dependencies.config_dir,
        user_database_path=dependencies.user_database_path,
        static_database_path=dependencies.static_database_path,
        asset_root=dependencies.game_ui_asset_root,
    )
