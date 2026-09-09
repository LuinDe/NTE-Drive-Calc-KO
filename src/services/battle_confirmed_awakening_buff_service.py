# 提供边界明确且已审计的觉醒 Buff 适配器。
"""Expose bounded, manually audited awakening Buff adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.services.battle_character_awakening_hit_service import (
    FADIA_GODSLAYER_REQUIREMENT,
    LINKO_COATTACK_REQUIREMENT,
    MITSUKI_ULTRA_REQUIREMENT,
    ZERO_FIRST_GAZE_REQUIREMENT,
)


@dataclass(frozen=True, slots=True)
class ConfirmedAwakeningBuffSpec:
    name: str
    scope: str
    event_type: str
    duration_seconds: float | None
    modifier_values: tuple[tuple[Any, ...], ...]
    stacking_type: str = "AggregateByTarget"


_SPECS = {
    "character_awaken:1046:Effect1": ConfirmedAwakeningBuffSpec(
        "初明凝视: 铭隙鉴刻 추가 피해 (각성 1)", "self",
        "STATIC_EQUIPPED_SOURCE", None,
        (("DefIgnore", 0.75, ZERO_FIRST_GAZE_REQUIREMENT),),
    ),
    "character_awaken:1051:Effect1": ConfirmedAwakeningBuffSpec(
        "初明凝视: 铭隙鉴刻 추가 피해 (각성 1)", "self",
        "STATIC_EQUIPPED_SOURCE", None,
        (("DefIgnore", 0.75, ZERO_FIRST_GAZE_REQUIREMENT),),
    ),
    "character_awaken:1036:Effect5": ConfirmedAwakeningBuffSpec(
        "花开见血 (각성 5)", "team", "STATIC_EQUIPPED_SOURCE", None,
        (("ToppleDamageUp", 3.00),),
    ),
    "character_awaken:1036:resonance_6": ConfirmedAwakeningBuffSpec(
        "鸩火灼心 (6각성 공명)", "self",
        "EBuffEventType::BUFF_EVENT_SKILL_AFTER_DAMAGE", 20.0,
        (("AtkUp", 0.40),),
    ),
    "character_awaken:1004:Effect2": ConfirmedAwakeningBuffSpec(
        "闹钟响彻四方 (각성 2)", "self",
        "EBuffEventType::BUFF_EVENT_QTE_BEGIN", 15.0,
        (("DamageUpGeneralBase", 0.15),),
    ),
    "character_awaken:1019:Effect3": ConfirmedAwakeningBuffSpec(
        "개근 보너스 (각성 3)", "self",
        (
            "PASSIVE_ANY_HIT|GE_ActorReaction_1_Damage,"
            "GE_ActorReaction_1_1019_Damage,覆纹,weave"
        ),
        15.0,
        (("AtkUp", 0.15),),
        "AggregateByTarget|RefreshWholeStack",
    ),
    "character_awaken:1019:Effect5": ConfirmedAwakeningBuffSpec(
        "첫 번째 직감 (각성 5)", "self",
        (
            "PASSIVE_HIT|GE_Player_Mint_Skill1_Damage_New,"
            "GE_Player_Mint_Skill1_Damage_Test1"
        ),
        6.0,
        (("CritDamageBase", 0.25),),
    ),
    "character_awaken:1039:Effect3": ConfirmedAwakeningBuffSpec(
        "저주와 축복을 받은 자 (각성 3)", "self", "STATIC_EQUIPPED_SOURCE", None,
        (("HPMaxUp", 0.30),),
    ),
    "character_awaken:1039:Effect5": ConfirmedAwakeningBuffSpec(
        "신을 대적하는 자 치명타 상승 (각성 5)", "self",
        "EBuffEventType::BUFF_EVENT_Q_SKILL_BEGIN", 5.0,
        (("CritBase", 0.50, FADIA_GODSLAYER_REQUIREMENT),),
    ),
    "character_awaken:1039:resonance_6": ConfirmedAwakeningBuffSpec(
        "귀일의 성결한 자 (6각 공명)", "team",
        "STATIC_EQUIPPED_SOURCE", None, (("HPMaxUp", 0.10),),
    ),
    "character_awaken:1070:Effect5": ConfirmedAwakeningBuffSpec(
        "화려한 악장 (각성 5)", "self", "STATIC_EQUIPPED_SOURCE", None,
        (("CritBase", 0.15, MITSUKI_ULTRA_REQUIREMENT),),
    ),
    "character_awaken:1072:Effect6": ConfirmedAwakeningBuffSpec(
        "세계가 그대에게 메아리를 주리라 (각성 6)", "team", "STATIC_EQUIPPED_SOURCE", None,
        (("CritBase", 0.25, LINKO_COATTACK_REQUIREMENT),),
    ),
    "character_awaken:1072:resonance_6": ConfirmedAwakeningBuffSpec(
        "만물이 공명하는 찰나 (6각 공명)", "team",
        "ABILITY_EVENT|Q|GA_Radio072_UltraSkill", 13.0,
        (("DamageUpNatureBase", 0.30), ("DamageUpIncantationBase", 0.30)),
    ),
}

_REPLACES_GENERIC = frozenset({
    "character_awaken:1003:Effect4",
    "character_awaken:1019:Effect3",
    "character_awaken:1036:Effect1",
    "character_awaken:1036:Effect5",
    "character_awaken:1039:resonance_6",
    "character_awaken:1070:Effect5",
    "character_awaken:1075:Effect5",
    "character_awaken:1072:Effect6",
    "character_awaken:1072:resonance_6",
})


class BattleConfirmedAwakeningBuffService:
    @staticmethod
    def get(effect_definition_id: str) -> ConfirmedAwakeningBuffSpec | None:
        return _SPECS.get(effect_definition_id)

    @staticmethod
    def replaces_generic(effect_definition_id: str) -> bool:
        return effect_definition_id in _REPLACES_GENERIC


__all__ = ["BattleConfirmedAwakeningBuffService", "ConfirmedAwakeningBuffSpec"]
