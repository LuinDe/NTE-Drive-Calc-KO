# 为手动功能入口提供受限说明和设置导航，不执行授权或业务操作。
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from src.app.window_geometry import fit_dialog_to_available_screen


_MODE_LABELS = {"offline": "오프라인", "low": "저위험", "medium": "중위험", "developer": "개발"}
_REQUIREMENTS = {
    "battle_capture": "패킷 캡처 전투 리포트를 사용하는 저위험 모드 또는 DLL 전투 리포트를 사용하는 중위험 모드를 선택하고 위험을 명확히 확인하세요; 개발 모드에서는 선택한 소스에 따라 수집할 수 있습니다.",
    "game_sync": "저위험 모드에서 패킷 캡처 동기화를 사용하거나 중위험 모드에서 DLL 동기화를 사용하도록 선택하고, 위험을 명확히 확인하세요.",
    "packet_capture": "저위험 모드 또는 패킷 캡처 소스가 활성화된 개발 모드가 필요하며, 위험을 명확히 확인해야 합니다.",
    "interface_input": "저위험, 중위험 또는 개발 모드가 필요하며, 위험을 명확히 확인해야 합니다.",
    "native_load": "중위험 모드 또는 DLL 소스가 활성화된 개발 모드가 필요하며, 위험을 명확히 확인해야 합니다.",
    "native_sync": "중위험 모드 또는 DLL 소스가 활성화된 개발 모드가 필요하며, 위험을 명확히 확인해야 합니다.",
    "native_battle": "중위험 모드 또는 DLL 소스가 활성화된 개발 모드가 필요하며, 위험을 명확히 확인해야 합니다.",
    "native_equipment": "중위험 모드 또는 DLL 소스가 활성화된 개발 모드가 필요하며, 위험을 명확히 확인해야 합니다.",
    "diagnostics": "모든 작업 모드에서 진단 정보를 저장할 수 있습니다; 실제 수집 소스는 여전히 현재 모드를 따릅니다.",
    "compare_sources": "개발 모드를 선택하고 위험을 명확히 확인해야 합니다; 개발 모드는 이중 경로 대조로 고정되어 있습니다.",
}


def entry_is_allowed(policy, capability: str) -> bool:
    if capability in {"battle_capture", "game_sync"}:
        native = "native_battle" if capability == "battle_capture" else "native_sync"
        return policy.allowed(native) or policy.allowed("packet_capture")
    return policy.allowed(capability)


def _belongs_to(widget, owner) -> bool:
    if owner is None:
        return False
    parent = widget.parentWidget()
    while parent is not None:
        if parent is owner:
            return True
        parent = parent.parentWidget()
    return False


def prompt_operation_settings(
    parent, *, title: str, feature: str, detail: str, navigate, target: str,
    action_text: str = "설정으로 이동",
) -> None:
    """Only an explicit navigation click leaves the current feature dialog."""
    origin = QApplication.activeModalWidget()
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    text = QLabel(
        feature + "\n\n" + detail + "\n\n완료 후 돌아와 이 기능을 다시 클릭하세요; 이번 작업은 자동으로 이어지지 않습니다.",
        dialog,
    )
    text.setTextFormat(Qt.PlainText)
    text.setWordWrap(True)
    layout.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.Cancel, parent=dialog)
    cancel = buttons.button(QDialogButtonBox.Cancel)
    cancel.setText("취소")
    go = buttons.addButton(action_text, QDialogButtonBox.AcceptRole)
    go.setAutoDefault(False)
    cancel.setDefault(True)
    cancel.setFocus()
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    fit_dialog_to_available_screen(dialog, QSize(600, 245))
    if dialog.exec() != QDialog.Accepted:
        return
    if isinstance(origin, QDialog) and origin is not parent and _belongs_to(origin, parent):
        origin.reject()
    navigate(target)


def allow_operation_entry(parent, policy, capability: str, feature: str, navigate) -> bool:
    if entry_is_allowed(policy, capability):
        return True
    mode = _MODE_LABELS[policy.settings.mode.value]
    detail = "현재 " + mode + " 모드에서는 이 기능을 사용할 수 없습니다.\n" + _REQUIREMENTS[capability]
    prompt_operation_settings(parent, title="기능 제한됨", feature=feature, detail=detail,
                              navigate=navigate, target="mode")
    return False


def explain_operation_unavailable(parent, feature: str, detail: str, navigate, target: str = "detection") -> None:
    target = target if target in {"deployment", "home"} else "detection"
    if target == "home":
        prompt_operation_settings(
            parent, title="기능 일시 사용 불가", feature=feature, detail=detail,
            navigate=navigate, target=target, action_text="워크스페이스로 이동",
        )
        return
    destination = "컴포넌트 배포 설정" if target == "deployment" else "현재 검사 정보입니다; 필요하면 다시 검사를 눌러 주세요"
    prompt_operation_settings(
        parent, title="기능 일시 사용 불가", feature=feature,
        detail=detail + "\n설정에서 확인할 수 있습니다" + destination + ".",
        navigate=navigate, target=target,
    )
