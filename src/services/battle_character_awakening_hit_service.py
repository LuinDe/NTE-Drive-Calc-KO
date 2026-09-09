# 将已确认的角色觉醒条件收窄到对应正式逐击，不扩散为常驻面板属性。
"""Per-hit gates for character awakening modifiers."""

from __future__ import annotations

from typing import Any, Mapping

from src.domain.battle_report import BattleAnalysisHit


ZERO_FIRST_GAZE_REQUIREMENT = "battle-awakening:zero-first-gaze-extra-hit"
FADIA_GODSLAYER_REQUIREMENT = "battle-awakening:fadia-godslayer"
MITSUKI_ULTRA_REQUIREMENT = "battle-awakening:mitsuki-ultra"
LINKO_COATTACK_REQUIREMENT = "battle-awakening:linko-coattack"
SHINKU_RAGE_REQUIREMENT = "battle-character:shinku-rage-hit"

# 正式 Rage GE 证明本击来自升腾技能；不从普通 Q 名称或邻近命中推断状态。
SHINKU_RAGE_DAMAGE_IDS = frozenset(
    f"ge_player_shinku_{name}_rage_damage"
    for name in (
        "airattack", "branch1", "branch2", "branch3", "branch4", "branch5",
        "melee1_1", "melee1", "melee2", "melee3_1", "melee3", "melee4_1",
        "melee4", "melee5", "perfectevadeattack", "skill1", "skill2",
        "ultraskill2", "ultraskillpre", "ultraskill",
    )
)
_SHINKU_RAGE_SKILL_COEF_IDS = frozenset(
    f"ge_player_shinku_{name}_rage_damage"
    for name in ("skill1", "skill2", "ultraskillpre", "ultraskill", "ultraskill2")
)

_ZERO_FIRST_GAZE_DAMAGE_IDS = frozenset({
    "ge_player_female046_skill_kill_damage_lv1",
    "ge_player_female046_skill_kill_damage_lv2",
    "ge_player_female051_skill_kill_damage_lv1",
    "ge_player_female051_skill_kill_damage_lv2",
})
_ZERO_CHARACTER_IDS = frozenset({1046, 1051})
_KUHARA_ATTACHMENT_DAMAGE_ID = "ge_player_kuhara_seed_damage"
_LINKO_ULTRA_BASE_DAMAGE_ID = "GE_Player_Radio072_UltraSkill3_Damage"
_LINKO_ULTRA_EFFECT_TWO_DAMAGE_ID = (
    "GE_Player_Radio072_UltraSkill3_Damage_level2"
)


def _awakening_enabled(character: Mapping[str, Any], effect_id: str) -> bool:
    profile = character.get("profile")
    profile = profile if isinstance(profile, Mapping) else {}
    if bool(profile.get("awakening_selection_initialized")):
        return effect_id in {
            str(value) for value in profile.get("selected_awaken_effect_ids") or ()
        }
    try:
        required = int(effect_id.removeprefix("Effect"))
        current = int(
            profile.get("awakening_level")
            or character.get("awakening_level")
            or 0
        )
    except (TypeError, ValueError):
        return False
    return current >= required


def character_awakening_damage_multiplier(
    character: Mapping[str, Any],
    *,
    damage_id: str,
    shinku_rage_skill_coefficient: float | None = None,
) -> tuple[float | None, str]:
    """Return explicit per-hit awakening multipliers backed by formal effects."""

    if (
        int(character.get("character_id") or 0) == 1076
        and damage_id.casefold() in _SHINKU_RAGE_SKILL_COEF_IDS
        and _awakening_enabled(character, "Effect6")
    ):
        if shinku_rage_skill_coefficient is None:
            return None, "각성 6 강화 E/Q에 정식 Shinku_RageSkillDmgCoefL6 곡선이 없습니다"
        return 1.0 + shinku_rage_skill_coefficient, (
            "각성 6 「적룡의 보물고」: 정식 강화 E/Q CoefModify "
            f"+{shinku_rage_skill_coefficient:.0%}"
        )
    if (
        int(character.get("character_id") or 0) == 1055
        and damage_id.casefold() == _KUHARA_ATTACHMENT_DAMAGE_ID
        and _awakening_enabled(character, "Effect2")
    ):
        return 2.0, "각성 2 「비수가 되어 돌아온 과거」: 致命玫约 피해 추가 100% 상승"
    return 1.0, ""


