# 在战报页内展示固定轴配置反事实，并复用角色页完整配置编辑器。
"""Dedicated fixed-axis marginal page for one battle report."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.dialogs import show_help
from src.domain.battle_report import (
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
)
from src.features.battle_report.analysis_components import (
    analysis_section,
    analysis_table,
)
from src.features.battle_report.build_change_summary import (
    character_level_summary,
    fork_level_summary,
)
from src.features.battle_report.composition_view import BattleDamageCompositionPanel
from src.features.battle_report.hit_formula_dialog import BattleHitFormulaDialog
from src.features.battle_report.marginal_derived_settlement_view import BattleMarginalDerivedSettlementView
from src.features.battle_report.marginal_buff_render_mixin import (
    BattleMarginalBuffRenderMixin,
)
from src.features.battle_report.marginal_benefit_view import (
    build_marginal_benefit_sections,
    render_marginal_benefits,
)
from src.features.battle_report.marginal_character_panel import (
    BattleMarginalCharacterPanel,
    render_character_panel_and_margins,
)
from src.features.battle_report.marginal_result_table_view import (
    BUFF_BENEFIT_HEADERS,
    BUFF_BENEFIT_WIDTHS,
    display_projection,
)
from src.features.battle_report.marginal_toolbar import build_marginal_toolbar
from src.features.battle_report.marginal_replacement_controller import (
    show_marginal_equipment_replacement,
)
from src.features.battle_report.role_contribution_view import (
    BattleRoleDamagePieWidget,
    render_counterfactual_roles,
)
from src.features.battle_report.timeline_layout import TimelineSelection
from src.features.battle_report.timeline_view import BattleUnifiedTimelineWidget
from src.features.official_role.profile_editor import OfficialRoleProfileEditor
from src.services.battle_build_equipment_service import freeze_equipment_context
from src.services.battle_marginal_candidate_service import (
    BattleMarginalCandidateService,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    ELAPSED_TIME_MODE,
)
from src.ui.dashboard_widgets import metric_card
from src.ui.widgets import NoWheelComboBox


def _number(value: float) -> str:
    return f"{value:,.0f}"


class BattleMarginalPage(BattleMarginalBuffRenderMixin, QWidget):
    """Edit one memory-only candidate and replay the selected role's battle half."""

    back_requested = Signal()
    recalculate_requested = Signal(object)
    reset_requested = Signal()
    draft_changed = Signal()
    role_changed = Signal(object)

    def __init__(self, *, game_ui_asset_root=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._game_ui_asset_root = game_ui_asset_root
        self._analysis: BattleAnalysisSnapshot | None = None
        self._candidate_analysis: BattleAnalysisSnapshot | None = None
        self._marginal_benefits = None
        self._marginal_panel = None
        self._details: list[dict] = []
        self._editors: list[OfficialRoleProfileEditor | None] = []
        self._editor_character_ids: list[int] = []
        self._equipment_editable = True
        self._inferred_fact_ids: tuple[str, ...] = ()
        self._draft_dirty = False
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        toolbar = build_marginal_toolbar(
            back=self.back_requested.emit,
            role_changed=self._character_changed,
            inferred_toggled=self._mark_draft_changed,
            reset=self._reset_draft,
            recalculate=self._request_recalculate,
        )
        self.character_combo = toolbar.character_combo
        self.change_summary = toolbar.change_summary
        self.use_inferred_facts = toolbar.use_inferred_facts
        self.reset_button = toolbar.reset_button
        self.recalculate_button = toolbar.recalculate_button
        outer.addWidget(toolbar.widget)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        root = QVBoxLayout(content)
        root.setContentsMargins(22, 14, 22, 22)
        root.setSpacing(14)

        metrics = QGridLayout()
        definitions = (
            ("dps", "현재/후보 DPS", "고정축 추정"),
            ("damage", "현재/후보 총 피해", "현재 적용 기준선 대비"),
            ("role", "캐릭터 피해", "현재 분석 캐릭터"),
            ("structured", "구조화 리플레이", "나머지는 등급별 추정"),
        )
        self.metric_labels: dict[str, QLabel] = {}
        self.metric_subtitles: dict[str, QLabel] = {}
        for column, (key, title, subtitle) in enumerate(definitions):
            card, value, sub = metric_card(title, "—", subtitle)
            self.metric_labels[key] = value
            self.metric_subtitles[key] = sub
            metrics.addWidget(card, 0, column)
        root.addLayout(metrics)

        self.derived_settlements = BattleMarginalDerivedSettlementView()
        self.derived_settlements.hit_activated.connect(
            lambda event_id: self._open_counterfactual_hit(TimelineSelection("hit", event_id, None))
        )
        root.addWidget(self.derived_settlements)

        timeline_card, timeline_layout = analysis_section("조정 후 히트 축")
        timeline_controls = QHBoxLayout()
        timeline_controls.addWidget(QLabel("기준"))
        self.timeline_time_mode_combo = NoWheelComboBox()
        self.timeline_time_mode_combo.addItem("시간 정지 제외", ACTIVE_TIME_MODE)
        self.timeline_time_mode_combo.addItem("시간 정지 포함", ELAPSED_TIME_MODE)
        self.timeline_time_mode_combo.setCurrentIndex(
            self.timeline_time_mode_combo.findData(ELAPSED_TIME_MODE)
        )
        self.timeline_time_mode_combo.currentIndexChanged.connect(
            self._timeline_time_mode_changed
        )
        timeline_controls.addWidget(self.timeline_time_mode_combo)
        timeline_controls.addWidget(QLabel("줌"))
        self.timeline_zoom_combo = NoWheelComboBox()
        for percent in (10, 25, 50, 100, 200, 300, 400, 500, 600, 700, 800):
            self.timeline_zoom_combo.addItem(f"{percent}%", percent / 100.0)
        self.timeline_zoom_combo.setCurrentIndex(
            self.timeline_zoom_combo.findData(1.0)
        )
        self.timeline_zoom_combo.currentIndexChanged.connect(
            self._timeline_zoom_changed
        )
        timeline_controls.addWidget(self.timeline_zoom_combo)
        timeline_controls.addStretch()
        timeline_help_text = (
            "원본 전투 리포트의 동작·히트·시간 구간을 그대로 사용합니다. 완전 또는 부분 정량화 항목은 해당 투영값을 사용하고,"
            "미정량화 항목은 원본 축 수치를 유지하며 피해 이름에 원본 축 플레이스홀더로 표시합니다."
            "HP 상한 정산은 원본 캐릭터 귀속을 유지합니다. 라크리모사 5각성은 악몽 피해를 따르고,"
            "파디아 패시브는 고유 HP를 따르며, 근거가 부족할 때만 원래 값으로 고정합니다."
        )
        timeline_help = QPushButton("?")
        timeline_help.setObjectName("btnHelp")
        timeline_help.setToolTip(timeline_help_text)
        timeline_help.clicked.connect(
            lambda _checked=False, button=timeline_help: show_help(
                button,
                "조정 후 히트 축",
                timeline_help_text,
            )
        )
        timeline_controls.addWidget(timeline_help)
        timeline_layout.addLayout(timeline_controls)
        self.counterfactual_timeline = BattleUnifiedTimelineWidget(
            game_ui_asset_root=self._game_ui_asset_root
        )
        self.counterfactual_timeline.set_hit_heading(
            "조정 후 히트 (미정량화 항목은 원본 축 플레이스홀더)"
        )
        self.counterfactual_timeline.selection_activated.connect(
            self._open_counterfactual_hit
        )
        self.counterfactual_timeline_scroll = QScrollArea()
        self.counterfactual_timeline_scroll.setWidgetResizable(True)
        self.counterfactual_timeline_scroll.setFrameShape(QFrame.NoFrame)
        self.counterfactual_timeline_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAsNeeded
        )
        self.counterfactual_timeline_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        self.counterfactual_timeline_scroll.setWidget(self.counterfactual_timeline)
        self.counterfactual_timeline_scroll.horizontalScrollBar().valueChanged.connect(
            self.counterfactual_timeline.set_horizontal_view_offset
        )
        self.counterfactual_timeline.horizontal_pan_requested.connect(
            self._pan_counterfactual_timeline
        )
        self.counterfactual_timeline.content_height_changed.connect(
            self._fit_counterfactual_timeline_height
        )
        self._fit_counterfactual_timeline_height(
            self.counterfactual_timeline.sizeHint().height()
        )
        timeline_layout.addWidget(self.counterfactual_timeline_scroll)
        root.addWidget(timeline_card)
        self.character_panel = BattleMarginalCharacterPanel()
        root.addWidget(self.character_panel)
        attribute_card, attribute_layout = analysis_section("드라이브 서브 스탯 단위 한계 이득")
        attribute_note = QLabel(
            "실제로 나올 수 있는 금색 드라이브 서브 스탯만 표시하며, 각 행의 기본 단위는 1칸입니다. 패널 속성은 현재 적용 기준선이고,"
            "피해 가중 현재 패널 속성은 공식 패널 연동 피해가 발생한 시점의 동적 속성으로 가중합니다."
            "링코 패널이 제어하는 팀원의 동조 피해도 여기에 포함되므로 패널 연동 피해가 상단의 원본 캐릭터 피해보다 클 수 있습니다."
            "Core 원본 피해 귀속은 변경하지 않습니다."
        )
        attribute_note.setStyleSheet(
            themed_style("color:#8b949e;font-size:12px")
        )
        attribute_note.setWordWrap(True)
        attribute_layout.addWidget(attribute_note)
        self.attribute_table = analysis_table(
            (
                "속성 단위",
                "패널 속성",
                "피해 가중 현재 패널 속성",
                "패널 연동 이득",
                "팀 전체 기대 이득",
                "연동 패널 피해",
                "패널 연동 피해",
                "연동 팀 전체 피해",
            ),
            280,
            default_widths=(220, 125, 165, 145, 145, 135, 120, 145),
        )
        attribute_layout.addWidget(self.attribute_table)
        root.addWidget(attribute_card)

        (
            self.core_main_table,
            self.core_main_notice,
            self.fork_benefit_panel,
            self.fork_benefit_table,
            self.fork_benefit_notice,
        ) = build_marginal_benefit_sections(root)
        buff_card, buff_layout = analysis_section("팀 Buff 한계 이득")
        buff_note = QLabel(
            "Buff를 하나씩 개별 제거하고, 실제로 피해를 입힌 캐릭터별로 이득을 나눕니다."
            "캐릭터 이득을 합하면 해당 Buff의 팀 전체 이득이 되지만, 서로 다른 Buff끼리는 직접 합산할 수 없습니다."
            "정식 히트 인과 근거가 있는 메커니즘 패시브도 여기서 출처 캐릭터별로 병합해 표시합니다."
            "피해 커버리지는 고정축 유효 피해를 집계하며, 커버된 히트와 연동된 HP 상한 정산을 포함합니다."
        )
        buff_note.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        buff_note.setWordWrap(True)
        buff_layout.addWidget(buff_note)
        self.buff_benefit_table = analysis_table(
            BUFF_BENEFIT_HEADERS,
            240,
            default_widths=BUFF_BENEFIT_WIDTHS,
        )
        buff_layout.addWidget(self.buff_benefit_table)
        root.addWidget(buff_card)

        editor_card, editor_layout = analysis_section("캐릭터 설정")
        self.editor_stack = QStackedWidget()
        editor_layout.addWidget(self.editor_stack)
        root.addWidget(editor_card)

        roles_card, roles_layout = analysis_section("재계산 후 캐릭터 기여")
        roles_row = QHBoxLayout()
        self.roles_table = analysis_table(
            ("캐릭터", "새 피해", "새 비중", "캐릭터 이득"),
            180,
            default_widths=(180, 150, 150, 140),
        )
        roles_row.addWidget(self.roles_table, 3)
        self.roles_pie = BattleRoleDamagePieWidget()
        roles_row.addWidget(self.roles_pie, 2)
        roles_layout.addLayout(roles_row)
        root.addWidget(roles_card)

        composition_card, composition_layout = analysis_section("재계산 후 캐릭터 피해 구성")
        self.composition_panel = BattleDamageCompositionPanel(
            game_ui_asset_root=self._game_ui_asset_root
        )
        composition_layout.addWidget(self.composition_panel)
        root.addWidget(composition_card)
        root.addStretch()

    def set_editor_data(
        self,
        editor_data: dict,
        *,
        selected_character_id: int | None = None,
    ) -> None:
        self._draft_dirty = False
        self._load_editor_data(
            editor_data,
            selected_character_id=selected_character_id,
        )

    def _load_editor_data(
        self,
        editor_data: dict,
        *,
        selected_character_id: int | None = None,
    ) -> None:
        self.fork_benefit_panel.setParent(None)
        while self.editor_stack.count():
            widget = self.editor_stack.widget(0)
            self.editor_stack.removeWidget(widget)
            widget.deleteLater()
        self._editors.clear()
        self._details.clear()
        self._editor_character_ids.clear()
        self._equipment_editable = bool(
            editor_data.get("marginal_equipment_editable", True)
        )
        self._inferred_fact_ids = tuple(
            str(getattr(fact, "fact_id", ""))
            for fact in editor_data.get("inferred_character_facts") or ()
            if str(getattr(fact, "fact_id", ""))
        )
        self.use_inferred_facts.blockSignals(True)
        self.use_inferred_facts.setChecked(True)
        self.use_inferred_facts.blockSignals(False)
        self.use_inferred_facts.setVisible(bool(self._inferred_fact_ids))
        self.character_combo.blockSignals(True)
        self.character_combo.clear()
        for detail in editor_data.get("details") or ():
            character_id = int(detail["character"]["character_id"])
            name = str(detail["character"].get("name_zh") or character_id)
            self._details.append(detail)
            self._editors.append(None)
            self._editor_character_ids.append(character_id)
            self.editor_stack.addWidget(QWidget())
            self.character_combo.addItem(name, character_id)
        if selected_character_id is not None:
            selected_index = self.character_combo.findData(selected_character_id)
            if selected_index >= 0:
                self.character_combo.setCurrentIndex(selected_index)
        self.character_combo.blockSignals(False)
        self._character_changed(notify=False)

    def clear_candidate(self) -> None:
        self._load_editor_data({"details": [], "marginal_equipment_editable": True})
        self._draft_dirty = False
        self._analysis = None
        self._hit_details = None
        self._candidate_analysis = None
        self._marginal_benefits = None
        self._marginal_panel = None
        for label in self.metric_labels.values():
            label.setText("—")
        for key, text in {
            "dps": "고정축 추정",
            "damage": "현재 적용 기준선 대비",
            "role": "현재 분석 캐릭터",
            "structured": "나머지는 등급별 추정",
        }.items():
            self.metric_subtitles[key].setText(text)
        self.counterfactual_timeline.set_analysis(None)
        self.composition_panel.clear()
        self.character_panel.clear()
        self.attribute_table.setRowCount(0)
        self.core_main_table.setRowCount(0)
        self.fork_benefit_table.setRowCount(0)
        self.buff_benefit_table.setRowCount(0)
        self.roles_table.setRowCount(0)
        self.roles_pie.set_roles(())
        self.derived_settlements.render(None)

    def set_source_analysis(self, analysis: BattleAnalysisSnapshot, *, hit_details=None) -> None:
        self._marginal_benefits = None
        self._marginal_panel = None
        self._render_analysis(analysis, hit_details=hit_details)

    def set_marginal_result(
        self,
        analysis: BattleAnalysisSnapshot,
        *,
        marginal_benefits=None,
        marginal_panel=None,
        candidate_display_analysis: BattleAnalysisSnapshot | None = None,
        hit_details=None,
    ) -> None:
        self._draft_dirty = False
        self._marginal_benefits = marginal_benefits
        self._marginal_panel = marginal_panel
        details = None if hit_details is None else (hit_details.candidate if candidate_display_analysis is not None else hit_details.analysis)
        self._render_analysis(analysis, candidate_display_analysis, hit_details=details)

    def _render_analysis(
        self, analysis: BattleAnalysisSnapshot,
        candidate_display_analysis: BattleAnalysisSnapshot | None = None,
        hit_details=None,
    ) -> None:
        self._analysis = analysis
        self._hit_details = hit_details
        comparison = analysis.build_counterfactual
        self.derived_settlements.render(comparison)
        if comparison is None:
            self._candidate_analysis = analysis
            self.counterfactual_timeline.set_analysis(analysis)
            self.metric_labels["dps"].setText(_number(analysis.effective_dps))
            self.metric_labels["damage"].setText(_number(analysis.effective_damage))
            self.metric_subtitles["damage"].setText(
                "+0.00% · 현재 적용 기준선 (이번에 수정 없음)"
            )
            self.metric_labels["structured"].setText("—")
            self.roles_table.setRowCount(0)
            self.roles_pie.set_roles(())
            self.composition_panel.clear()
        else:
            self._candidate_analysis = candidate_display_analysis
            self.counterfactual_timeline.set_analysis(self._candidate_analysis)
            projected_damage = display_projection(
                candidate=comparison.candidate_damage,
                heuristic=comparison.heuristic_projection_damage,
                known=comparison.known_projection_damage,
            )
            projected_dps = display_projection(
                candidate=comparison.candidate_dps,
                heuristic=comparison.heuristic_projection_dps,
                known=comparison.known_projection_dps,
            )
            gain = (
                None
                if projected_damage is None or not comparison.baseline_damage
                else (
                    projected_damage / comparison.baseline_damage - 1.0
                ) * 100.0
            )
            self.metric_labels["dps"].setText(
                "—" if projected_dps is None else _number(projected_dps)
            )
            self.metric_labels["damage"].setText(
                "—" if projected_damage is None else _number(projected_damage)
            )
            self.metric_subtitles["damage"].setText(
                "후보 재계산 대기 중"
                if gain is None
                else f"{gain:+.2f}% · 기준선 {_number(comparison.baseline_damage)}"
            )
            self.metric_labels["structured"].setText(
                f"{comparison.structured_percent:.1f}%"
            )
            self.metric_subtitles["structured"].setText(
                f"추정 {max(0.0, 100.0 - comparison.structured_percent):.1f}%"
            )
            render_counterfactual_roles(
                self.roles_table, self.roles_pie,
                comparison.roles, projected_damage,
            )
            if comparison.quantification.status == "unavailable":
                self.composition_panel.clear()
            else:
                self.composition_panel.render(comparison.composition)
        self._render_selected_role()

    def _timeline_time_mode_changed(self, _index: int = -1) -> None:
        mode = str(self.timeline_time_mode_combo.currentData() or ACTIVE_TIME_MODE)
        self.counterfactual_timeline.set_time_mode(mode)

    def _timeline_zoom_changed(self, _index: int = -1) -> None:
        factor = float(self.timeline_zoom_combo.currentData() or 1.0)
        self.counterfactual_timeline.set_zoom_factor(factor)

    def _pan_counterfactual_timeline(self, delta: int) -> None:
        scrollbar = self.counterfactual_timeline_scroll.horizontalScrollBar()
        scrollbar.setValue(scrollbar.value() + delta)

    def _fit_counterfactual_timeline_height(self, height: int) -> None:
        scrollbar = self.counterfactual_timeline_scroll.horizontalScrollBar()
        scrollbar_height = scrollbar.sizeHint().height()
        self.counterfactual_timeline_scroll.setFixedHeight(
            max(300, int(height)) + scrollbar_height + 2
        )

    def _open_counterfactual_hit(self, selection: TimelineSelection) -> None:
        analysis = self._analysis
        candidate = self._candidate_analysis
        if analysis is None or candidate is None or selection.kind != "hit":
            return
        comparison = analysis.build_counterfactual
        comparison_hits = comparison.hits if comparison is not None else ()
        related_counterfactuals = tuple(
            row
            for row in comparison_hits
            if row.source_event_id == selection.item_id
        )
        projection = next(
            (row for row in comparison_hits if row.event_id == selection.item_id),
            None,
        )
        original_hit = next(
            (
                row
                for snapshot in (analysis, candidate)
                for row in (*snapshot.hits, *snapshot.timeline_hits)
                if row.event_id == selection.item_id
            ),
            selection.payload,
        )
        if getattr(original_hit, "event_id", None) != selection.item_id:
            return
        replay = next(
            (
                row
                for snapshot in (analysis, candidate)
                for row in snapshot.hit_replays
                if row.event_id == selection.item_id
            ),
            None,
        )
        details = getattr(self, "_hit_details", None)
        buff_projection, active_buffs = ((None, ()) if details is None
                                         else details.for_hit(original_hit, formula=True))
        dialog = getattr(self, "_counterfactual_hit_dialog", None)
        if dialog is None:
            dialog = BattleHitFormulaDialog(self)
            dialog.setWindowTitle("한계 이득 히트별 상세")
            self._counterfactual_hit_dialog = dialog
        dialog.show_for_hit(
            original_hit,
            replay,
            active_buffs=active_buffs,
            counterfactual=projection,
            related_counterfactuals=related_counterfactuals,
            related_analysis=candidate,
            projection=buff_projection, related_hit_details=details,
        )

    def profiles(self) -> list[dict]:
        profiles = []
        for index, character_id in enumerate(self._editor_character_ids):
            editor = self._editors[index]
            detail = self._details[index]
            if editor is None:
                profile = dict(detail.get("profile") or {})
                context_key = str(
                    detail.get("selected_equipment_context_key") or "battle"
                )
                context = (detail.get("equipment_contexts") or {}).get(context_key)
                selection = None if context is None else (context_key, context)
            else:
                profile = editor.profile()
                selection = editor.selected_equipment_context()
            profile.pop("battle_stat_overrides", None)
            if selection is None:
                raise ValueError("전투 리포트 한계 이득 후보에 계산 장비 세팅이 없습니다")
            context_key, context = selection
            profile.update({
                "equipment_context_key": context_key,
                "equipment_context_title": str(
                    context.get("source_title") or context.get("title") or "전투 리포트 장비 세팅 사본"
                ),
                "equipment_source_kind": str(context.get("source_kind") or "edited_copy"),
                "equipment_override": freeze_equipment_context(context),
            })
            profiles.append(profile)
        return profiles

    def selected_character_id(self) -> int | None:
        value = self.character_combo.currentData()
        return None if value is None else int(value)

    def selected_detail_scope(self) -> str | None:
        index = self.character_combo.currentIndex()
        if not 0 <= index < len(self._details):
            return None
        scope = self._details[index].get("analysis_detail_scope")
        return str(scope) if scope in {"first", "second"} else None

    def equipment_editable(self) -> bool:
        return self._equipment_editable
    def has_current_panel(self) -> bool:
        return (self._analysis is not None and self._marginal_panel is not None
                and self._marginal_panel.character_id == self.selected_character_id())

    def allows_automatic_recalculation(self) -> bool:
        return not self._draft_dirty

    def disabled_inferred_fact_ids(self) -> tuple[str, ...]:
        return () if self.use_inferred_facts.isChecked() else self._inferred_fact_ids

    def _character_changed(self, _index: int = -1, *, notify: bool = True) -> None:
        index = self.character_combo.currentIndex()
        if 0 <= index < self.editor_stack.count():
            editor = self._ensure_editor(index)
            if hasattr(editor, "set_fork_analysis_widget"):
                editor.set_fork_analysis_widget(self.fork_benefit_panel)
            self.editor_stack.setCurrentIndex(index)
        self._render_selected_role()
        self._refresh_change_summary()
        if notify:
            self.role_changed.emit(self.selected_detail_scope())

    def _refresh_change_summary(self, *_args) -> None:
        index = self.character_combo.currentIndex()
        if not 0 <= index < len(self._editors):
            self.change_summary.setText("캐릭터 구성 대기 중")
            self.change_summary.setToolTip("")
            return
        editor = self._ensure_editor(index)
        try:
            profile = editor.profile()
            equipment = editor.selected_equipment_context()
        except (KeyError, TypeError, ValueError):
            self.change_summary.setText("현재 후보가 아직 완전하지 않음")
            self.change_summary.setToolTip("캐릭터 육성과 고정 가능한 장비 세팅 선택을 완료하세요.")
            return
        awakening_count = len(profile.get("selected_awaken_effect_ids") or ())
        skill_levels = tuple(
            int(value) for value in (profile.get("skill_levels") or {}).values()
        )
        items = tuple((equipment or ("", {}))[1].get("items") or ())
        core_count = sum(str(item.get("kind") or "") == "core" for item in items)
        drive_count = sum(str(item.get("kind") or "") != "core" for item in items)
        parts = [
            character_level_summary(self._details[index], profile),
            f"{awakening_count}각성",
            "호감도 10" if profile.get("likeability_level_10_enabled") else "호감도 10 미적용",
            fork_level_summary(self._details[index], profile),
            "스킬 " + ("/".join(str(value) for value in skill_levels) or "미설정"),
            f"콘솔 {core_count}/드라이브 {drive_count}",
        ]
        summary = " · ".join(parts)
        if self._draft_dirty:
            summary += " · 구성이 변경됨, 재계산 대기"
        self.change_summary.setText(summary)
        self.change_summary.setToolTip("현재 후보:" + summary)

    def _ensure_editor(self, index: int) -> OfficialRoleProfileEditor:
        existing = self._editors[index]
        if existing is not None:
            return existing
        host = self.window()
        editor = OfficialRoleProfileEditor(
            self._details[index],
            self,
            include_analysis=False,
            include_equipment=True,
            show_fork_direct_damage_margin=False,
            allow_equipment_replacement=self._equipment_editable,
            show_equipment_context_selector=False,
            equipment_replacement_handler=(
                lambda target, context_key, current=index: (
                    self._replace_equipment(current, target, context_key)
                )
            ),
            scoring_engine=getattr(host, "scoring_engine", None),
            shape_areas=getattr(host, "_shape_areas", {}),
        )
        placeholder = self.editor_stack.widget(index)
        self.editor_stack.removeWidget(placeholder)
        placeholder.deleteLater()
        self.editor_stack.insertWidget(index, editor)
        self._editors[index] = editor
        editor.changed.connect(self._mark_draft_changed)
        return editor

    def _replace_equipment(
        self,
        index: int,
        target: dict,
        context_key: str,
    ) -> bool:
        if not self._equipment_editable or not 0 <= index < len(self._details):
            return False
        detail = self._details[index]
        context = (detail.get("equipment_contexts") or {}).get(context_key)
        if not isinstance(context, dict):
            return False

        def apply_replacement(replacement) -> None:
            BattleMarginalCandidateService.replace_equipment(
                context,
                target,
                replacement,
            )

        accepted = show_marginal_equipment_replacement(
            self,
            detail,
            target,
            context_key=context_key,
            on_replaced=apply_replacement,
        )
        if accepted:
            self._mark_draft_changed()
        return accepted

    def _mark_draft_changed(self, *_args) -> None:
        if not self._details:
            return
        self._draft_dirty = True
        self._refresh_change_summary()
        self.draft_changed.emit()

    def _reset_draft(self) -> None:
        self.reset_requested.emit()

    def _render_selected_role(self) -> None:
        analysis = self._analysis
        character_id = self.selected_character_id()
        if analysis is None or character_id is None:
            self.character_panel.clear()
            self.attribute_table.setRowCount(0)
            self.buff_benefit_table.setRowCount(0)
            self.metric_labels["role"].setText("—")
            return
        baseline = next(
            (row for row in analysis.baselines if row.character_id == character_id),
            None,
        )
        self._render_attributes(baseline)
        self._render_buff_benefits(
            analysis.buff_counterfactuals,
            passive_results=getattr(analysis, "passive_counterfactuals", ()),
        )
        render_marginal_benefits(
            self.core_main_table,
            self.core_main_notice,
            self.fork_benefit_table,
            self.fork_benefit_notice,
            self._marginal_benefits,
            character_id=character_id,
        )
        comparison = analysis.build_counterfactual
        role = next(
            (row for row in (comparison.roles if comparison else ()) if row.character_id == character_id),
            None,
        )
        if role is None:
            original = next(
                (row for row in analysis.roles if row.character_id == character_id),
                None,
            )
            self.metric_labels["role"].setText(
                "—" if original is None else _number(original.damage)
            )
            self.metric_subtitles["role"].setText(
                "+0.00% · 현재 적용 기준선 (이번에 수정 없음)"
            )
        else:
            projected_damage = display_projection(
                candidate=role.candidate_damage,
                heuristic=role.heuristic_projection_damage,
                known=role.known_projection_damage,
            )
            gain = (
                None
                if projected_damage is None or not role.baseline_damage
                else (projected_damage / role.baseline_damage - 1.0) * 100.0
            )
            self.metric_labels["role"].setText(
                "—" if projected_damage is None else _number(projected_damage)
            )
            self.metric_subtitles["role"].setText(
                "후보 재계산 대기 중"
                if gain is None
                else f"{gain:+.2f}% · 기준선 {_number(role.baseline_damage)}"
            )

    def _render_attributes(self, baseline: BattleCharacterBaseline | None) -> None:
        render_character_panel_and_margins(
            self.character_panel,
            self.attribute_table,
            analysis=self._analysis,
            baseline=baseline,
            marginal_panel=self._marginal_panel,
        )

    def _request_recalculate(self) -> None:
        self._refresh_change_summary()
        self.recalculate_requested.emit(self.profiles())
