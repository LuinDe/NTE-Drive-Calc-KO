# 将属性 Buff 与机制被动边际结果挂载到同一分析。
"""Attach attribute-Buff and mechanism-passive results to one analysis."""

from __future__ import annotations
from src.services.battle_buff_projection_memo import BattleBuffProjectionMemo

from collections.abc import Mapping, Sequence
from dataclasses import replace

from src.domain.native_analysis import DirectFormulaBackend
from src.domain.battle_report import (
    BattleAnalysisSnapshot,
    BattleHitBuffProjection,
    BattleSkillDamageEvidence,
)
from src.services.battle_buff_counterfactual_service import (
    BUFF_COUNTERFACTUAL_MODEL_VERSION,
    BattleBuffCounterfactualService,
)
from src.services.battle_passive_counterfactual_service import (
    PASSIVE_COUNTERFACTUAL_MODEL_VERSION,
    BattlePassiveCounterfactualService,
)
from src.services.battle_topple_hit_replay_service import (
    BattleToppleCharacterConfig,
)
from src.services.battle_analysis_progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)
from src.services.battle_buff_interval_index import BattleBuffIntervalIndex


class BattleMarginalCounterfactualProjectionService:
    """Own the immutable final projection for marginal result streams."""

    @staticmethod
    def apply(
        analysis: BattleAnalysisSnapshot,
        build: Mapping[str, object] | None,
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        *,
        topple_character_configs: (
            Mapping[int, BattleToppleCharacterConfig] | None
        ) = None,
        progress_callback: BattleAnalysisProgressCallback | None = None,
        direct_formula_backend: DirectFormulaBackend | None = None,
        interval_index: BattleBuffIntervalIndex | None = None,
        original_projection_by_event: (
            Mapping[str, BattleHitBuffProjection] | None
        ) = None,
        projection_memo: BattleBuffProjectionMemo | None = None,
    ) -> BattleAnalysisSnapshot:
        buff_counterfactuals = BattleBuffCounterfactualService.calculate(
            analysis,
            skill_evidence,
            topple_character_configs=topple_character_configs,
            direct_formula_backend=direct_formula_backend,
            progress_callback=progress_callback,
            interval_index=interval_index,
            original_projection_by_event=original_projection_by_event,
            projection_memo=projection_memo,
        )
        report_battle_analysis_progress(
            progress_callback,
            phase="passive_counterfactual",
            message="블라썸 패시브와 메커니즘 이득을 집계하는 중…",
        )
        passive_counterfactuals = BattlePassiveCounterfactualService.calculate(
            analysis,
            build,
        )
        return replace(
            analysis,
            buff_counterfactuals=buff_counterfactuals,
            buff_counterfactual_model_version=BUFF_COUNTERFACTUAL_MODEL_VERSION,
            passive_counterfactuals=passive_counterfactuals,
            passive_counterfactual_model_version=(
                PASSIVE_COUNTERFACTUAL_MODEL_VERSION
            ),
        )


__all__ = ["BattleMarginalCounterfactualProjectionService"]
