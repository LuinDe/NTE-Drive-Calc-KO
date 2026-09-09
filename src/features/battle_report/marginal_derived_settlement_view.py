# 在边际页显式列出候选配置新增、但不改写原逐击的派生结算。
"""Audit table for candidate-only fixed-axis settlements."""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QLabel, QTableWidgetItem, QVBoxLayout

from src.app.theme import themed_style
from src.domain.battle_counterfactual import BattleBuildCounterfactual
from src.features.battle_report.analysis_components import analysis_table
from src.services.battle_daffodill_marginal_service import (
    DAFFODILL_EFFECT_FIVE_METHOD,
)


class BattleMarginalDerivedSettlementView(QFrame):
    """Make baseline-zero mechanism gains visible and source-addressable."""

    hit_activated = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._event_ids: list[str] = []
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        heading = QLabel("후보 추가 메커니즘 정산")
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(themed_style("color:#d29922;font-weight:700"))
        layout.addWidget(self.summary)
        note = QLabel(
            "0각성의 통찰은 원래 추가로 한 번 정산되고, 5각성은 통찰 중첩마다 한 번씩 더 추가합니다. 여기서는 0각성 대비 추가된 피해 이벤트만 따로 나열하며,"
            "이 이벤트들은 새 총 피해와 캐릭터 이득에 포함되지만 트리거 조건이 된 원본 히트는 변경하지 않습니다."
            "행을 더블 클릭하면 전체 공식을 볼 수 있습니다."
        )
        note.setWordWrap(True)
        note.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(note)
        self.table = analysis_table(
            (
                "추가 메커니즘",
                "원본 축 트리거 히트",
                "정산 횟수",
                "1회 피해",
                "추가 피해",
                "캐릭터 이득",
                "팀 전체 환산 이득",
                "공식 앵커",
            ),
            150,
            default_widths=(220, 150, 100, 130, 140, 120, 140, 360),
        )
        self.table.cellDoubleClicked.connect(self._activate_row)
        layout.addWidget(self.table)
        self.hide()

    def render(self, comparison: BattleBuildCounterfactual | None) -> None:
        rows = () if comparison is None else tuple(
            row
            for row in comparison.hits
            if row.quantification.method == DAFFODILL_EFFECT_FIVE_METHOD
            and row.candidate_damage is not None
        )
        grouped = defaultdict(list)
        for row in rows:
            grouped[(row.quantification.method, row.source_event_id)].append(row)
        groups = tuple(grouped.values())
        self._event_ids = [group[0].event_id for group in groups]
        self.table.setRowCount(len(groups))
        total_gain = sum(
            row.candidate_damage - row.baseline_damage
            for row in rows
            if row.candidate_damage is not None
        )
        team_gain = (
            total_gain / comparison.baseline_damage * 100.0
            if comparison is not None and comparison.baseline_damage > 0.0
            else 0.0
        )
        self.summary.setText(
            f"5각성의 0각성 대비 추가: 정산 {len(rows)}회, 피해 합계 {total_gain:+,.2f};"
            f"팀 전체 환산 {team_gain:+.2f}%."
        )
        role_baselines = {
            role.character_id: role.baseline_damage
            for role in (() if comparison is None else comparison.roles)
        }
        for row_index, group in enumerate(groups):
            first = group[0]
            gain = sum(
                row.candidate_damage - row.baseline_damage
                for row in group
                if row.candidate_damage is not None
            )
            per_settlement = gain / len(group)
            role_baseline = role_baselines.get(first.character_id, 0.0)
            role_gain = gain / role_baseline * 100.0 if role_baseline > 0.0 else 0.0
            group_team_gain = (
                gain / comparison.baseline_damage * 100.0
                if comparison is not None and comparison.baseline_damage > 0.0
                else 0.0
            )
            formula = (
                f"{len(group)} × {per_settlement:,.2f} = {gain:,.2f};"
                f"{first.quantification.explanation}"
            )
            values = (
                "다포딜 5각성·추가 브레이크",
                first.source_event_id,
                f"총 {1 + len(group)}회 (기본 1 + 추가 {len(group)})",
                f"{per_settlement:,.2f}",
                f"{gain:+,.2f}",
                f"{role_gain:+.2f}%",
                f"{group_team_gain:+.2f}%",
                formula,
            )
            tooltip = (
                f"총 횟수 = 0각성 기본 1회 + 5각성 추가 {len(group)}회;\n"
                f"추가 이득 = 통찰 중첩 수 {len(group)} × 다포딜 개인 브레이크 1회분"
                f" {per_settlement:,.2f} = {gain:,.2f}\n"
                f"{first.quantification.explanation}\n"
                "더블 클릭하면 후보 피해 공식을 볼 수 있습니다. 원본 축 트리거 히트 자체는 독립적으로 유지됩니다."
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(tooltip)
                self.table.setItem(row_index, column, item)
        self.setVisible(bool(groups))

    def _activate_row(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._event_ids):
            self.hit_activated.emit(self._event_ids[row])


__all__ = ["BattleMarginalDerivedSettlementView"]
