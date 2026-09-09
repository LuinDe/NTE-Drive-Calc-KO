# 按固定逐击轴重放残虹蓄焰的取得与两种 Q 的独立消费资格。
"""Forward-only Zankou awakening state used by direct hit replay."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from src.services.battle_state_payload import HIT_FIELDS, ACTION_FIELDS, state_rows
from src.domain.native_analysis import BattleComputeBackend
from src.services.battle_hit_state_compute import compute_hit_state, restore_q_final_states
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleInferredAction,
)
from src.services.battle_damage_composition_service import (
    explicit_reaction_channel_for_hit,
)


_ZANKOU_ID = 1036
_CHARGE_FINAL_MULTIPLIER = 2.5


@dataclass(frozen=True, slots=True)
class ZankouQFinalDamageEvidence:
    event_id: str
    multiplier: float
    evidence_basis: str


def _selected_effects(character: Mapping[str, Any]) -> frozenset[str]:
    profile = character.get("profile")
    profile = profile if isinstance(profile, Mapping) else {}
    if bool(profile.get("awakening_selection_initialized")):
        return frozenset(
            str(value)
            for value in profile.get("selected_awaken_effect_ids") or ()
        )
    try:
        level = int(
            profile.get("awakening_level")
            or character.get("awakening_level")
            or 0
        )
    except (TypeError, ValueError):
        level = 0
    return frozenset(f"Effect{ordinal}" for ordinal in range(1, level + 1))


def _is_charge_trigger(hit: BattleAnalysisHit) -> bool:
    if hit.direction != "outgoing":
        return False
    if hit.classification == "weave" and hit.character_id == _ZANKOU_ID:
        return True
    return explicit_reaction_channel_for_hit(hit) == (
        "reaction_scorch",
        "浊燃",
    )


def _q_branch(hit: BattleAnalysisHit) -> str:
    if hit.character_id != _ZANKOU_ID or hit.classification != "direct":
        return ""
    identity = "|".join((hit.ability_id, hit.gameplay_effect_id)).casefold()
    if "zankou" not in identity or "ultraskill" not in identity:
        return ""
    if "magicultraskill" in identity:
        return "magic"
    return "force"


def _zankou_q_actions(
    analysis: BattleAnalysisSnapshot,
) -> tuple[BattleInferredAction, ...]:
    return tuple(
        sorted(
            (
                action
                for action in getattr(analysis, "inferred_actions", ())
                if action.character_id == _ZANKOU_ID and action.input_kind == "Q"
            ),
            key=lambda action: (action.start_us, action.action_id),
        )
    )


def reconstruct_zankou_q_final_damage(
    analysis: BattleAnalysisSnapshot,
    character: Mapping[str, Any] | None,
    *, compute_backend: BattleComputeBackend | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, ZankouQFinalDamageEvidence]:
    """Return the independently consumed +150% final multiplier for Q hits."""

    if character is None or int(character.get("character_id") or 0) != _ZANKOU_ID:
        return {}
    selected = _selected_effects(character)
    if "Effect2" not in selected:
        return {}
    if isinstance(compute_backend, BattleComputeBackend) and compute_backend.supports_battle_compute:
        return restore_q_final_states(compute_hit_state("zankou_q", {
            "hits": state_rows(analysis.hits, HIT_FIELDS),
            "actions": state_rows(getattr(analysis, "inferred_actions", ()), ACTION_FIELDS),
            "effect_four_enabled": "Effect4" in selected,
        }, backend=compute_backend, checkpoint=checkpoint))

    hits_by_event = {hit.event_id: hit for hit in analysis.hits}
    triggers = tuple(
        sorted(
            (hit for hit in analysis.hits if _is_charge_trigger(hit)),
            key=lambda hit: (hit.relative_time_us, hit.sequence, hit.event_id),
        )
    )
    actions = _zankou_q_actions(analysis)
    if not actions:
        return {}

    trigger_index = 0
    force_ready = False
    magic_ready = False
    results: dict[str, ZankouQFinalDamageEvidence] = {}
    for action in actions:
        while (
            trigger_index < len(triggers)
            and triggers[trigger_index].relative_time_us < action.start_us
        ):
            force_ready = True
            if "Effect4" in selected:
                magic_ready = True
            trigger_index += 1

        branches: dict[str, list[BattleAnalysisHit]] = {"magic": [], "force": []}
        for event_id in action.evidence_event_ids:
            hit = hits_by_event.get(event_id)
            if hit is not None and (branch := _q_branch(hit)):
                branches[branch].append(hit)

        for branch, branch_hits in branches.items():
            ready = magic_ready if branch == "magic" else force_ready
            if not ready or not branch_hits:
                continue
            branch_name = "血宴入梦时" if branch == "magic" else "焚天烬灭舞"
            basis = (
                "2각성 「심연의 입맞춤」 蓄焰(헥스/스코치 발동 후 획득):"
                f"{branch_name} 이번 발동의 독립 최종 피해 +150%, 즉 ×2.5"
                + (
                    "; 4각성 「악몽의 꽃」으로 血宴 분기가 자격을 독립적으로 보유·소비"
                    if branch == "magic"
                    else ""
                )
            )
            for hit in branch_hits:
                results[hit.event_id] = ZankouQFinalDamageEvidence(
                    event_id=hit.event_id,
                    multiplier=_CHARGE_FINAL_MULTIPLIER,
                    evidence_basis=basis,
                )
            if branch == "magic":
                magic_ready = False
            else:
                force_ready = False

    return results


__all__ = [
    "ZankouQFinalDamageEvidence",
    "reconstruct_zankou_q_final_damage",
]
