# 展示单个逐击点的结构化重放结果与完整乘区公式。
"""Modeless formula dialog for a selected immutable battle hit."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_counterfactual import BattleBuildHitCounterfactual
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
)
from src.services.battle_hit_replay_explanation_service import (
    BattleHitReplayExplanationService,
)
from src.services.skill_name_rendering_service import preferred_battle_damage_name


class BattleHitFormulaDialog(QDialog):
    """Keep one non-modal dialog reusable while the user explores nearby hits."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("히트별 피해 공식")
        self.setModal(False)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        self.title_label = QLabel("히트별 피해 공식")
        self.title_label.setStyleSheet(
            themed_style("color:#58a6ff;font-size:16px;font-weight:700")
        )
        root.addWidget(self.title_label)

        self.detail = QPlainTextEdit()
        self.detail.setObjectName("battleHitFormulaDetail")
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self.detail, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.hide)
        root.addWidget(buttons)
        fit_dialog_to_available_screen(self, QSize(960, 760))

    def set_hit(
        self,
        hit: BattleAnalysisHit,
        replay: BattleHitReplayResult | None,
        *,
        active_buffs: Sequence[BattleInferredBuffInterval] = (),
        counterfactual: BattleBuildHitCounterfactual | None = None,
        related_counterfactuals: Sequence[BattleBuildHitCounterfactual] = (),
        related_analysis: BattleAnalysisSnapshot | None = None,
        projection=None, related_hit_details=None,
    ) -> None:
        damage_name = preferred_battle_damage_name(
            hit.damage_name,
            hit.skill_name,
            hit.ability_id,
        )
        self.title_label.setText(f"{hit.character_name} · {damage_name}")
        sections = [BattleHitReplayExplanationService.build(
            hit,
            replay,
            active_buffs=active_buffs,
            counterfactual=counterfactual,
            projection=projection, allow_projection_fallback=False,
        )]
        related_hits = {
            row.event_id: row for row in (() if related_analysis is None else related_analysis.hits)
        }
        related_replays = {
            row.event_id: row
            for row in (() if related_analysis is None else related_analysis.hit_replays)
        }
        quantified = tuple(
            row for row in related_counterfactuals
            if row.event_id in related_hits
            and row.candidate_damage is not None
        )
        if quantified and related_analysis is not None:
            added = sum(
                row.candidate_damage - row.baseline_damage
                for row in quantified
                if row.candidate_damage is not None
            )
            base = (
                hit.damage
                if counterfactual is None
                else counterfactual.candidate_damage
                if counterfactual.candidate_damage is not None
                else counterfactual.known_projection_damage
                if counterfactual.known_projection_damage is not None
                else counterfactual.baseline_damage
            )
            formula_base = (
                base
                if counterfactual is None or counterfactual.candidate_formula_damage is None
                else counterfactual.candidate_formula_damage
            )
            formula_added = sum(
                row.candidate_damage
                if row.candidate_formula_damage is None
                else row.candidate_formula_damage
                for row in quantified
                if row.candidate_damage is not None
            )
            sections.append(
                "【연관 후보 추가 정산】\n"
                "위의 팀 브레이크와 모든 캐릭터 기여는 완전히 유지됩니다. 아래 후보 이벤트는 같은 트리거 시점에 추가로 더해질 뿐,"
                "원본 팀 브레이크를 대체하지 않습니다.\n"
                f"고정축 정산 클러스터 = 위의 조정 후 히트 {base:,.2f} + "
                f"연관 추가 {added:,.2f} = {base + added:,.2f}\n"
                f"후보 공식 감사 합계 = 팀 브레이크 공식 {formula_base:,.2f} + "
                f"5각성 추가 공식 {formula_added:,.2f} = "
                f"{formula_base + formula_added:,.2f}"
            )
            for row in quantified:
                related_hit = related_hits[row.event_id]
                related_projection, related_buffs = ((None, ()) if related_hit_details is None
                    else related_hit_details.for_hit(related_hit, formula=True))
                sections.append(BattleHitReplayExplanationService.build(
                    related_hit,
                    related_replays.get(row.event_id),
                    active_buffs=related_buffs,
                    counterfactual=row,
                    projection=related_projection, allow_projection_fallback=False,
                ))
        self.detail.setPlainText(
            "\n\n".join(sections)
        )
        self.detail.moveCursor(QTextCursor.MoveOperation.Start)

    def show_for_hit(
        self,
        hit: BattleAnalysisHit,
        replay: BattleHitReplayResult | None,
        *,
        active_buffs: Sequence[BattleInferredBuffInterval] = (),
        counterfactual: BattleBuildHitCounterfactual | None = None,
        related_counterfactuals: Sequence[BattleBuildHitCounterfactual] = (),
        related_analysis: BattleAnalysisSnapshot | None = None,
        projection=None, related_hit_details=None,
    ) -> None:
        self.set_hit(
            hit,
            replay,
            active_buffs=active_buffs,
            counterfactual=counterfactual,
            related_counterfactuals=related_counterfactuals,
            related_analysis=related_analysis,
            projection=projection, related_hit_details=related_hit_details,
        )
        fit_dialog_to_available_screen(self, QSize(960, 760))
        self.show()
        self.raise_()
        self.activateWindow()
