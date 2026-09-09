# 独立判断单个冻结命中与 Buff 区间的适用修正和证据理由。
from __future__ import annotations
from dataclasses import dataclass
from src.domain.battle_report import BattleAnalysisHit, BattleInferredBuffInterval
from src.services.battle_buff_semantic_service import calculation_applies_to_damage, specialized_calculation_reason
from src.services.battle_character_awakening_hit_service import character_awakening_requirement_applies
from src.services.battle_character_passive_service import passive_requirement_applies
from src.services.battle_damage_composition_service import classify_battle_hit_channel
from src.services.battle_outer_realm_buff_service import outer_realm_requirement_applies

BUFF_ATTRIBUTE_PROJECTION_VERSION = "battle-buff-attribute-v22"
_INFERRED_HIT_TARGET_PREFIX = "battle-hit-target|id="

_CONTINUOUS_DAMAGE_CHANNELS = frozenset(
    {
        "dot",
        "special_nightmare",
        "special_zankou_erosion",
        "special_zankou_venom",
        "reaction_scorch",
    }
)

_PROPERTY_ALIASES = {
    "Crit": "CritBase",
    "CritAdd": "CritBase",
    "CritDamageAdd": "CritDamageBase",
    "DamageUpGeneralAdd": "DamageUpGeneralBase",
    "DamageUpChaosAdd": "DamageUpChaosBase",
    "DamageUpCosmosAdd": "DamageUpCosmosBase",
    "DamageUpIncantationAdd": "DamageUpIncantationBase",
    "DamageUpLakshanaAdd": "DamageUpLakshanaBase",
    "DamageUpNatureAdd": "DamageUpNatureBase",
    "DamageUpPsycheAdd": "DamageUpPsycheBase",
    "DamageUpPsychicallyAdd": "DamageUpPsychicallyBase",
    "ToppleDamageUp": "UnbalDamageUp",
}
_SAFE_ADDITIVE_PROPERTIES = frozenset(
    {
        "AtkUp",
        "AtkAdd",
        "HPMaxUp",
        "HPMaxAdd",
        "DefUp",
        "DefAdd",
        "CritBase",
        "CritDamageBase",
        "DamageUpGeneralBase",
        "DamageUpChaosBase",
        "DamageUpCosmosBase",
        "DamageUpIncantationBase",
        "DamageUpLakshanaBase",
        "DamageUpNatureBase",
        "DamageUpPsycheBase",
        "DamageUpPsychicallyBase",
        "DefIgnore",
        "DamagePenetrateChaos",
        "DamagePenetrateCosmos",
        "DamagePenetrateIncantation",
        "DamagePenetrateLakshana",
        "DamagePenetrateNature",
        "DamagePenetratePsyche",
        "DamagePenetratePsychically",
        "DamageResistChaosBase",
        "DamageResistChaosAdd",
        "DamageResistCosmosBase",
        "DamageResistCosmosAdd",
        "DamageResistIncantationBase",
        "DamageResistIncantationAdd",
        "DamageResistLakshanaBase",
        "DamageResistLakshanaAdd",
        "DamageResistNatureBase",
        "DamageResistNatureAdd",
        "DamageResistPsycheBase",
        "DamageResistPsycheAdd",
        "DamageResistPsychicallyBase",
        "DamageResistPsychicallyAdd",
        "MagBase",
        "UnbalIntensityBase",
        "UnbalIntensityUp",
        "UnbalIntensityAdd",
        "UnbalDamageUp",
    }
)
_TOPPLE_ONLY_PROPERTIES = frozenset(
    {
        "UnbalIntensityBase",
        "UnbalIntensityUp",
        "UnbalIntensityAdd",
        "UnbalDamageUp",
    }
)
_TOPPLE_CHANNELS = frozenset(
    {
        "other_topple",
        "special_daffodill_extra_topple",
    }
)
_TOPPLE_FORMULA_PROPERTIES = frozenset(
    {
        *_TOPPLE_ONLY_PROPERTIES,
        "DefIgnore",
        *(
            property_id
            for property_id in _SAFE_ADDITIVE_PROPERTIES
            if property_id.startswith("DamagePenetrate") or property_id.startswith("DamageResist")
        ),
    }
)
_ELEMENT_NAMES = {
    "Chaos": "chaos",
    "Cosmos": "cosmos",
    "Incantation": "incantation",
    "Lakshana": "lakshana",
    "Nature": "nature",
    "Psyche": "psyche",
    "Psychically": "psychically",
}
_ELEMENT_PROPERTY_ATTRIBUTE = {
    property_id: damage_type
    for element_name, damage_type in _ELEMENT_NAMES.items()
    for property_id in (
        f"DamageUp{element_name}Base",
        f"DamagePenetrate{element_name}",
        f"DamageResist{element_name}Base",
        f"DamageResist{element_name}Add",
    )
}
_TARGET_PROPERTIES = frozenset(
    property_id for property_id in _SAFE_ADDITIVE_PROPERTIES if property_id.startswith("DamageResist")
)
_CONFIDENCE_ORDER = {"未解析": 0, "低": 1, "中": 2, "高": 3}
_NON_DAMAGE_PROPERTIES = frozenset(
    {
        "ChargeGetEfficiencyBase",
        "DerivedDamageCoefficient",
        "HealUp",
        "HPCurrentReductionRatio",
        "HPCurrentRestoreRatio",
        "ImmuneDeadByTeammates",
        "ShareOutTeammatesDamageMul",
        "ShieldUp",
        "ToppleDurationAdd",
        "UltraEnergyAdd",
        "MoveSpeedMaxMult",
    }
)
_UNRESOLVED_REASON_MARKERS = (
    "적용 대상이 아직 확정되지 않았습니다",
    "逐击角色未知",
    "대상 인스턴스 없음",
    "정식 보스 분류 없음",
    "牙齿 상태 없음",
    "각성 4 런타임 상태 없음",
    "계약 대상 상태 없음",
    "히트 전 대상 HP 없음",
    "정식 태그 상태 없음",
    "정식 대상 태그 상태 없음",
    "버프 적용 대상과 일치하지 않음",
    "아직 안전한 피해 곱연산 구간에 매핑되지 않음",
    "지원되는 가산 보정이 아님",
    "수치 또는 Calculation이 아직 해석되지 않음",
    "계산할 수 있는 속성 보정 없음",
)


