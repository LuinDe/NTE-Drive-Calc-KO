# 组合战报边际分析工具栏控件。
"""Fixed navigation and role-selection toolbar for battle marginal analysis."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton

from src.app.dialogs import show_help
from src.app.theme import themed_style
from src.ui.widgets import NoWheelComboBox


@dataclass(frozen=True, slots=True)
class BattleMarginalToolbar:
    widget: QFrame
    character_combo: NoWheelComboBox
    change_summary: QLabel
    use_inferred_facts: QCheckBox
    reset_button: QPushButton
    recalculate_button: QPushButton


def build_marginal_toolbar(
    *,
    back: Callable[[], None],
    role_changed: Callable[[int], None],
    inferred_toggled: Callable[[bool], None],
    reset: Callable[[], None],
    recalculate: Callable[[], None],
) -> BattleMarginalToolbar:
    help_text = (
        "이번 전투의 동작·히트·대상·시간 구간을 고정하고 캐릭터 속성과 구성만 교체합니다."
        "변화 의존 항목이 없으면 알려진 부분과 공백을 각각 표시하며, 미지 항목을 0 이득으로 기록하지 않습니다."
    )
    toolbar = QFrame()
    toolbar.setObjectName("marginalStickyToolbar")
    toolbar.setStyleSheet(themed_style(
        "QFrame#marginalStickyToolbar{background:#0d1117;"
        "border-bottom:1px solid #30363d;}"
    ))
    layout = QHBoxLayout(toolbar)
    layout.setContentsMargins(22, 10, 22, 10)
    layout.setSpacing(10)
    back_button = QPushButton("← 전투 리포트로 돌아가기")
    back_button.clicked.connect(back)
    layout.addWidget(back_button)
    title = QLabel("고정축 한계 이득 계산")
    title.setObjectName("pageTitle")
    title.setToolTip(help_text)
    layout.addWidget(title)
    role_box = QFrame()
    role_box.setObjectName("marginalRoleSelector")
    role_box.setStyleSheet(themed_style(
        "QFrame#marginalRoleSelector{background:#13233a;border:1px solid #2f81f7;"
        "border-radius:7px;}"
    ))
    role_layout = QHBoxLayout(role_box)
    role_layout.setContentsMargins(10, 4, 8, 4)
    role_layout.setSpacing(7)
    role_label = QLabel("분석 캐릭터")
    role_label.setStyleSheet(themed_style("color:#58a6ff;font-weight:700"))
    role_layout.addWidget(role_label)
    character_combo = NoWheelComboBox()
    character_combo.currentIndexChanged.connect(role_changed)
    role_layout.addWidget(character_combo)
    layout.addWidget(role_box)
    change_summary = QLabel("캐릭터 구성 대기 중")
    change_summary.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    layout.addWidget(change_summary, 1)
    use_inferred_facts = QCheckBox("히트로 보완한 적용 사실 사용")
    use_inferred_facts.setChecked(True)
    use_inferred_facts.setToolTip(
        "현재 적용 기준선에는 없지만 완전한 원본 히트로 캐릭터 효과가 적용되었음을 정확히 증명할 수 있을 때만 표시됩니다."
        "해제하면 이 페이지의 후보에만 영향을 주며, 전투 리포트 스냅샷, 수정 사본, 캐릭터 페이지는 변경하지 않습니다."
    )
    use_inferred_facts.hide()
    use_inferred_facts.toggled.connect(inferred_toggled)
    layout.addWidget(use_inferred_facts)
    reset_button = QPushButton("초기화")
    reset_button.setToolTip("이 페이지에 들어왔을 때의 메모리 기준선을 복원합니다. DB를 읽거나 저장하지 않습니다.")
    reset_button.clicked.connect(reset)
    layout.addWidget(reset_button)
    recalculate_button = QPushButton("재계산")
    recalculate_button.setObjectName("btnPrimary")
    recalculate_button.clicked.connect(recalculate)
    layout.addWidget(recalculate_button)
    help_button = QPushButton("?")
    help_button.setObjectName("btnHelp")
    help_button.setToolTip(help_text)
    help_button.clicked.connect(
        lambda _checked=False: show_help(
            help_button,
            "고정축 한계 이득 계산",
            help_text,
        )
    )
    layout.addWidget(help_button)
    return BattleMarginalToolbar(
        widget=toolbar,
        character_combo=character_combo,
        change_summary=change_summary,
        use_inferred_facts=use_inferred_facts,
        reset_button=reset_button,
        recalculate_button=recalculate_button,
    )
