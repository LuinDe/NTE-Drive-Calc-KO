# 为人物面板的单位与逐击循环提供沿用请求 token 的取消检查点。
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TypeVar

from src.services.battle_analysis_progress import (
    BattleAnalysisProgressCallback, report_battle_analysis_progress,
)

_Item = TypeVar("_Item")


def marginal_progress_items(
    items: Iterable[_Item], callback: BattleAnalysisProgressCallback | None,
    *, interval: int = 64,
) -> Iterator[_Item]:
    if callback is None:
        yield from items
        return
    for ordinal, item in enumerate(items):
        if ordinal % interval == 0:
            report_battle_analysis_progress(
                callback, phase="marginal_panel_calculation",
                message="캐릭터 속성 한계 이득을 계산하는 중…", completed=ordinal,
            )
        yield item
