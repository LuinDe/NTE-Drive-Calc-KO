# 展示战报派生角色事实的只读视图。
"""Read-only presentation for battle-derived character facts."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import QLabel

from src.domain.battle_report import BattleInferredCharacterFact


class BattleInferredFactLabel(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self.setWordWrap(True)
        self.hide()

    def render_facts(
        self,
        facts: Sequence[BattleInferredCharacterFact],
    ) -> None:
        if not facts:
            self.clear_facts()
            return
        self.setText(
            "추론 사실 (기본적으로 이번 전투 계산에 사용, 각성 선택은 변경하지 않음):"
            + "；".join(
                f"캐릭터 {fact.character_id} · {fact.fact_value} · "
                f"{fact.source_gameplay_effect_id} · 신뢰도 {fact.confidence}"
                for fact in facts
            )
        )
        self.show()

    def clear_facts(self) -> None:
        self.clear()
        self.hide()
