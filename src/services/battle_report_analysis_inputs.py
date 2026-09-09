# 在单次页面请求内复用冻结战报事实，候选始终领取独立的可变副本。
"""Request-owned source facts shared by baseline and candidate analysis."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.services.battle_build_profile_normalization_service import (
    normalize_inferred_battle_build,
)
from src.services.battle_formal_damage_tag_service import BattleFormalDamageTagService
from src.services.battle_import_equipment_projection_service import apply_import_equipment_locks
from src.services.battle_report_history_support import StaleBattleReportContextError

if TYPE_CHECKING:
    from src.services.battle_report_history_service import BattleReportHistoryService


def _static_identity(path: Path | None) -> tuple[int, int] | None:
    if path is None:
        return None
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_size, stat.st_mtime_ns


@dataclass(frozen=True, slots=True)
class BattleReportAnalysisInputs:
    """Own prepared facts privately; never lend their mutable dictionaries out."""

    battle_record_id: int
    _owner: object = field(repr=False, compare=False)
    _static_identity: tuple[int, int] | None = field(repr=False)
    _values: tuple[dict[str, Any] | None, ...] = field(repr=False, compare=False)
    _role_details: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def load(
        cls, history: BattleReportHistoryService, battle_record_id: int,
    ) -> BattleReportAnalysisInputs:
        static_path = history._dependencies.static_database_path
        static_identity = _static_identity(static_path)
        with history._open_current_dao() as dao:
            record = dao.load_battle_record(battle_record_id)
            if record is None:
                values = (None,) * 5
            else:
                evidence = dao.load_battle_axis_evidence(battle_record_id)
                build = normalize_inferred_battle_build(
                    dao.load_battle_build_snapshot(battle_record_id)
                )
                build_edit = dao.load_battle_build_edit(battle_record_id)
                locks = dao.load_battle_import_equipment_locks(battle_record_id)
                target = dao.load_battle_target_condition(battle_record_id)
                values = (record, evidence, build, build_edit, target)
        if record is not None:
            target = history._complete_target_condition(target, evidence)
            apply_import_equipment_locks(build, locks)
            history._localize_axis_evidence(evidence)
            BattleFormalDamageTagService.project(evidence, static_path)
            values = (record, evidence, build, build_edit, target)
        result = cls(battle_record_id, history, static_identity, deepcopy(values))
        result._validate(history, battle_record_id)
        return result

    def _validate(
        self, history: BattleReportHistoryService, battle_record_id: int,
    ) -> None:
        if self._owner is not history or self.battle_record_id != battle_record_id:
            raise ValueError("고정 분석 입력이 현재 서비스 또는 전투 리포트에 속하지 않습니다")
        if not history._context_is_current(history._dependencies):
            raise StaleBattleReportContextError("전투 리포트 계정 컨텍스트가 바뀌었습니다")
        if _static_identity(history._dependencies.static_database_path) != self._static_identity:
            raise StaleBattleReportContextError("고정 분석에 사용된 정적 데이터가 바뀌었습니다")

    def copy_for(
        self, history: BattleReportHistoryService, battle_record_id: int,
    ) -> tuple[dict[str, Any] | None, ...]:
        self._validate(history, battle_record_id)
        return deepcopy(self._values)

    def role_detail_cache_for(
        self, history: BattleReportHistoryService, battle_record_id: int,
    ) -> dict[int, dict[str, Any]]:
        """Keep role resources within this frozen request; enrichment copies each value."""
        self._validate(history, battle_record_id)
        return self._role_details
