# 按可靠目标实例记录重叠血量区间诊断，但不改写正式逐击。
"""Project HP-interval diagnostics without mutating protocol evidence."""

from __future__ import annotations

from dataclasses import replace

from src.domain.battle_report import BattleAnalysisHit


SINGLE_TARGET_DAMAGE_NORMALIZATION_VERSION = (
    "battle-single-target-damage-normalization-v5"
)


class BattleSingleTargetDamageNormalizationService:
    """Record HP-delta overlap independently for each reliable target instance."""

    @classmethod
    def normalize(
        cls,
        hits: tuple[BattleAnalysisHit, ...],
        *,
        confirmed_single_target: bool,
    ) -> tuple[BattleAnalysisHit, ...]:
        return cls._normalize_hp_interval_overlaps(
            hits,
            confirmed_single_target=confirmed_single_target,
        )

    @classmethod
    def _normalize_hp_interval_overlaps(
        cls,
        hits: tuple[BattleAnalysisHit, ...],
        *,
        confirmed_single_target: bool,
    ) -> tuple[BattleAnalysisHit, ...]:
        frontier_by_target: dict[tuple[str, str], float] = {}
        normalized: list[BattleAnalysisHit] = []
        for hit in sorted(hits, key=lambda row: (row.relative_time_us, row.sequence)):
            if (
                hit.direction != "outgoing"
                or not cls._has_reliable_target_id(hit.target_id)
                or hit.overkill_damage is not None
            ):
                normalized.append(hit)
                continue
            before = hit.target_hp_before
            after = hit.target_hp_after
            if before is None or after is None or before <= after:
                normalized.append(hit)
                continue
            tolerance = max(0.5, abs(hit.damage) * 0.000_001)
            if abs((before - after) - hit.damage) > tolerance:
                normalized.append(hit)
                continue
            target_scope = (hit.scope_half.casefold(), hit.target_id)
            frontier = frontier_by_target.get(target_scope)
            corrected = hit
            if (
                frontier is not None
                and before > frontier + tolerance
                and after < frontier - tolerance
            ):
                overlap = before - frontier
                corrected = replace(
                    hit,
                    raw_damage=hit.raw_damage or hit.damage,
                    damage_correction_kind=(
                        "single_target_hp_interval_overlap_diagnostic"
                    ),
                    damage_correction_confidence="中",
                    damage_overlap_correction=overlap,
                    damage_correction_basis=(
                        (
                            "사용자가 단일 적을 확인했습니다;"
                            if confirmed_single_target
                            else "nte-core가 신뢰할 수 있는 대상 인스턴스를 제공했습니다;"
                        )
                        + "이 행의 HP 구간이 해당 대상이 이전에 커버한 구간과 겹치는 것으로 의심됩니다. 겹침 "
                        f"{overlap:g}. HP 샘플만으로는 정식 히트의 중복을 증명할 수 없으므로,"
                        "진단만 기록하고 피해를 수정하지 않습니다."
                    ),
                )
            normalized.append(corrected)
            if frontier is None or after < frontier:
                frontier_by_target[target_scope] = after
        return tuple(normalized)

    @staticmethod
    def _has_reliable_target_id(target_id: str) -> bool:
        return str(target_id).strip().casefold() not in {"", "unknown"}