def normalize_battle_buff_property_id(property_id: str) -> str:
    """Return the formula-facing property used by per-hit Buff projection."""

    return _PROPERTY_ALIASES.get(property_id, property_id)


def _minimum_confidence(*values: str) -> str:
    normalized = tuple(value if value in _CONFIDENCE_ORDER else "低" for value in values)
    return min(normalized, key=_CONFIDENCE_ORDER.__getitem__) if normalized else "未解析"


def _confirmed_source_tags_apply(
    interval: BattleInferredBuffInterval,
    modifier: object,
    hit: BattleAnalysisHit,
) -> tuple[bool, str]:
    target_tags = tuple(getattr(modifier, "target_require_tags", ()) or ())
    if target_tags:
        return False, "정식 대상 태그 상태가 없어 조건부 버프를 추산하지 않음"
    tags = tuple(getattr(modifier, "source_require_tags", ()) or ())
    if not tags:
        return True, ""
    attack_type = hit.attack_type.casefold()
    identity = "|".join(
        (
            hit.attack_type,
            hit.gameplay_effect_id,
            hit.ability_id,
            hit.skill_name,
            hit.damage_name,
        )
    ).casefold()
    channel_id = classify_battle_hit_channel(hit)[0]
    is_melee = (
        attack_type in {"普攻", "일반 공격", "normal", "normalattack", "melee", "a"}
        or "_melee" in hit.ability_id.casefold()
    ) and "ultraskill" not in identity
    is_ultra = attack_type in {"q技能", "ultra"} or "ultraskill" in identity
    requirements = {
        "state.damage": hit.direction == "outgoing",
        "state.damage.skill": (
            attack_type in {"skill", "e技能"} or ("_skill" in identity and "ultraskill" not in identity)
        ),
        "state.damage.ultraskill": is_ultra,
        "state.damage.qte": (attack_type == "qte" or "qte" in identity),
        "state.damage.attachment": ("attachment" in identity or "ge_player_kuhara_budboom_damage" in identity),
        "state.damage.melee": is_melee,
        "state.damage.normalattack": is_melee,
        "state.damage.dot": channel_id in _CONTINUOUS_DAMAGE_CHANNELS,
        "state.damage.unbalance": channel_id in _TOPPLE_CHANNELS,
        "state.damage.perfectevadedamage": ("perfectevade" in identity or "闪避反击" in hit.attack_type),
        "state.cure": False,
        "ability.ultraskill": is_ultra,
        "state.damage.extremecounter": (
            "extrem" in identity or "极限反击" in hit.attack_type or "闪避反击" in hit.attack_type
        ),
        "state.damage.normalorcounter": (
            is_melee or "extrem" in identity or "极限反击" in hit.attack_type or "闪避反击" in hit.attack_type
        ),
    }
    recognized = tuple(requirements[tag.casefold()] for tag in tags if tag.casefold() in requirements)
    ability_requirements = tuple(
        tag.rsplit(".", 1)[-1].casefold() in identity
        for tag in tags
        if tag.casefold().startswith("ability.") and tag.casefold() != "ability.ultraskill"
    )
    supported = set(requirements)
    unsupported = tuple(
        tag for tag in tags if tag.casefold() not in supported and not tag.casefold().startswith("ability.")
    )
    if unsupported:
        return False, "정식 태그 상태가 없어 조건부 버프를 추산하지 않음"
    if (recognized and not all(recognized)) or (ability_requirements and not all(ability_requirements)):
        return False, "이 버프 보정은 지정한 스킬 피해 태그에만 적용됨"
    return True, ""