def character_awakening_damage_id(
    character: Mapping[str, Any] | None,
    *,
    damage_id: str,
) -> str:
    """Select the formal GE variant changed by an explicit awakening edit."""

    if character is None or int(character.get("character_id") or 0) != 1072:
        return damage_id
    candidates = {
        _LINKO_ULTRA_BASE_DAMAGE_ID.casefold(),
        _LINKO_ULTRA_EFFECT_TWO_DAMAGE_ID.casefold(),
    }
    if damage_id.casefold() not in candidates:
        return damage_id
    return (
        _LINKO_ULTRA_EFFECT_TWO_DAMAGE_ID
        if _awakening_enabled(character, "Effect2")
        else _LINKO_ULTRA_BASE_DAMAGE_ID
    )


def character_awakening_requirement_applies(
    requirement: str,
    hit: BattleAnalysisHit,
) -> tuple[bool, str]:
    """Return whether one confirmed awakening modifier belongs to this hit."""

    normalized = str(requirement or "").casefold()
    if normalized == SHINKU_RAGE_REQUIREMENT:
        applies = (
            hit.character_id == 1076
            and hit.direction == "outgoing"
            and hit.gameplay_effect_id.casefold() in SHINKU_RAGE_DAMAGE_IDS
        )
        return applies, "" if applies else "승천 피해 증가는 이 히트의 정식 Rage 스킬 근거만 사용합니다"
    identity = "|".join((
        hit.attack_type,
        hit.ability_id,
        hit.gameplay_effect_id,
        hit.skill_name,
        hit.damage_name,
    )).casefold()
    if normalized == ZERO_FIRST_GAZE_REQUIREMENT:
        applies = (
            hit.character_id in _ZERO_CHARACTER_IDS
            and hit.gameplay_effect_id.casefold() in _ZERO_FIRST_GAZE_DAMAGE_IDS
        )
        return applies, (
            "" if applies else "初明凝视의 75% 방어 관통은 铭隙鉴刻의 추가 피해에만 적용됩니다"
        )
    if normalized == FADIA_GODSLAYER_REQUIREMENT:
        applies = hit.character_id == 1039 and "fadia_ultraskillmelee" in identity
        return applies, "" if applies else "각성 5 치명 상승은 敌神者에게만 적용됩니다"
    if normalized == MITSUKI_ULTRA_REQUIREMENT:
        applies = hit.character_id == 1070 and "ultraskill" in identity
        return applies, "" if applies else "각성 5 치명 상승은 Q 스킬 피해에만 적용됩니다"
    if normalized == LINKO_COATTACK_REQUIREMENT:
        applies = hit.formula_context_kind.startswith("linko_coattack:")
        return applies, "" if applies else "링코 각성 6 치명 확률은 同频合击에만 적용됩니다"
    if "con_mint_lv6" in normalized:
        if hit.target_hp_before is None or not hit.target_max_hp:
            return False, "히트 전 대상 HP가 없어 대상이 40% 미만인지 확인할 수 없습니다"
        applies = hit.target_hp_before / hit.target_max_hp < 0.40
        return applies, "" if applies else "대상의 히트 전 HP가 40% 이상입니다"
    unresolved = {
        "con_targetnotboss": "정식 보스 분류가 없어 비보스 조건을 추산하지 않습니다",
        "con_skia_level3_1": "牙齿 상태가 없어 각성 3 조건을 추산하지 않습니다",
        "con_skia_level3_2": "牙齿 상태가 없어 각성 3 조건을 추산하지 않습니다",
        "con_mint_lv4": "각성 4 런타임 상태가 없어 조건부 버프를 추산하지 않습니다",
        "con_kuhara_targethaveattachment": "계약 대상 상태가 없어 조건부 버프를 추산하지 않습니다",
        "con_radio072_isawake_critup": "링코 각성 6은 同频合击 공식 식별 쪽에서 대신 처리합니다",
        "con_1072_islevel5": "링코 각성 5의 사이클 호환 조건은 아직 일반 버프로 추론할 수 없습니다",
    }
    for marker, reason in unresolved.items():
        if marker in normalized:
            return False, reason
    return True, ""
