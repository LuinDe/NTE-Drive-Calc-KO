# 以正式升腾技能命中证据投影真红增伤，不猜测未观测的完整状态区间。
"""Conservative per-hit Shinku Rage damage-up projection."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any
from src.services.battle_state_payload import HIT_FIELDS, state_rows, state_row
from src.domain.native_analysis import BattleComputeBackend
from src.services.battle_character_state_compute import compute_character_state

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
)
from src.services.battle_character_awakening_hit_service import (
    SHINKU_RAGE_DAMAGE_IDS,
    SHINKU_RAGE_REQUIREMENT,
)
from src.services.official_role_awakening_service import active_awaken_effects

_CURVE_TABLE = "/Game/DataTable/Skill/GlobalCharacterData/DT_ShinkuEffectFigure"
_BUFF_ASSET = (
    "/Game/Blueprints/Abilities/Player/Ability_076_Shinku/Buff/Buff_Shinku_Rage"
)
_CALC_ASSET = (
    "/Game/Blueprints/Abilities/Calculation/Shinku/Calc_Shinku_RageDmgUp"
)


@dataclass(frozen=True, slots=True)
class BattleShinkuRageConfig:
    damage_up: float
    resonance_damage_up: float
    awakenings: tuple[Mapping[str, Any], ...]


def _curve_value(static_dao: Any, curve_id: str) -> float:
    points = tuple((static_dao.get_combat_curve(_CURVE_TABLE, curve_id) or {}).get(
        "points"
    ) or ())
    if len(points) != 1 or not isinstance(points[0].get("value"), (int, float)):
        raise ValueError(f"신쿠 승천 곡선 {curve_id}에 유일한 수치 근거가 없습니다")
    value = float(points[0]["value"])
    if not isfinite(value) or value < 0.0:
        raise ValueError(f"신쿠 승천 곡선 {curve_id}의 수치가 잘못되었습니다")
    return value


class BattleShinkuRageBuffService:
    @staticmethod
    def load_config(static_dao: Any) -> BattleShinkuRageConfig:
        return BattleShinkuRageConfig(
            damage_up=_curve_value(static_dao, "Shinku_Rage_DmgUp"),
            resonance_damage_up=_curve_value(static_dao, "Shinku_Rage_DmgUpEx_L3"),
            awakenings=tuple(static_dao.list_character_awaken_effects(1076)),
        )

    @staticmethod
    def infer(
        *,
        build: Mapping[str, Any] | None,
        hits: Sequence[BattleAnalysisHit],
        config: BattleShinkuRageConfig | None,
        compute_backend: BattleComputeBackend | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        character = next((
            row for row in (build or {}).get("characters") or ()
            if int(row.get("character_id") or 0) == 1076
        ), None)
        if character is None:
            return ()
        profile = dict(character.get("profile") or {})
        profile.setdefault("awakening_level", int(character.get("awakening_level") or 0))
        resonance = config is not None and any(
            row.get("effect_id") == "resonance_3"
            for row in active_awaken_effects(profile, config.awakenings)
        )
        native = compute_character_state("shinku", {
            "hits": state_rows(hits, HIT_FIELDS), "resonance": resonance,
            "config": state_row(config, ("damage_up", "resonance_damage_up")) if config is not None else None,
            "damage_ids": sorted(SHINKU_RAGE_DAMAGE_IDS),
        }, backend=compute_backend, checkpoint=checkpoint) if (
            isinstance(compute_backend, BattleComputeBackend) and compute_backend.supports_battle_compute
        ) else None
        grouped: dict[int, list[str]] = {}
        if native is None:
            for hit in hits:
                if (
                    hit.character_id == 1076
                    and hit.direction == "outgoing"
                    and hit.gameplay_effect_id.casefold() in SHINKU_RAGE_DAMAGE_IDS
                ):
                    grouped.setdefault(hit.relative_time_us, []).append(hit.event_id)
        else:
            groups = native.get("groups")
            if not isinstance(groups, list):
                raise ValueError("invalid_character_state_result")
            for group in groups:
                if (not isinstance(group, list) or len(group) != 2 or type(group[0]) is not int
                    or not isinstance(group[1], list) or not all(isinstance(item, str) for item in group[1])):
                    raise ValueError("invalid_character_state_result")
                grouped[group[0]] = group[1]
        if config is None:
            value = None
            basis = "신쿠 승천의 정식 곡선이 없어 피해 증가 수치는 알 수 없음으로 유지됩니다."
        else:
            value = (native.get("value") if native is not None else
                     config.damage_up + (config.resonance_damage_up if resonance else 0.0))
            if native is not None and (type(value) not in (int, float) or not isfinite(value)):
                raise ValueError("invalid_character_state_result")
            basis = (
                f"정식 곡선 Shinku_Rage_DmgUp={config.damage_up:g}"
                + (
                    f" + Shinku_Rage_DmgUpEx_L3={config.resonance_damage_up:g}"
                    if resonance else ""
                )
                + " 값을 DamageUpGeneralBase에 가산합니다."
            )
        return tuple(
            BattleInferredBuffInterval(
                interval_id=f"buff:shinku-rage:{at_us}",
                buff_asset_path=_BUFF_ASSET,
                buff_name="승천의 적 (3각성 공명 포함)" if resonance else "승천의 적",
                source_effect_definition_id="character-rage:1076",
                source_kind="confirmed_character_form",
                source_character_id=1076,
                source_character_name=str(character.get("observed_name") or "真红"),
                target_scope="self",
                start_us=at_us,
                end_us=at_us + 1,
                stacks=1,
                duration_policy="ObservedRageHitOnly",
                state_confidence="中",
                value_confidence="未解析" if value is None else "高",
                inference_basis=(
                    "정식 Rage 피해 항목이 이 히트가 승천 스킬임을 확인합니다. 명중 시점만 투영하며, "
                    "관측되지 않은 지속 상태를 채우지 않고, 위압의 응시·독행·일반 스킬로 외삽하지 않습니다. "
                    + basis
                ),
                trigger_event_type="FORMAL_SHINKU_RAGE_HIT",
                evidence_action_ids=(),
                evidence_event_ids=tuple(event_ids),
                modifiers=(BattleBuffModifierEvidence(
                    property_id="DamageUpGeneralBase",
                    modifier_operation="EGameplayModOp::Additive",
                    magnitude_kind="confirmed_static_curve",
                    magnitude_value=value,
                    calculation_asset_path=_CALC_ASSET,
                    value_confidence="未解析" if value is None else "高",
                    application_requirement_asset_path=SHINKU_RAGE_REQUIREMENT,
                ),),
                stacking_type="AggregateByTarget",
                stack_limit_count=1,
            )
            for at_us, event_ids in sorted(grouped.items())
        )
