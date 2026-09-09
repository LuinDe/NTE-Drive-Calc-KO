# 将一次团队倾陷事件拆成逐角色独立格子并求和，不把触发者当作唯一伤害来源。
"""Team topple replay from per-character immutable formula cells."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleHitBuffProjection,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleHitReplayTerm,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_special_replay_numeric import (
    mitigation_input, numeric_replay,
)


@dataclass(frozen=True, slots=True)
class BattleToppleCharacterConfig:
    """Static inputs not owned by the frozen role panel."""

    character_id: int
    damage_attribute: str
    level_multiplier: float


def _term(
    *,
    term_id: str,
    property_id: str,
    label: str,
    value: float,
    source_group: str,
    source_name: str,
    is_percent: bool,
    basis: str,
) -> BattleHitReplayTerm:
    return BattleHitReplayTerm(
        term_id=term_id,
        property_id=property_id,
        label=label,
        value=float(value),
        source_group=source_group,
        source_name=source_name,
        is_percent=is_percent,
        evidence_basis=basis,
    )


def _baseline_terms(
    baseline: BattleCharacterBaseline,
    property_ids: Sequence[str],
) -> tuple[BattleHitReplayTerm, ...]:
    selected = tuple(property_ids)
    terms = tuple(
        _term(
            term_id=f"{row.source_group}:{row.property_id}",
            property_id=row.property_id,
            label=row.label,
            value=row.value,
            source_group=row.source_group,
            source_name=row.source_name,
            is_percent=row.is_percent,
            basis=f"{baseline.source} 캐릭터 속성 출처 스냅샷",
        )
        for row in baseline.source_stats
        if row.property_id in selected and row.value != 0.0
    )
    if terms:
        return terms
    return tuple(
        _term(
            term_id=f"resolved:{row.property_id}",
            property_id=row.property_id,
            label=row.label,
            value=row.value,
            source_group="resolved",
            source_name="고정 합계",
            is_percent=row.is_percent,
            basis=f"{baseline.source} 합계값; 과거 출처 미분리",
        )
        for row in baseline.stats
        if row.property_id in selected and row.value != 0.0
    )


def _buff_terms(projection, property_ids: Sequence[str]) -> tuple[BattleHitReplayTerm, ...]:
    selected = set(property_ids)
    return tuple(
        _term(
            term_id=f"buff:{row.property_id}:{':'.join(row.interval_ids)}",
            property_id=row.property_id,
            label="、".join(row.buff_names) or row.property_id,
            value=row.additive_value,
            source_group="buff",
            source_name=f"Buff: {'、'.join(row.buff_names) or row.property_id}",
            is_percent=row.property_id in {"UnbalIntensityUp", "UnbalDamageUp"},
            basis=f"히트 시 Buff 투영 (신뢰도 {row.confidence})",
        )
        for row in projection.modifiers
        if row.property_id in selected and row.additive_value != 0.0
    )


def _half_label(scope_half: str) -> str:
    return {"upper": "전반", "lower": "후반"}.get(scope_half, scope_half)


def _baselines_for_hit(
    hit: BattleAnalysisHit,
    analysis: BattleAnalysisSnapshot,
) -> tuple[tuple[BattleCharacterBaseline, ...], tuple[str, ...]]:
    """Resolve the observed same-half party without borrowing the other half."""

    scope_half = hit.scope_half.strip().lower()
    if not scope_half:
        return analysis.baselines, ()

    observed_names: dict[int, str] = {}
    for row in analysis.timeline_hits:
        character_id = row.character_id
        if (
            row.scope_half.strip().lower() != scope_half
            or row.direction != "outgoing"
            or character_id is None
            or character_id <= 0
        ):
            continue
        observed_names.setdefault(character_id, row.character_name)

    half_name = _half_label(scope_half)
    if len(observed_names) != 4:
        return (), (
            f"이 히트는 {half_name}에 속하지만, 정식 히트별 확인에서"
            f"같은 하프 캐릭터가 {len(observed_names)}명(예상 4명)"
            "만 확인되어 팀 브레이크를 완전히 리플레이할 수 없습니다",
        )

    baselines_by_id = {row.character_id: row for row in analysis.baselines}
    missing_baselines = tuple(
        observed_names[character_id]
        for character_id in observed_names
        if character_id not in baselines_by_id
    )
    if missing_baselines:
        return (), (
            f"{half_name} 캐릭터에 고정 패널이 없습니다: {'、'.join(missing_baselines)}",
        )

    return (
        tuple(
            baseline
            for baseline in analysis.baselines
            if baseline.character_id in observed_names
        ),
        (),
    )


class BattleToppleHitReplayService:
    """Replay one observed topple event as a sum of all configured role cells."""

    @staticmethod
    def projection_hits(
        hit: BattleAnalysisHit, analysis: BattleAnalysisSnapshot,
        character_configs: Mapping[int, BattleToppleCharacterConfig], *,
        source_character_id: int | None = None,
    ) -> tuple[BattleAnalysisHit, ...]:
        """Prepare all role cells together before entering per-hit replay."""
        if source_character_id is None:
            baselines, errors = _baselines_for_hit(hit, analysis)
            if errors:
                return ()
        else:
            baselines = tuple(row for row in analysis.baselines
                              if row.character_id == source_character_id)
        return tuple(
            replace(hit, character_id=baseline.character_id,
                    character_name=baseline.character_name,
                    damage_attribute=character_configs[baseline.character_id].damage_attribute)
            for baseline in baselines if baseline.character_id in character_configs
        )

    @classmethod
    @numeric_replay
    def replay(
        cls,
        *,
        hit: BattleAnalysisHit,
        analysis: BattleAnalysisSnapshot,
        character_configs: Mapping[int, BattleToppleCharacterConfig],
        source_character_id: int | None = None,
        formula_type: str = "브레이크 피해 (캐릭터별 합산)",
        projection_for_hit: Callable[[BattleAnalysisHit], BattleHitBuffProjection] | None = None,
    ) -> BattleHitReplayResult:
        condition = analysis.target_condition
        if condition is None:
            return cls._unreplayable(
                hit,
                "사용자가 확인한 단일 대상 방어·저항이 아직 저장되지 않음",
                formula_type=formula_type,
            )
        factors: list[BattleHitReplayFactor] = []
        missing: list[str] = []
        prepared = []
        if source_character_id is None:
            baselines, roster_errors = _baselines_for_hit(hit, analysis)
        else:
            baselines = tuple(
                baseline
                for baseline in analysis.baselines
                if baseline.character_id == source_character_id
            )
            roster_errors = (
                ()
                if baselines
                else (f"캐릭터 {source_character_id}의 고정 패널이 없습니다",)
            )
        if roster_errors:
            return cls._unreplayable(
                hit,
                *roster_errors,
                formula_type=formula_type,
            )
        for baseline in baselines:
            config = character_configs.get(baseline.character_id)
            if config is None:
                missing.append(
                    f"{baseline.character_name}에게 정적 속성 또는 브레이크 레벨 곡선이 없습니다"
                )
                continue
            projection, cell_inputs = cls._character_inputs(
                hit=hit, analysis=analysis, baseline=baseline, config=config,
                projection_for_hit=projection_for_hit,
            )
            prepared.append((baseline, config, projection, cell_inputs))

        if not prepared or missing:
            return cls._unreplayable(
                hit,
                *missing or ("계산할 수 있는 출전 캐릭터 칸이 없습니다",),
                formula_type=formula_type,
            )
        numbers = yield ("special_topple_v1", {
            "observed": hit.damage, "enemy_topple_limit": condition.enemy_topple_limit,
            "feast": condition.environment_kind == "feast",
            "cells": [cell for _, _, _, cell in prepared],
        })
        target_multiplier = numbers["target_multiplier"]
        target_formula = (
            "쟁봉 연회 상위 Boss 실측 구간 2500%"
            if condition.environment_kind == "feast" and condition.enemy_topple_limit >= 70.0
            else "일반 구간 max(1, UnbalMax ÷ 3)"
        )
        factors.extend(
            cls._character_contribution(
                baseline=baseline, config=config, projection=projection,
                cell_inputs=inputs, numbers=cell_numbers, target_multiplier=target_multiplier,
            )
            for (baseline, config, projection, inputs), cell_numbers
            in zip(prepared, numbers["cells"], strict=True)
        )
        predicted = numbers["selected"]
        signed_error, absolute_error = numbers["signed_error"], numbers["absolute_error"]
        confidence = (
            "高" if absolute_error is not None and absolute_error <= 0.5
            else "中" if absolute_error is not None and absolute_error <= 2.0
            else "低"
        )
        factors.insert(0, BattleHitReplayFactor(
            factor_id="topple_target",
            label="적 브레이크 상한 구간",
            value=target_multiplier,
            evidence_basis=(
                f"사용자가 확인한 대상 속성 패키지 UnbalMax={condition.enemy_topple_limit:g}"
                "이며, 쟁봉 연회 상위 Boss 구간은 실제 히트별 검증을 거칩니다"
            ),
            formula=target_formula,
            terms=(_term(
                term_id="target:UnbalMax",
                property_id="UnbalMax",
                label="적 브레이크 상한",
                value=condition.enemy_topple_limit,
                source_group="target",
                source_name="적",
                is_percent=False,
                basis="사용자가 확인한 대상 속성 패키지",
            ),),
        ))
        return BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=predicted,
            critical_damage=None,
            selected_damage=predicted,
            selected_error_percent=absolute_error,
            critical_state="not_applicable",
            confidence=confidence,
            factors=tuple(factors),
            missing_evidence=((
                "다포딜 추가 브레이크 정산은 다포딜 본인의 브레이크 기여만 리플레이"
                "하며, 정적 TRUE 태그는 브레이크의 방어·저항 규칙을 바꾸지 않습니다"
            ),) if source_character_id is not None else (
                "브레이크 이벤트의 캐릭터별 분량은 공식 리플레이로 얻습니다. nte-core는 현재 팀 합계만 보고합니다",
            ),
            formula_type=formula_type,
            critical_rate=0.0,
            expected_damage=predicted,
            corrected_expected_damage=hit.damage if predicted > 0.0 else None,
            signed_error_percent=signed_error,
            critical_policy="disabled",
        )

    @staticmethod
    def _character_inputs(
        *,
        hit: BattleAnalysisHit,
        analysis: BattleAnalysisSnapshot,
        baseline: BattleCharacterBaseline,
        config: BattleToppleCharacterConfig,
        projection_for_hit: Callable[[BattleAnalysisHit], BattleHitBuffProjection] | None = None,
    ) -> tuple:
        condition = analysis.target_condition
        assert condition is not None
        role_hit = replace(
            hit,
            character_id=baseline.character_id,
            character_name=baseline.character_name,
            damage_attribute=config.damage_attribute,
        )
        projection = (
            BattleBuffAttributeProjectionService.project_hit(role_hit, analysis.buff_intervals)
            if projection_for_hit is None else projection_for_hit(role_hit)
        )
        frozen = {row.property_id: row.value for row in baseline.stats}
        values = BattleBuffAttributeProjectionService.apply_additive(
            frozen,
            projection,
        )
        return projection, {
            "strength_base": float(values.get("UnbalIntensityBase", 0.0)),
            "strength_up": float(values.get("UnbalIntensityUp", 0.0)),
            "strength_add": float(values.get("UnbalIntensityAdd", 0.0)),
            "damage_up": float(values.get("UnbalDamageUp", 0.0)),
            "level_multiplier": config.level_multiplier,
            "mitigation": mitigation_input(condition, baseline.character_level, values,
                                           projection, config.damage_attribute,
                                           clamp_defense=False),
        }

    @staticmethod
    def _character_contribution(
        *, baseline, config, projection, cell_inputs, numbers, target_multiplier,
    ) -> BattleHitReplayFactor:
        strength, topple_damage_up = numbers["strength"], cell_inputs["damage_up"]
        attribute = config.damage_attribute
        strength_terms = (
            *_baseline_terms(
                baseline,
                ("UnbalIntensityBase", "UnbalIntensityUp", "UnbalIntensityAdd"),
            ),
            *_buff_terms(
                projection,
                ("UnbalIntensityBase", "UnbalIntensityUp", "UnbalIntensityAdd"),
            ),
        )
        damage_up_terms = (
            *_baseline_terms(baseline, ("UnbalDamageUp", "ToppleDamageUp")),
            *_buff_terms(projection, ("UnbalDamageUp",)),
        )
        terms = (
            _term(
                term_id=f"character:{baseline.character_id}:level_multiplier",
                property_id="ToppleLevelMultiplier",
                label="레벨 기초값",
                value=config.level_multiplier,
                source_group="static",
                source_name="공식 브레이크 레벨 곡선",
                is_percent=False,
                basis=f"캐릭터 레벨 {baseline.character_level:g}",
            ),
            *strength_terms,
            *damage_up_terms,
            _term(
                term_id=f"character:{baseline.character_id}:defense",
                property_id="DefenseMultiplier",
                label="방어 구간",
                value=numbers["defense"],
                source_group="calculated",
                source_name="캐릭터와 적",
                is_percent=False,
                basis="이 캐릭터의 레벨, 방어 관통 및 사용자가 확인한 DefBase/6",
            ),
            _term(
                term_id=f"character:{baseline.character_id}:resistance",
                property_id=f"ResistanceMultiplier:{attribute}",
                label=f"{attribute} 저항 구간",
                value=numbers["resistance"],
                source_group="calculated",
                source_name="캐릭터와 적",
                is_percent=False,
                basis="이 캐릭터의 고유 피해 속성, 속성 관통 및 대상 저항",
            ),
        )
        return BattleHitReplayFactor(
            factor_id=f"topple_character:{baseline.character_id}",
            label=f"{baseline.character_name}倾陷贡献",
            value=numbers["damage"],
            evidence_basis=(
                f"{baseline.source} 패널 + 공식 {config.damage_attribute} 속성 +"
                "히트 시 Buff"
            ),
            formula=(
                f"{config.level_multiplier:g} × "
                f"(1 + {strength:g}/300 + {topple_damage_up:g}) × "
                f"{target_multiplier:g} × {numbers['defense']:.6f} × "
                f"{numbers['resistance']:.6f}"
            ),
            terms=terms,
        )

    @staticmethod
    def _unreplayable(
        hit: BattleAnalysisHit,
        *reasons: str,
        formula_type: str = "브레이크 피해 (캐릭터별 합산)",
    ) -> BattleHitReplayResult:
        return BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=None,
            critical_damage=None,
            selected_damage=None,
            selected_error_percent=None,
            critical_state="unreplayable",
            confidence="未解析",
            factors=(),
            missing_evidence=tuple(reasons),
            formula_type=formula_type,
        )
