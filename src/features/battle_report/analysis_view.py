# 展示统一分析时段下的战斗时间轴、逐击证据和角色边际。
"""Long-form battle analysis view; all calculations stay in application services."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStyle,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.dialogs import show_help
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_report import BattleAnalysisSnapshot, BattleDamageComposition
from src.features.battle_report.analysis_composition_mixin import (
    BattleAnalysisCompositionMixin,
)
from src.features.battle_report.analysis_components import (
    apply_inferred_scope_warning,
    analysis_section as _section,
    analysis_table as _table,
)
from src.features.battle_report.analysis_log_mixin import BattleAnalysisLogMixin
from src.features.battle_report.analysis_scope_mixin import BattleAnalysisScopeMixin
from src.features.battle_report.analysis_timeline_detail_mixin import (
    BattleTimelineDetailMixin,
)
from src.features.battle_report.composition_view import (
    BattleDamageCompositionPanel,
)
from src.features.battle_report.inferred_fact_view import BattleInferredFactLabel
from src.features.battle_report.build_snapshot_control import (
    BattleBuildSnapshotControl,
)
from src.features.battle_report.buff_evidence_view import BattleBuffEvidencePanel
from src.features.battle_report.role_contribution_view import (
    BattleRoleDamagePieWidget,
)
from src.features.battle_report.timeline_view import (
    BattleUnifiedTimelineWidget,
)
from src.features.battle_report.timeline_layout import (
    format_analysis_evidence,
    format_damage as _number,
    format_time as _time,
    format_time_stop_evidence,
)
from src.features.battle_report.target_vital_view import BattleTargetVitalPanel
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    ELAPSED_TIME_MODE,
    BattleTimelineTimeMode,
    project_timeline_time_us,
    projected_range_duration_us,
    unproject_timeline_time_us,
)
from src.services.battle_environment_condition_service import (
    display_battle_environment_name,
)
from src.services.skill_name_rendering_service import (
    preferred_battle_damage_name,
    render_battle_classification,
)
from src.ui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox

class BattleLongAnalysisView(
    BattleAnalysisCompositionMixin,
    BattleAnalysisScopeMixin,
    BattleTimelineDetailMixin,
    BattleAnalysisLogMixin,
    QWidget,
):
    range_requested = Signal(int, int)
    range_reset_requested = Signal()
    character_selected = Signal(int)
    detail_scope_changed = Signal(str)
    marginal_requested = Signal()
    details_requested = Signal(str, object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        game_ui_asset_root: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._game_ui_asset_root = game_ui_asset_root
        self._analysis: BattleAnalysisSnapshot | None = None
        self._current_composition: BattleDamageComposition | None = None
        self._analysis_record_id: int | None = None
        self._selected_character_id: int | None = None
        self._time_mode: BattleTimelineTimeMode = ELAPSED_TIME_MODE
        self._detail_scope = "current"
        self._composition_grouping = "coarse"
        self._topple_detail_requested_analysis: BattleAnalysisSnapshot | None = None
        self._log_page = 0
        self._log_page_size = 200
        self._build()
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        timeline_card, timeline_layout = _section("통합 전투 타임라인")
        range_row = QGridLayout()
        range_row.setHorizontalSpacing(8)
        range_row.setVerticalSpacing(8)
        self.capability_label = QLabel("히트별 근거 대기 중", timeline_card)
        self.capability_label.hide()
        range_row.addWidget(QLabel("시작 (타임라인 초)"), 0, 0)
        self.start_spin = NoWheelDoubleSpinBox()
        self.start_spin.setDecimals(3)
        self.start_spin.setRange(0.0, 999999.0)
        range_row.addWidget(self.start_spin, 0, 1)
        range_row.addWidget(QLabel("종료 (타임라인 초)"), 0, 2)
        self.end_spin = NoWheelDoubleSpinBox()
        self.end_spin.setDecimals(3)
        self.end_spin.setRange(0.001, 999999.0)
        range_row.addWidget(self.end_spin, 0, 3)
        apply_button = QPushButton("확인")
        apply_button.setObjectName("btnPrimary")
        apply_button.clicked.connect(self._request_range)
        range_row.addWidget(apply_button, 0, 4)
        reset_button = QPushButton("초기화")
        reset_button.clicked.connect(self.range_reset_requested)
        range_row.addWidget(reset_button, 0, 5)
        range_row.addWidget(QLabel("기준"), 0, 6)
        self.time_mode_combo = NoWheelComboBox()
        self.time_mode_combo.addItem("시간 정지 제외", ACTIVE_TIME_MODE)
        self.time_mode_combo.addItem("시간 정지 포함", ELAPSED_TIME_MODE)
        self.time_mode_combo.setCurrentIndex(
            self.time_mode_combo.findData(ELAPSED_TIME_MODE)
        )
        self.time_mode_combo.currentIndexChanged.connect(self._time_mode_changed)
        range_row.addWidget(self.time_mode_combo, 0, 7)
        range_row.addWidget(QLabel("줌"), 0, 8)
        self.zoom_combo = NoWheelComboBox()
        for percent in (10, 25, 50, 100, 200, 300, 400, 500, 600, 700, 800):
            self.zoom_combo.addItem(f"{percent}%", percent / 100.0)
        self.zoom_combo.setCurrentIndex(self.zoom_combo.findData(1.0))
        self.zoom_combo.currentIndexChanged.connect(self._zoom_changed)
        range_row.addWidget(self.zoom_combo, 0, 9)
        help_text = (
            "위쪽은 직접 피해, 아래쪽은 동작이며 특수 피해와 사이클은 공용 행을 사용합니다."
            "드래그로 이동하고 우클릭으로 분석 시작/종료를 설정할 수 있으며, 타임라인 본체는 감사 팝업 때문에 바뀌지 않습니다."
        )
        help_button = QPushButton("?")
        help_button.setObjectName("btnHelp")
        help_button.setToolTip(help_text)
        help_button.clicked.connect(
            lambda _checked=False, button=help_button: show_help(
                button,
                "통합 전투 타임라인",
                help_text,
            )
        )
        range_row.addWidget(help_button, 0, 10)
        range_row.setColumnStretch(10, 1)
        timeline_layout.addLayout(range_row)
        timeline_layout.addWidget(self.capability_label)

        context_row = QHBoxLayout()
        self.context_row = context_row
        self.build_edit_control = BattleBuildSnapshotControl()
        context_row.addWidget(QLabel("상세 범위"))
        self.scope_button_group = QButtonGroup(self)
        self.scope_button_group.setExclusive(True)
        self.scope_buttons: dict[str, QPushButton] = {}
        for mode, label in (("current", "따라가기"), ("first", "전반"), ("second", "후반")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, value=mode: self._select_detail_scope(value)
            )
            self.scope_button_group.addButton(button)
            self.scope_buttons[mode] = button
            context_row.addWidget(button)
        self.scope_buttons["current"].setChecked(True)
        self.scope_buttons["first"].setEnabled(False)
        self.scope_buttons["second"].setEnabled(False)
        context_row.addStretch()
        context_row.addWidget(self.build_edit_control)
        self.current_scope_title = QLabel("현재 환경")
        context_row.addWidget(self.current_scope_title)
        self.current_scope_label = QLabel("未知")
        self.current_scope_label.setStyleSheet(
            themed_style("color:#58a6ff;font-weight:600")
        )
        context_row.addWidget(self.current_scope_label)
        self.environment_button = QPushButton("환경 확인")
        context_row.addWidget(self.environment_button)
        timeline_layout.addLayout(context_row)

        self.action_summary_label = QLabel("동작·입력 추정 대기 중", timeline_card)
        self.action_summary_label.hide()
        self.timeline = BattleUnifiedTimelineWidget(
            game_ui_asset_root=self._game_ui_asset_root
        )
        self.timeline.range_boundary_requested.connect(self._timeline_boundary_requested)
        self.timeline.selection_activated.connect(
            self._timeline_selection_activated
        )
        self.timeline_scroll = QScrollArea()
        self.timeline_scroll.setWidgetResizable(True)
        self.timeline_scroll.setFrameShape(QFrame.NoFrame)
        self.timeline_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.timeline_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.timeline_scroll.setWidget(self.timeline)
        self.timeline_scroll.horizontalScrollBar().valueChanged.connect(
            self.timeline.set_horizontal_view_offset
        )
        self.timeline.horizontal_pan_requested.connect(lambda delta: self.timeline_scroll.horizontalScrollBar().setValue(self.timeline_scroll.horizontalScrollBar().value() + delta))
        self.timeline.content_height_changed.connect(
            lambda height: self._fit_axis_height(self.timeline_scroll, height)
        )
        self._fit_axis_height(self.timeline_scroll, self.timeline.sizeHint().height())
        timeline_layout.addWidget(self.timeline_scroll)
        root.addWidget(timeline_card)

        roles_card, roles_layout = _section("선택 구간 캐릭터 기여")
        roles_content = QHBoxLayout()
        roles_content.setContentsMargins(0, 0, 0, 0)
        roles_content.setSpacing(18)
        self.roles_table = _table(
            ("캐릭터", "히트 / 정산", "유효 피해", "유효 DPS", "구간 비중"),
            180,
            default_widths=(155, 120, 112, 102, 126),
        )
        self.roles_table.setMinimumWidth(620)
        roles_content.addWidget(self.roles_table, 3)
        self.roles_pie = BattleRoleDamagePieWidget()
        roles_content.addWidget(self.roles_pie, 2)
        roles_layout.addLayout(roles_content)
        root.addWidget(roles_card)
        composition_card, composition_layout = _section("선택 구간 캐릭터 피해 구성")
        composition_controls = QHBoxLayout()
        composition_controls.addWidget(QLabel("분류 기준"))
        self.composition_group = QButtonGroup(self)
        self.composition_group.setExclusive(True)
        self.composition_buttons: dict[str, QPushButton] = {}
        for grouping, label in (("coarse", "대분류"), ("fine", "세분류")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, value=grouping: (
                    self._set_composition_grouping(value)
                )
            )
            self.composition_group.addButton(button)
            self.composition_buttons[grouping] = button
            composition_controls.addWidget(button)
        self.composition_buttons["coarse"].setChecked(True)
        self.composition_status_label = QLabel()
        self.composition_status_label.setStyleSheet(
            themed_style("color:#d29922;font-size:12px")
        )
        self.composition_status_label.hide()
        composition_controls.addWidget(self.composition_status_label)
        composition_controls.addStretch()
        self.composition_topple_button = QPushButton("정밀 브레이크 귀속 계산")
        self.composition_topple_button.clicked.connect(
            self._request_topple_attribution
        )
        self.composition_topple_button.hide()
        composition_controls.addWidget(self.composition_topple_button)
        composition_layout.addLayout(composition_controls)
        self.damage_composition_panel = BattleDamageCompositionPanel(
            game_ui_asset_root=self._game_ui_asset_root
        )
        composition_layout.addWidget(self.damage_composition_panel)
        root.addWidget(composition_card)

        audit_card, audit_layout = _section("감사")
        audit_row = QHBoxLayout()
        audit_row.setSpacing(10)
        self.audit_buttons: dict[str, QPushButton] = {}
        for key, label in (
            ("buff", "Buff"),
            ("skills", "스킬 상세"),
            ("hits", "히트별 로그"),
            ("targets", "대상"),
            ("marginal", "한계 이득 계산"),
        ):
            button = QPushButton(label)
            self.audit_buttons[key] = button
            audit_row.addWidget(button)
        audit_row.addStretch()
        audit_layout.addLayout(audit_row)
        self.inferred_fact_label = BattleInferredFactLabel()
        audit_layout.addWidget(self.inferred_fact_label)
        root.addWidget(audit_card)

        self.buff_dialog, buff_layout = self._audit_dialog("버프 감사")
        self.buff_panel = BattleBuffEvidencePanel()
        buff_layout.addWidget(self.buff_panel)
        self.skills_dialog, skills_layout = self._audit_dialog("스킬 상세")
        self.skills_table = _table(
            ("캐릭터", "피해 항목", "출처 스킬", "분류", "히트", "伤害", "비중"),
            250,
            default_widths=(130, 250, 230, 140, 86, 130, 92),
        )
        skills_layout.addWidget(self.skills_table)
        self.log_dialog, log_layout = self._audit_dialog("히트별 로그", QSize(1380, 820))
        filter_row = QHBoxLayout()
        self.log_filter = QLineEdit()
        self.log_filter.setPlaceholderText("캐릭터, 스킬, 피해 항목 또는 대상 필터")
        self.log_filter.textChanged.connect(self._reset_log_page)
        filter_row.addWidget(self.log_filter, 1)
        self.prev_button = QPushButton("이전 페이지")
        self.prev_button.pressed.connect(self._previous_log_page)
        filter_row.addWidget(self.prev_button)
        self.log_page_label = QLabel("0 / 0")
        filter_row.addWidget(self.log_page_label)
        self.next_button = QPushButton("다음 페이지")
        self.next_button.pressed.connect(self._next_log_page)
        filter_row.addWidget(self.next_button)
        log_layout.addLayout(filter_row)
        self.log_table = _table(
            (
                "시간", "序号", "캐릭터", "피해 항목 / 출처 스킬", "유형",
                "대상", "伤害", "공식 리플레이 / 오차", "치명 판정",
                "상세",
            ),
            360,
            default_widths=(
                112, 72, 140, 360, 230, 170, 130, 190, 110, 72,
            ),
        )
        self.log_table.cellClicked.connect(self._log_cell_clicked)
        log_layout.addWidget(self.log_table)
        self.target_dialog, target_layout = self._audit_dialog(
            "피격 대상과 HP 근거", QSize(1180, 780)
        )
        self.target_vital_panel = BattleTargetVitalPanel()
        target_layout.addWidget(self.target_vital_panel)
        self.environment_button.clicked.connect(
            self._open_environment_editor
        )
        self.audit_buttons["buff"].clicked.connect(
            lambda: self._request_detailed_analysis("buff")
        )
        self.audit_buttons["skills"].clicked.connect(
            lambda: self._open_lightweight_audit("skills")
        )
        self.audit_buttons["hits"].clicked.connect(
            lambda: self._request_detailed_analysis("hit")
        )
        self.audit_buttons["targets"].clicked.connect(
            lambda: self._open_lightweight_audit("targets")
        )
        self.audit_buttons["marginal"].clicked.connect(self.marginal_requested)

    def _audit_dialog(
        self,
        title: str,
        size: QSize = QSize(1080, 760),
    ) -> tuple[QDialog, QVBoxLayout]:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        fit_dialog_to_available_screen(dialog, size)
        return dialog, layout

    def clear(self, message: str = "현재 기록에는 집계 요약만 있어 정식 히트별 축이 아직 없습니다.") -> None:
        self._analysis = None
        self._hit_details = None
        self._current_composition = None
        self._topple_detail_requested_analysis = None
        self._analysis_record_id = None
        self._selected_character_id = None
        self.capability_label.setText(message)
        self.capability_label.show()
        self.action_summary_label.setText("현재 사용할 수 있는 동작 추정이 없습니다.")
        self.timeline.set_analysis(None)
        self.roles_pie.set_roles(())
        self.damage_composition_panel.clear()
        self.composition_status_label.hide()
        self.composition_topple_button.hide()
        self.buff_panel.clear()
        self.target_vital_panel.clear()
        self.inferred_fact_label.clear_facts()
        self._hide_hit_formula_dialog()
        self._hide_hit_buff_dialog()
        for table in (
            self.roles_table,
            self.skills_table,
            self.log_table,
        ):
            table.setRowCount(0)
        self.build_edit_control.set_state(
            has_edit=False,
            active=False,
            available=False,
        )
        self.current_scope_label.setText("未知")

    def set_loading(self, message: str) -> None:
        """Keep the previous projection visible while its replacement loads."""

        self.capability_label.setText(message)
        self.capability_label.show()

    def set_analysis(
        self,
        analysis: BattleAnalysisSnapshot,
        *,
        selected_character_id: int | None = None,
        hit_details=None,
    ) -> None:
        self._analysis_record_id = analysis.battle_record_id
        self._selected_character_id = selected_character_id
        self._analysis = analysis
        self._hit_details = hit_details
        self._hide_hit_formula_dialog()
        self._hide_hit_buff_dialog()
        self.timeline.set_analysis(analysis)
        self.timeline.set_time_mode(self._time_mode)
        self._focus_selected_timeline_range()
        self._render_time_presentation()
        self.capability_label.hide()
        self._render_damage_composition()
        self._render_roles()
        self._log_page = 0
        outer_buffs = tuple(dict.fromkeys(
            row.buff_name for row in analysis.timeline_buff_intervals
            if row.source_kind == "outer_realm_season_buff"
        ))
        outer_tip = (
            ""
            if not outer_buffs
            else "; 시즌 버프:" + "、".join(outer_buffs)
        )
        condition = analysis.target_condition
        self.environment_button.setEnabled(True)
        self.current_scope_label.setStyleSheet(
            themed_style("color:#58a6ff;font-weight:600")
        )
        if (
            condition is not None
            and condition.source_kind == "inferred_encounter_hp_injective_default"
        ):
            self.current_scope_label.setText(
                display_battle_environment_name(condition)
            )
            self.environment_button.setToolTip(
                (
                    getattr(analysis, "target_identity_inference_basis", "")
                    or "환경과 대상은 전체 조우의 대상별 초기 최대 HP로 추론했으며, 열어서 확인할 수 있습니다."
                ) + outer_tip
            )
        elif condition is not None:
            environment_name = display_battle_environment_name(condition)
            summary = environment_name
            if condition.feast_options:
                summary += f"; 쟁봉 보너스 {len(condition.feast_options)}개"
            if condition.witch_buff_name_zh:
                summary += f"; {condition.witch_buff_name_zh}"
            summary += outer_tip
            self.current_scope_label.setText(environment_name)
            self.environment_button.setToolTip(summary)
        elif getattr(analysis, "detected_environment_kind", ""):
            self.current_scope_label.setText(
                getattr(analysis, "detected_environment_name", "") or "추론된 환경"
            )
            self.environment_button.setToolTip(
                getattr(analysis, "target_identity_inference_basis", "")
                or "전투 리포트 근거로 환경을 추론했습니다. 열어서 구체적인 대상을 확인할 수 있습니다."
            )
        else:
            self.current_scope_label.setText("未知")
            self.environment_button.setToolTip("열어서 전투 환경과 대상을 확인하세요.")
        apply_inferred_scope_warning(self.current_scope_label, analysis, condition)

    def set_target_catalog(self, catalog: dict[str, object]) -> None:
        self.target_vital_panel.set_catalog(catalog)

    def _timeline_selection_activated(self, selected: object) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        if getattr(selected, "kind", None) != "hit":
            self._render_timeline_selection_detail(selected)
            return
        if not analysis.hit_replays:
            self.details_requested.emit("hit", selected)
            return
        self._render_timeline_selection_detail(selected)

    def _request_detailed_analysis(self, kind: str) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        if kind == "buff" and analysis.buff_counterfactual_model_version:
            self.complete_analysis_details(kind, None)
            return
        if kind == "hit" and analysis.hit_replays:
            self.complete_analysis_details(kind, None)
            return
        self.details_requested.emit(kind, None)

    def complete_analysis_details(self, kind: str, payload: object) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        if kind == "buff":
            self.buff_panel.render(analysis)
            self.buff_dialog.open()
        elif kind == "hit":
            if payload is None:
                self._render_log()
                self.log_dialog.open()
            else:
                self._render_timeline_selection_detail(payload)

    def _open_lightweight_audit(self, kind: str) -> None:
        if kind == "skills":
            self._render_skills()
            self.skills_dialog.open()
        elif kind == "targets":
            self._render_targets()
            self.target_dialog.open()

    def _open_environment_editor(self) -> None:
        self._render_targets()
        self.target_vital_panel.open_environment_dialog()

    def selected_range(self) -> tuple[int, int] | None:
        analysis = self._analysis
        if analysis is None:
            return None
        return analysis.range_start_us, analysis.range_end_us

    def detail_scope(self) -> str:
        return self._detail_scope

    def set_detail_scope(
        self,
        mode: str,
        *,
        first_available: bool,
        second_available: bool,
    ) -> None:
        self.scope_buttons["first"].setEnabled(first_available)
        self.scope_buttons["second"].setEnabled(second_available)
        button = self.scope_buttons.get(mode)
        if button is None or not button.isEnabled():
            mode = "current"
            button = self.scope_buttons[mode]
        self._detail_scope = mode
        button.setChecked(True)

    def _select_detail_scope(self, mode: str) -> None:
        button = self.scope_buttons.get(mode)
        if button is None or not button.isEnabled() or mode == self._detail_scope:
            return
        self._detail_scope = mode
        button.setChecked(True)
        self.detail_scope_changed.emit(mode)

    def selected_character_id(self) -> int | None:
        return self._selected_character_id

    @staticmethod
    def _fit_axis_height(scroll: QScrollArea, content_height: int) -> None:
        scrollbar_extent = scroll.style().pixelMetric(
            QStyle.PixelMetric.PM_ScrollBarExtent
        )
        scroll.setFixedHeight(
            max(1, int(content_height)) + scrollbar_extent + scroll.frameWidth() * 2
        )
    def _render_skills(self) -> None:
        analysis = self._analysis
        rows = analysis.skills if analysis else ()
        self.skills_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = (
                item.character_name,
                preferred_battle_damage_name(
                    item.damage_name,
                    item.skill_name,
                    item.ability_id,
                ),
                item.skill_name,
                render_battle_classification(item.classification),
                f"{item.hits:,}",
                _number(item.damage),
                f"{item.share_percent:.2f}%",
            )
            for column, value in enumerate(values):
                self.skills_table.setItem(row, column, QTableWidgetItem(value))
    def _render_targets(self) -> None:
        analysis = self._analysis
        if analysis is None or not hasattr(analysis, "target_condition"):
            self.target_vital_panel.clear()
            return
        self.target_vital_panel.render(
            analysis,
            projected_time=self._display_time_us,
        )

    def _time_mode_changed(self, _index: int = -1) -> None:
        value = self.time_mode_combo.currentData()
        self._time_mode = (
            ELAPSED_TIME_MODE if value == ELAPSED_TIME_MODE else ACTIVE_TIME_MODE
        )
        self.timeline.set_time_mode(self._time_mode)
        self._focus_selected_timeline_range()
        self._render_time_presentation()
        self._render_roles()
        if self.log_dialog.isVisible():
            self._render_log()
        if self.target_dialog.isVisible():
            self._render_targets()

    def _display_time_us(self, raw_time_us: int) -> int:
        analysis = self._analysis
        if analysis is None:
            return max(0, int(raw_time_us))
        return project_timeline_time_us(
            raw_time_us,
            battle_start_us=getattr(analysis, "battle_start_us", 0),
            intervals=getattr(analysis, "time_stop_intervals", ()),
            mode=self._time_mode,
        )

    def _selected_display_duration_seconds(self) -> float:
        analysis = self._analysis
        if analysis is None:
            return 0.0
        duration_us = projected_range_duration_us(
            analysis.range_start_us,
            analysis.range_end_us,
            intervals=analysis.time_stop_intervals,
            mode=self._time_mode,
        )
        return max(duration_us / 1_000_000.0, 0.001)

    def _render_time_presentation(self) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        start_display = self._display_time_us(analysis.range_start_us)
        end_display = self._display_time_us(analysis.range_end_us)
        mode_name = "시간 정지 제외" if self._time_mode == ACTIVE_TIME_MODE else "시간 정지 포함"
        capability_name = format_analysis_evidence(analysis)
        hit_replays = getattr(analysis, "hit_replays", ())
        selected_hits, selected_actions, selected_inputs = (
            self._selected_axis_evidence()
        )
        replayed = sum(row.selected_damage is not None for row in hit_replays)
        crit_resolved = sum(
            row.critical_state in {"critical", "non_critical"}
            for row in hit_replays
        )
        self.capability_label.setText(
            f"{capability_name} · "
            f"{mode_name} {_time(start_display)}—{_time(end_display)} · "
            f"피해 모델 {analysis.formula_model_version} · "
            f"입력 투영 {len(selected_inputs)}개 / "
            f"동작 {len(selected_actions)}개 · "
            f"공식 리플레이 {replayed}/{len(hit_replays)} · "
            f"치명 판정 {crit_resolved} · "
            f"{getattr(analysis, 'hit_replay_model_version', '') or '公式按需加载'} · "
            f"{getattr(analysis, 'timeline_projection_version', '未生成时间轴')} · "
            f"{getattr(analysis, 'target_vital_model_version', '未生成生命轴')} · "
            f"{getattr(analysis, 'buff_inference_version', '') or 'Buff 按需加载'} · "
            f"{getattr(analysis, 'buff_attribute_projection_version', '') or 'Buff 投影按需加载'}"
        )
        self.inferred_fact_label.render_facts(
            tuple(getattr(analysis, "inferred_character_facts", ()))
        )
        action_event_ids = {
            event_id
            for action in selected_actions
            for event_id in action.evidence_event_ids
        }
        outgoing = tuple(
            hit
            for hit in selected_hits
            if hit.direction == "outgoing"
        )
        covered_damage = sum(
            hit.damage for hit in outgoing if hit.event_id in action_event_ids
        )
        outgoing_damage = sum(hit.damage for hit in outgoing)
        event_coverage = (
            len(action_event_ids) / len(outgoing) * 100.0 if outgoing else 0.0
        )
        damage_coverage = (
            covered_damage / outgoing_damage * 100.0 if outgoing_damage else 0.0
        )
        zoom_percent = round(float(self.zoom_combo.currentData() or 1.0) * 100)
        self.action_summary_label.setText(
            f"{mode_name} · {format_time_stop_evidence(analysis)} · "
            f"줌 {zoom_percent}% · 현재 구간 추정 입력 "
            f"{len(selected_inputs):,}개 / "
            f"{len(selected_actions):,}개 · 참조한 피해 이벤트 "
            f"{len(action_event_ids):,}/{len(outgoing):,} ({event_coverage:.1f}%) · "
            f"피해 커버리지 {damage_coverage:.1f}%. 커버리지는 모델에 들어간 근거만 나타내며 동작 정확도를 뜻하지 않습니다."
        )
        self.start_spin.setValue(start_display / 1_000_000.0)
        self.end_spin.setValue(end_display / 1_000_000.0)

    def _zoom_changed(self, _index: int = -1) -> None:
        factor = float(self.zoom_combo.currentData() or 1.0)
        scrollbar = self.timeline_scroll.horizontalScrollBar()
        viewport = self.timeline_scroll.viewport()
        center_time_us = self.timeline.display_time_at_widget_x(
            scrollbar.value() + viewport.width() / 2.0
        )
        self.timeline.set_zoom_factor(factor)

        def restore_center_time() -> None:
            try:
                target_x = self.timeline.widget_x_for_display_time(center_time_us)
                scrollbar.setValue(round(target_x - viewport.width() / 2.0))
            except RuntimeError:
                # 组合框信号排队后，页面可能已经随测试或导航被销毁。
                return

        QTimer.singleShot(0, restore_center_time)
        self._render_time_presentation()

    def _request_range(self) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        start = unproject_timeline_time_us(
            round(self.start_spin.value() * 1_000_000),
            battle_start_us=analysis.battle_start_us,
            battle_end_us=analysis.battle_end_us,
            intervals=analysis.time_stop_intervals,
            mode=self._time_mode,
        )
        end = unproject_timeline_time_us(
            round(self.end_spin.value() * 1_000_000),
            battle_start_us=analysis.battle_start_us,
            battle_end_us=analysis.battle_end_us,
            intervals=analysis.time_stop_intervals,
            mode=self._time_mode,
            prefer_interval_end=True,
        )
        if end > start:
            self.range_requested.emit(start, end)

    def _timeline_boundary_requested(self, boundary: str, raw_time_us: int) -> None:
        spin = self.start_spin if boundary == "start" else self.end_spin
        spin.setValue(self._display_time_us(raw_time_us) / 1_000_000.0)
        self._request_range()