def _inferred_hit_target_applies(
    requirement: str,
    hit: BattleAnalysisHit,
) -> tuple[bool, str]:
    if not requirement.startswith(_INFERRED_HIT_TARGET_PREFIX):
        return True, ""
    target_id = requirement.removeprefix(_INFERRED_HIT_TARGET_PREFIX)
    if hit.target_id != target_id:
        return False, "추론된 조건부 피해 증가는 배율 단차가 나타난 같은 대상에만 투영됨"
    return True, ""


@dataclass(frozen=True, slots=True)
class IntervalProjectionEvaluation:
    candidates: tuple[tuple[str, float, str], ...]
    accepted_properties: tuple[str, ...]
    reasons: tuple[str, ...]


def evaluate_interval_projection(
    hit: BattleAnalysisHit,
    interval: BattleInferredBuffInterval,
    channel_id: str,
) -> IntervalProjectionEvaluation:
    candidates: list[tuple[str, float, str]] = []
    accepted_properties: set[str] = set()
    accepted = False
    interval_reasons: list[str] = []
    source_character_scope = interval.target_scope.startswith("character:")
    if (
        interval.target_scope
        not in {
            "self",
            "team",
            "team_others",
            "target",
        }
        and not source_character_scope
    ):
        interval_reasons.append("적용 대상이 아직 확정되지 않았습니다")
    elif interval.target_scope == "team_others" and (hit.character_id is None or int(hit.character_id) <= 0):
        interval_reasons.append("逐击角色未知，无法确认该击是否属于来源角色之外的队友")
    elif interval.target_scope == "target" and not interval.target_id:
        interval_reasons.append("대상 인스턴스 없음, 적 버프/디버프는 대상 간 추산하지 않음")
    elif interval.target_scope == "target" and interval.target_id != hit.target_id:
        interval_reasons.append("이 히트의 대상 인스턴스와 일치하지 않음")
    else:
        for modifier in interval.modifiers:
            source_applies, source_reason = _confirmed_source_tags_apply(
                interval,
                modifier,
                hit,
            )
            if not source_applies:
                interval_reasons.append(source_reason)
                continue
            requirement = modifier.application_requirement_asset_path.casefold()
            target_applies, target_reason = _inferred_hit_target_applies(
                modifier.application_requirement_asset_path,
                hit,
            )
            if not target_applies:
                interval_reasons.append(target_reason)
                continue
            passive_applies, passive_reason = passive_requirement_applies(
                modifier.application_requirement_asset_path,
                hit,
            )
            if not passive_applies:
                interval_reasons.append(passive_reason)
                continue
            awakening_applies, awakening_reason = character_awakening_requirement_applies(
                modifier.application_requirement_asset_path,
                hit,
            )
            if not awakening_applies:
                interval_reasons.append(awakening_reason)
                continue
            outer_applies, outer_reason = outer_realm_requirement_applies(
                modifier.application_requirement_asset_path,
                hit,
            )
            if not outer_applies:
                interval_reasons.append(outer_reason)
                continue
            if requirement == "battle-channel:continuous-damage" and channel_id not in _CONTINUOUS_DAMAGE_CHANNELS:
                interval_reasons.append("이 버프는 噩梦·蚀心·鸩火·스코치 등 지속 피해에만 적용됨")
                continue
            applies_to_hit = calculation_applies_to_damage(
                modifier.calculation_asset_path,
                hit.gameplay_effect_id,
            )
            if applies_to_hit is False:
                interval_reasons.append("이 전용 배율은 바인딩된 피해 항목에만 적용되어 이 히트에는 사용하지 않음")
                continue
            specialized_reason = specialized_calculation_reason(modifier.calculation_asset_path)
            if applies_to_hit is True and specialized_reason:
                interval_reasons.append(f"{specialized_reason}, 히트별 리플레이 어댑터가 별도로 계산함")
                continue
            property_id = normalize_battle_buff_property_id(modifier.property_id)
            if (
                property_id == "CoefModify"
                and interval.source_effect_definition_id == "character_awaken:1036:resonance_3"
                and hit.character_id == 1036
                and hit.classification == "direct"
                and "zankou"
                in "|".join(
                    (
                        hit.ability_id,
                        hit.gameplay_effect_id,
                    )
                ).casefold()
                and "ultraskill"
                in "|".join(
                    (
                        hit.ability_id,
                        hit.gameplay_effect_id,
                    )
                ).casefold()
            ):
                interval_reasons.append("3각성 Q의 CoefModify는 정식 스킬 배율 근거가 이미 소비했으므로 확인 대기 속성으로 다시 투영하지 않습니다")
                continue
            if property_id in _TOPPLE_ONLY_PROPERTIES and channel_id not in _TOPPLE_CHANNELS:
                interval_reasons.append(f"{property_id}은(는) 브레이크 피해의 캐릭터별 칸에만 포함됨")
                continue
            if channel_id in _TOPPLE_CHANNELS and property_id not in _TOPPLE_FORMULA_PROPERTIES:
                interval_reasons.append(f"{property_id}은(는) 브레이크의 캐릭터별 공식에 포함되지 않음")
                continue
            if channel_id == "reaction_nova" and property_id not in {
                "MagBase",
                "DamagePenetratePsychically",
                "DamageResistPsychicallyBase",
                "DamageResistPsychicallyAdd",
            }:
                interval_reasons.append(f"{property_id}은(는) 노바의 레벨·사이클 강도·저항 공식에 포함되지 않음")
                continue
            operation = modifier.modifier_operation.casefold()
            if property_id not in _SAFE_ADDITIVE_PROPERTIES:
                interval_reasons.append(
                    f"{property_id}은(는) 이 히트의 피해 공식에 속하지 않음"
                    if property_id in _NON_DAMAGE_PROPERTIES
                    else f"{property_id}은(는) 아직 안전한 피해 곱연산 구간에 매핑되지 않음"
                )
                continue
            target_property = property_id in _TARGET_PROPERTIES
            if target_property != (interval.target_scope == "target"):
                interval_reasons.append(f"{property_id}은(는) 버프 적용 대상과 일치하지 않음")
                continue
            expected_attribute = _ELEMENT_PROPERTY_ATTRIBUTE.get(property_id)
            if expected_attribute is not None and hit.damage_attribute.casefold() != expected_attribute:
                interval_reasons.append(f"{property_id}은(는) 해당 히트의 피해 속성과 일치하지 않음")
                continue
            if not operation.endswith("additive"):
                interval_reasons.append(f"{property_id}은(는) 지원되는 가산 보정이 아님")
                continue
            if modifier.magnitude_value is None or modifier.value_confidence not in {"中", "高"}:
                interval_reasons.append(f"{property_id}의 수치 또는 Calculation이 아직 해석되지 않음")
                continue
            accepted = True
            accepted_properties.add(property_id)
            confidence = _minimum_confidence(
                interval.state_confidence,
                modifier.value_confidence,
            )
            candidates.append((property_id, float(modifier.magnitude_value), confidence))
    reasons = tuple(dict.fromkeys(interval_reasons or (() if accepted else ("계산할 수 있는 속성 보정 없음",))))
    return IntervalProjectionEvaluation(
        tuple(candidates),
        tuple(sorted(accepted_properties)),
        reasons,
    )
