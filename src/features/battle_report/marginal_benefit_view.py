# 构建并渲染金色空幕主属性与弧盘固定轴综合收益表。
"""Qt presentation for selected-role equipment marginal benefits."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.domain.battle_counterfactual_quantification import QuantificationStatus
from src.domain.battle_marginal_benefit import (
    BattleForkMarginal,
    BattleMarginalBenefits,
    BattleMarginalDelta,
)
from src.features.battle_report.analysis_components import (
    analysis_section,
    analysis_table,
)
from src.features.battle_report.marginal_quantification_view import (
    quantification_status_text,
)


def build_marginal_benefit_sections(
    root: QVBoxLayout,
) -> tuple[QTableWidget, QLabel, QWidget, QTableWidget, QLabel]:
    core_card, core_layout = analysis_section("콘솔 메인 속성 한계 이득 (금색 후보)")
    core_note = QLabel(
        "현재 임의 품질의 콘솔을 인식해 세트와 서브 스탯을 고정하고, 후보는 일괄 금색 만렙 메인 속성을 사용합니다."
        "“메인 속성 없음 대비”는 일괄 비교용이고, “현재 교체”는 실제 장비 교체 결정용입니다."
    )
    _style_note(core_note)
    core_layout.addWidget(core_note)
    core_notice = QLabel("백그라운드 계산 대기 중…")
    _style_note(core_notice)
    core_layout.addWidget(core_notice)
    core_table = analysis_table(
        (
            "금색 메인 속성",
            "메인 속성 값",
            "메인 속성 없음 대비·캐릭터",
            "메인 속성 없음 대비·팀 전체",
            "현재 교체·캐릭터",
            "현재 교체·팀 전체",
            "후보 캐릭터 피해",
            "후보 팀 전체 피해",
            "정량화 상태",
        ),
        250,
        default_widths=(190, 110, 165, 165, 155, 155, 145, 145, 190),
    )
    core_layout.addWidget(core_table)
    root.addWidget(core_card)

    fork_panel = QWidget()
    fork_panel.setObjectName("battleForkBenefitPanel")
    fork_layout = QVBoxLayout(fork_panel)
    fork_layout.setContentsMargins(0, 8, 0, 0)
    fork_title = QLabel("고정축 종합 이득")
    fork_title.setObjectName("battleForkBenefitTitle")
    fork_title.setStyleSheet(themed_style("font-weight:bold;color:#58a6ff"))
    fork_layout.addWidget(fork_title)
    fork_note = QLabel(
        "A=아크 없음, B=아크 상시 패널만 복원, C=완전한 아크."
        "상시=B-A, 스킬/메커니즘=C-B, 종합=C-A; 팀 Buff 표는 여전히 메커니즘 세부 내역이며 이 표와 합산하지 않습니다."
    )
    _style_note(fork_note)
    fork_layout.addWidget(fork_note)
    fork_notice = QLabel("백그라운드 계산 대기 중…")
    _style_note(fork_notice)
    fork_layout.addWidget(fork_notice)
    fork_table = analysis_table(
        (
            "아크 없음 캐릭터 피해",
            "아크 없음 팀 전체 피해",
            "상시·캐릭터",
            "상시·팀 전체",
            "스킬/메커니즘·캐릭터",
            "스킬/메커니즘·팀 전체",
            "종합·캐릭터",
            "종합·팀 전체",
            "정량화 상태",
        ),
        135,
        default_widths=(150, 150, 145, 145, 165, 165, 145, 145, 205),
    )
    fork_table.setFixedHeight(88)
    fork_layout.addWidget(fork_table)
    return core_table, core_notice, fork_panel, fork_table, fork_notice


def render_marginal_benefits(
    core_table: QTableWidget,
    core_notice: QLabel,
    fork_table: QTableWidget,
    fork_notice: QLabel,
    benefits: BattleMarginalBenefits | None,
    *,
    character_id: int | None,
) -> None:
    if benefits is None or benefits.character_id != character_id:
        core_table.setRowCount(0)
        fork_table.setRowCount(0)
        core_notice.setText("선택한 캐릭터의 백그라운드 고정축 계산 대기 중…")
        fork_notice.setText("선택한 캐릭터의 백그라운드 고정축 계산 대기 중…")
        core_notice.show()
        fork_notice.show()
        return
    _render_core(core_table, core_notice, benefits)
    _render_fork(fork_table, fork_notice, benefits.fork)


def _render_core(
    table: QTableWidget,
    notice: QLabel,
    benefits: BattleMarginalBenefits,
) -> None:
    rows = benefits.core_main_stats
    table.setRowCount(len(rows))
    notice.setText(benefits.core_notice)
    notice.setVisible(bool(benefits.core_notice))
    for row_index, row in enumerate(rows):
        values = (
            f"{row.label}{'（当前同类）' if row.is_current else ''}",
            _property_value(row.value, row.is_percent),
            _gain(row.contribution.role_status, row.contribution.role_gain_percent),
            _gain(row.contribution.team_status, row.contribution.team_gain_percent),
            _gain(row.replacement.role_status, row.replacement.role_gain_percent),
            _gain(row.replacement.team_status, row.replacement.team_gain_percent),
            _damage(row.contribution.projected_role_damage),
            _damage(row.contribution.projected_team_damage),
            _status(row.contribution),
        )
        tooltip = _delta_tooltip("메인 속성 없음 대비", row.contribution)
        tooltip += "\n" + _delta_tooltip("현재 메인 속성 교체", row.replacement)
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(tooltip)
            table.setItem(row_index, column, item)


def _render_fork(
    table: QTableWidget,
    notice: QLabel,
    fork: BattleForkMarginal | None,
) -> None:
    if fork is None:
        table.setRowCount(0)
        notice.setText("현재 캐릭터에 아크 분석 결과가 없습니다.")
        notice.show()
        return
    if fork.unavailable_reason:
        table.setRowCount(0)
        notice.setText(fork.unavailable_reason)
        notice.show()
        return
    notice.hide()
    assert fork.permanent is not None
    assert fork.skill is not None
    assert fork.comprehensive is not None
    table.setRowCount(1)
    values = (
        _damage(fork.no_fork_role_damage),
        _damage(fork.no_fork_team_damage),
        _gain(fork.permanent.role_status, fork.permanent.role_gain_percent),
        _gain(fork.permanent.team_status, fork.permanent.team_gain_percent),
        _gain(fork.skill.role_status, fork.skill.role_gain_percent),
        _gain(fork.skill.team_status, fork.skill.team_gain_percent),
        _gain(
            fork.comprehensive.role_status,
            fork.comprehensive.role_gain_percent,
        ),
        _gain(
            fork.comprehensive.team_status,
            fork.comprehensive.team_gain_percent,
        ),
        _status(fork.comprehensive),
    )
    tooltip = "\n".join((
        _delta_tooltip("아크 상시", fork.permanent),
        _delta_tooltip("아크 스킬/메커니즘", fork.skill),
        _delta_tooltip("아크 종합", fork.comprehensive),
        _closure_text(fork),
    ))
    for column, value in enumerate(values):
        item = QTableWidgetItem(value)
        item.setToolTip(tooltip)
        table.setItem(0, column, item)


def _style_note(label: QLabel) -> None:
    label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    label.setWordWrap(True)


def _property_value(value: float, percent: bool) -> str:
    return f"{value * 100:.2f}%" if percent else f"{value:,.2f}"


def _damage(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def _gain(status: QuantificationStatus, value: float | None) -> str:
    if status == "unavailable" or value is None:
        return "—"
    if status == "not_applicable":
        return "+0.00%"
    text = f"{value:+.2f}%"
    return f"{text} (부분)" if status == "partial" else text


def _status(delta: BattleMarginalDelta) -> str:
    return (
        f"캐릭터 {quantification_status_text(delta.role_status)} "
        f"{delta.role_coverage_percent:.1f}% / "
        f"팀 전체 {quantification_status_text(delta.team_status)} "
        f"{delta.team_coverage_percent:.1f}%"
    )


def _delta_tooltip(label: str, delta: BattleMarginalDelta) -> str:
    gaps = "\n".join(f"- {line}" for line in delta.gap_explanations)
    text = f"{label}: {_status(delta)}."
    return text if not gaps else f"{text}\n누락된 의존 항목:\n{gaps}"


def _closure_text(fork: BattleForkMarginal) -> str:
    if fork.closure_role_damage is None or fork.closure_team_damage is None:
        return "A/B/C 폐합: 현재 정량화 상태가 부족해 정확한 폐합 오차를 제시하지 않았습니다."
    return (
        "A/B/C 폐합 오차:"
        f"캐릭터 {fork.closure_role_damage:+,.2f},"
        f"팀 전체 {fork.closure_team_damage:+,.2f}."
    )


__all__ = ["build_marginal_benefit_sections", "render_marginal_benefits"]
