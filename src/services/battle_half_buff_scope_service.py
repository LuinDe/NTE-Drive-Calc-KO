# 轨外之境换半会更换队伍；推算 Buff 不能跨半场继承。
"""Scope inferred character Buff intervals to their evidenced abyss half."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from src.domain.battle_report import BattleInferredBuffInterval


_KNOWN_HALVES = ("upper", "lower")


class BattleHalfBuffScopeService:
    """Clip inferred character Buffs at a confirmed upper/lower team boundary."""

    @staticmethod
    def scope(
        intervals: Sequence[BattleInferredBuffInterval],
        *,
        raw_hits: Sequence[Mapping[str, Any]],
        battle_end_us: int,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        half_starts: dict[str, int] = {}
        rosters: dict[str, set[int]] = defaultdict(set)
        for hit in raw_hits:
            half = str(hit.get("abyss_half") or "").strip().casefold()
            if half not in _KNOWN_HALVES:
                continue
            time_us = int(hit.get("relative_time_us") or 0)
            half_starts[half] = min(half_starts.get(half, time_us), time_us)
            character_id = int(hit.get("character_id") or 0)
            character_known = bool(
                hit.get("character_known", character_id > 0)
            )
            if character_known and character_id > 0:
                rosters[half].add(character_id)

        ordered_halves = sorted(half_starts, key=half_starts.__getitem__)
        if len(ordered_halves) < 2:
            return tuple(intervals)
        ranges = {
            half: (
                0 if ordinal == 0 else half_starts[half],
                (
                    half_starts[ordered_halves[ordinal + 1]]
                    if ordinal + 1 < len(ordered_halves)
                    else battle_end_us
                ),
            )
            for ordinal, half in enumerate(ordered_halves)
        }

        scoped: list[BattleInferredBuffInterval] = []
        for interval in intervals:
            if interval.source_character_id <= 0:
                scoped.append(interval)
                continue
            matches = [
                (half, *ranges[half])
                for half in ordered_halves
                if interval.source_character_id in rosters[half]
            ]
            if not matches:
                scoped.append(replace(
                    interval,
                    target_scope="unknown",
                    inference_basis=(
                        f"{interval.inference_basis}; 고정 캐릭터는 있지만 전투 리포트에 해당 캐릭터의"
                        "정식 상/하 하프 소속 근거가 없어, 구간은 유지하되 하프를 넘어 이득을 투영하지 않습니다."
                    ),
                ))
                continue
            intersections = [
                (half, max(interval.start_us, start_us), min(interval.end_us, end_us))
                for half, start_us, end_us in matches
                if max(interval.start_us, start_us) < min(interval.end_us, end_us)
            ]
            for half, start_us, end_us in intersections:
                suffix = f":half:{half}" if len(intersections) > 1 else ""
                scoped.append(replace(
                    interval,
                    interval_id=f"{interval.interval_id}{suffix}",
                    start_us=start_us,
                    end_us=end_us,
                    inference_basis=(
                        f"{interval.inference_basis}; 전투 리포트의 {half} 하프 팀 경계에 따라 적용 구간을 제한합니다."
                    ),
                ))
        return tuple(sorted(scoped, key=lambda row: (
            row.start_us,
            row.end_us,
            row.interval_id,
        )))
