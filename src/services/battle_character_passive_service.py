# 将已审计角色培养被动整理为固定轴重放可消费的规则目录。
"""Character-passive catalog and conservative fixed-axis rule adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.domain.battle_report import BattleAnalysisHit, BattleBuffModifierEvidence


CHARACTER_PASSIVE_MODEL_VERSION = "battle-character-passive-v4"
MITSUKI_GRADUAL_BUFF_IDENTITY = (
    "confirmed:character_passive:1070:mitsuki-gradual-attack"
)


@dataclass(frozen=True, slots=True)
class CharacterPassiveDefinition:
    passive_id: str
    character_id: int
    character_name: str
    ability_id: str
    name: str
    unlock_stage: int
    asset_path: str
    replay_kind: str
    adapter_id: str
    fixed_axis_policy: str


@dataclass(frozen=True, slots=True)
class EnabledCharacterPassive:
    definition: CharacterPassiveDefinition
    source_character_id: int
    source_character_name: str


@dataclass(frozen=True, slots=True)
class CharacterPassiveRuleSpec:
    passive_id: str
    passive_name: str
    source_character_id: int
    source_character_name: str
    source_asset_path: str
    target_scope: str
    event_type: str
    effect_type: str
    duration_policy: str
    duration_seconds: float | None
    modifiers: tuple[BattleBuffModifierEvidence, ...]
    stacking_type: str = "AggregateByTarget"
    stack_limit_count: int = 1
    cooldown_seconds: float | None = None


def _passive(
    character_id: int,
    character_name: str,
    ability_id: str,
    name: str,
    stage: int,
    replay_kind: str,
    adapter_id: str,
    policy: str,
    asset_path: str = "",
) -> CharacterPassiveDefinition:
    return CharacterPassiveDefinition(
        passive_id=f"PASSIVE-{character_id}-{ability_id}",
        character_id=character_id,
        character_name=character_name,
        ability_id=ability_id,
        name=name,
        unlock_stage=stage,
        asset_path=asset_path or f"confirmed:character-passive:{character_id}:{ability_id}",
        replay_kind=replay_kind,
        adapter_id=adapter_id,
        fixed_axis_policy=policy,
    )


_CATALOG = (
    _passive(1003, "早雾", "GA_Sagiri_Passive_1", "可以吃吗？", 2, "target_state", "dot-final-multiplier", "대상 DOT 종류별로 전용 최종 곱연산 구간 재구성"),
    _passive(1003, "早雾", "GA_Sagiri_Passive_2", "鬼把戏", 4, "target_state", "control-defense-down", "띄우기 또는 제압 성공 시 대상 방어 감소 구간 설정"),
    _passive(1004, "安魂曲", "GA_Lacrimosa_Passive_1", "番茄酱盛宴", 2, "derived_hit", "dissonance-toppled-hit", "관측된 디스코드 강화 히트를 유지하고 리플레이"),
    _passive(1004, "安魂曲", "GA_Lacrimosa_Passive_2", "就要自然醒", 4, "action_resource", "lacrimosa-extra-e", "고정 축에서 5번째 A 이후 추가 E 사용 가능 여부 검증"),
    _passive(1008, "翳", "GA_Skia_Passive_1", "现场控制", 2, "target_state", "delay-replace", "이전 레모라 정산 후 대상별로 새 레모라 설정"),
    _passive(1008, "翳", "GA_Skia_Passive_2", "捉拿归案", 4, "timed_modifier", "skia-shadow-damage", "Q 이후 15초 동안 兽牙影刺 통용 피해 증강만 투영"),
    _passive(1010, "娜娜莉", "GA_Nanally_Passive_1", "不止「一腔」的热血", 2, "action_lifecycle", "creation-volley", "블라썸 꽃 히트를 유지하고 10송이 및 1초 간격 등록"),
    _passive(1010, "娜娜莉", "GA_Nanally_Passive_2", "绝对「公正」的决斗", 4, "derived_hit", "nanally-reaction-follow-up", "사이클 이벤트와 2초 재사용 대기시간에 따라 추가 공격 귀속"),
    _passive(1019, "薄荷", "GA_Mint_Passive_1", "变身！超级薄荷！", 2, "spatial_hit", "creation-radius", "단일 대상 이득은 0, 다중 대상은 위치 정보가 없으면 실제 히트 유지"),
    _passive(1019, "薄荷", "GA_Mint_Passive_2", "收工！宾果时间！", 4, "front_state", "mint-front-defense", "필드 체류 구간에 따라 방어력 투영, 경직 저항은 피해에 미포함"),
    _passive(1020, "哈尼娅", "GA_Haniel_Passive_1", "是友情啊", 2, "target_state", "dark-star-attack-drain", "대상 노바 종료 시 팀 전체 고정 공격력 누적"),
    _passive(1020, "哈尼娅", "GA_Haniel_Passive_2", "是羁绊啊", 4, "derived_hit", "haniel-hero-aura", "合奏 중첩에 따라 魔法炮 발사 히트 리플레이"),
    _passive(1021, "埃德嘉", "GA_Edgar_Passive_1", "温和的锋芒", 2, "action_resource", "edgar-charge-reaction", "차지 즉시 에너지 회복과 30초 재사용 대기시간 기록"),
    _passive(1021, "埃德嘉", "GA_Edgar_Passive_2", "不变的暖意", 4, "action_resource", "edgar-truth-key", "E/QTE로 열쇠 획득 및 Q 영역 연장"),
    _passive(1023, "白藏", "GA_Cang_Passive_1", "适度恶趣味", 2, "target_state", "scorch-refresh", "言灵字는 이전 스코치를 종료하고 같은 피해 주기로 재구성"),
    _passive(1023, "白藏", "GA_Cang_Passive_2", "适度上工", 4, "static_modifier", "cang-team-cooperation", "상시 공격력은 직접 투영, 협동 히트는 원래 축에 따라 귀속"),
    _passive(1025, "哈索尔", "GA_Hathor_Passive_1", "延时预警", 2, "target_state", "delay-critical", "대상 레모라 구간에 따라 팀 전체 치명 확률 투영"),
    _passive(1025, "哈索尔", "GA_Hathor_Passive_2", "效率推进", 4, "action_resource", "hathor-delivery-stack", "본인 처치에 따라 闪送 중첩과 분할 E 가능 여부 관리"),
    _passive(1033, "阿德勒", "GA_Adler_Passive_1", "克己", 2, "target_state", "adler-random-debuff", "런타임 랜덤 결과가 없으면 세 효과 각 1/3로 추정"),
    _passive(1033, "阿德勒", "GA_Adler_Passive_2", "正心", 4, "static_modifier", "adler-defense", "상시 방어력은 방어력 배율 스킬에 반영"),
    _passive(1036, "残虹", "GA_Zankou_Passive1", "暮落残阳", 2, "target_state", "zankou-scorch-stack", "대상 DOT 부여에 따라 3중첩 스코치 재구성"),
    _passive(1036, "残虹", "GA_Zankou_Passive2", "殷红幻景", 4, "static_modifier", "zankou-ring-strength", "상시 사이클 강도는 직접 투영, 시작 시 사이클 값은 별도 저장"),
    _passive(1039, "法帝娅", "GA_Fadia_Passive_1", "罪感熔炉", 2, "target_state", "fadia-max-hp-drain", "노바 종료 최대 HP 어댑터 재사용"),
    _passive(1039, "法帝娅", "GA_Fadia_Passive_2", "拒斥与豪掠", 4, "static_modifier", "fadia-team-hp", "팀 전체 최대 HP 백분율 직접 투영"),
    _passive(1046, "「零」", "GA_Female_Passive_1", "鉴定师", 2, "healing", "charge-heal", "양의 종결 에너지 이벤트에 따라 치료 기록, 피해에 섞지 않음"),
    _passive(1046, "「零」", "GA_Female_Passive_2", "异象感知力", 4, "skill_modifier", "protagonist-q-damage", "제로의 极轨终结만 통용 피해 증강 획득"),
    _passive(1052, "浔", "GA_Jin_Passive_1", "鬼兰家纹", 2, "action_lifecycle", "creation-time-stop", "시간 정지 중 블라썸 히트 유지, 상시 패시브는 반사실로 제거하지 않음"),
    _passive(1052, "浔", "GA_Jin_Passive_2", "天下万宝", 4, "skill_multiplier", "jin-q-terminal", "종결 단계 기본 배율 ×2"),
    _passive(1054, "达芙蒂尔", "GA_Daffodill_Passive_1", "破鞘", 2, "target_state", "dissonance-topple-cap", "대상 2중첩 시 그룹 전체 브레이크 상한 갱신"),
    _passive(1054, "达芙蒂尔", "GA_Daffodill_Passive_2", "空蝉", 4, "skill_modifier", "daffodill-entry-damage", "幻影移行만 통용 피해 증강 획득"),
    _passive(1055, "九原", "GA_Kuhara_Passive_1", "顺势而获", 2, "action_lifecycle", "creation-cap", "고정 축에 블라썸 히트 유지 및 2그루·6그루 상한 등록"),
    _passive(1055, "九原", "GA_Kuhara_Passive_2", "风声为我所用", 4, "derived_hit", "kuhara-rose-settlement", "대상 玫约 상태에 따라 15배율 추가 청산 귀속"),
    _passive(1070, "海月", "GA_Mitsuki_Passive1", "泛音", 2, "derived_hit", "dark-star-triple-hit", "대상별 노바 종료 시 우미츠키 히트 3회 귀속"),
    _passive(1070, "海月", "GA_Mitsuki_Passive2", "渐强", 4, "stack_modifier", "mitsuki-jellyfish-stack", "水母弹 히트 후 중첩 추가 및 그룹 전체 5초 갱신"),
    _passive(1071, "卡厄斯", "GA_Chaos_Passive_1", "未迟到的正义", 2, "derived_hit", "delay-end-damage", "대상 레모라 실제 지속 시간에 따라 종료 피해 리플레이"),
    _passive(1071, "卡厄斯", "GA_Chaos_Passive_2", "重点关注！", 4, "target_state", "pursuit-license", "追缉许可의 카오스 본인 통용 피해 증강을 30%로 대체"),
    _passive(1072, "灵可", "GA_Radio072_Passive_1", "弱点感应", 2, "reaction_formula", "lingke-follow-up", "헥스 추가 배율과 한정 통용 피해 증강을 반응 어댑터에 반영"),
    _passive(1072, "灵可", "GA_Radio072_Passive_2", "精确调频", 4, "target_state", "same-frequency-resistance", "대상과 발동 속성별로 12초 저항 감소 설정"),
    _passive(1073, "小吱", "GA_Chiichan073_Passive_1", "飞鸟症候群", 2, "action_resource", "charge-reaction-add", "차지 기본값에 4를 더한 뒤 에너지 충전 효율을 곱함"),
    _passive(1073, "小吱", "GA_Chiichan073_Passive_2", "囤积癖", 4, "front_state", "chiichan-charge-efficiency", "필드 체류 중에만 에너지 충전 효율 투영"),
    _passive(1075, "伊洛伊", "GA_Oneiroi_Passive_1", "镜象", 2, "action_lifecycle", "creation-copy", "3초마다 복제 그루와 복제 꽃 20송이 생성"),
    _passive(1075, "伊洛伊", "GA_Oneiroi_Passive_2", "交感性神经系统", 4, "timed_modifier", "oneiroi-heal-defense-ignore", "치료할 때마다 팀 전체 20초 방어 무시 갱신"),
    _passive(1076, "真红", "GA_Shinku_Passive_1", "独行", 2, "derived_hit", "shinku-charge-reaction", "차지 이벤트와 1초 재사용 대기시간에 따라 범위 히트와 공격력 중첩 귀속"),
    _passive(1076, "真红", "GA_Shinku_Passive_2", "逆鳞", 4, "skill_modifier", "shinku-q-non-boss", "보스가 아닌 대상에 대한 极轨终结만 통용 피해 증강 획득"),
)


_DIRECT_RULES: dict[str, tuple[dict[str, Any], ...]] = {
    "PASSIVE-1008-GA_Skia_Passive_2": ({
        "scope": "self", "event": "ABILITY_EVENT_END|Q", "duration": 15.0,
        "modifiers": (("DamageUpGeneralBase", 0.10, "battle-passive|ge-prefix-any=GE_Player_Skia_ShadowAtk,GE_Player_Skia_SkillShadowAtk"),),
    },),
    "PASSIVE-1019-GA_Mint_Passive_2": (
        {"scope": "self", "event": "BUFF_EVENT_CHANGE_ROLE_IN_BEGIN", "duration_policy": "Infinite", "modifiers": (("DefUp", 0.20, ""),)},
        {"scope": "self", "event": "BUFF_EVENT_CHANGE_ROLE_OUT_BEGIN", "effect": "REMOVE", "duration_policy": "Instant", "modifiers": ()},
    ),
    "PASSIVE-1023-GA_Cang_Passive_2": ({"scope": "self", "event": "PASSIVE_STATIC", "modifiers": (("AtkUp", 0.20, ""),)},),
    "PASSIVE-1033-GA_Adler_Passive_2": ({"scope": "self", "event": "PASSIVE_STATIC", "modifiers": (("DefUp", 0.20, ""),)},),
    "PASSIVE-1036-GA_Zankou_Passive2": ({"scope": "self", "event": "PASSIVE_STATIC", "modifiers": (("MagBase", 100.0, ""),)},),
    "PASSIVE-1039-GA_Fadia_Passive_2": ({"scope": "team", "event": "PASSIVE_STATIC", "modifiers": (("HPMaxUp", 0.10, ""),)},),
    "PASSIVE-1046-GA_Female_Passive_2": ({
        "scope": "self", "event": "PASSIVE_STATIC",
        "modifiers": (("DamageUpGeneralBase", 0.25, "battle-passive|ability-prefix-any=GA_Female046_UltraSkill,GA_Female051_UltraSkill"),),
    },),
    "PASSIVE-1054-GA_Daffodill_Passive_2": ({
        "scope": "self", "event": "PASSIVE_STATIC",
        "modifiers": (("DamageUpGeneralBase", 0.80, "battle-passive|ge-prefix-any=GE_Player_Daffodill_EntryAttack"),),
    },),
    "PASSIVE-1070-GA_Mitsuki_Passive2": ({
        "scope": "self",
        "event": (
            "PASSIVE_HIT|GE_Player_Mitsuki_PerfectAtkBullet,水母弹,jellyfish"
        ),
        "duration": 5.0,
        "modifiers": (("AtkUp", 0.01, ""),),
        "stacking_type": "AggregateBySource+RefreshWholeStack",
        "stack_limit_count": 10,
    },),
    "PASSIVE-1076-GA_Shinku_Passive_1": ({
        "scope": "self",
        "event": "PASSIVE_HIT|GE_Player_Shinku_ReactionAOE_Damage",
        "duration": 30.0,
        "modifiers": (("AtkUp", 0.05, ""),),
        "stacking_type": "AggregateBySource+RefreshWholeStack",
        "stack_limit_count": 10,
        "cooldown_seconds": 1.0,
    },),
    "PASSIVE-1072-GA_Radio072_Passive_1": ({
        "scope": "team",
        "event": "PASSIVE_STATIC",
        "modifiers": ((
            "DamageUpGeneralBase",
            0.10,
            "battle-passive|follow-up-consumer=true;target-weave=true",
        ),),
    },),
}


def _stage(character: Mapping[str, Any]) -> int:
    profile = character.get("profile") or {}
    return int(character.get("breakthrough_stage") or profile.get("breakthrough_stage") or 0)


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


def passive_requirement_applies(
    requirement: str,
    hit: BattleAnalysisHit,
) -> tuple[bool, str]:
    """Evaluate the narrow, explainable hit consumer marker used by passives."""

    if not requirement.casefold().startswith("battle-passive|"):
        return True, ""
    conditions = requirement.split("|", 1)[1].split(";")
    for condition in conditions:
        key, separator, raw = condition.partition("=")
        values = tuple(value.casefold() for value in raw.split(",") if value)
        if not separator or not values:
            return False, "캐릭터 패시브 소비자 조건을 해석할 수 없습니다"
        if key == "ability-prefix-any":
            actual = hit.ability_id.casefold()
            matched = any(actual.startswith(value) for value in values)
            label = "스킬"
        elif key == "ge-prefix-any":
            actual = hit.gameplay_effect_id.casefold()
            matched = any(actual.startswith(value) for value in values)
            label = "피해 항목"
        elif key == "formal-follow-up":
            actual = hit.is_formal_follow_up
            matched = str(actual).casefold() in values
            label = "정식 추가 공격 식별"
        elif key == "follow-up-consumer":
            actual = (
                not hit.is_follow_up
                and hit.classification != "weave"
                and (
                    hit.is_formal_follow_up
                    or hit.formula_context_kind.startswith("linko_coattack:")
                )
            )
            matched = str(actual).casefold() in values
            label = "추가 공격 공식 소비자 식별"
        elif key == "target-weave":
            actual = hit.target_has_weave
            matched = str(actual).casefold() in values
            label = "대상 헥스 상태"
        else:
            return False, f"캐릭터 패시브 소비자 조건 {key}은(는) 아직 지원되지 않습니다"
        if not matched:
            return False, f"이 캐릭터 패시브는 지정한 {label}에만 적용됩니다"
    return True, ""


class BattleCharacterPassiveService:
    """Expose all audited passives and only materialize bounded formula rules."""

    @staticmethod
    def catalog() -> tuple[CharacterPassiveDefinition, ...]:
        return _CATALOG

    @staticmethod
    def is_unlocked(
        build: Mapping[str, Any] | None,
        character_id: int,
        unlock_stage: int,
    ) -> bool:
        logical_id = 1046 if character_id == 1051 else character_id
        for character in (build or {}).get("characters") or ():
            source_id = int(character.get("character_id") or 0)
            source_logical_id = 1046 if source_id == 1051 else source_id
            if source_logical_id == logical_id:
                return _stage(character) >= unlock_stage
        return False

    @classmethod
    def enabled_passives(
        cls,
        build: Mapping[str, Any] | None,
    ) -> tuple[EnabledCharacterPassive, ...]:
        catalog_by_character: dict[int, list[CharacterPassiveDefinition]] = {}
        for definition in cls.catalog():
            catalog_by_character.setdefault(definition.character_id, []).append(definition)
        result = []
        for character in (build or {}).get("characters") or ():
            source_id = int(character.get("character_id") or 0)
            logical_id = 1046 if source_id == 1051 else source_id
            source_name = str(character.get("observed_name") or logical_id)
            for definition in catalog_by_character.get(logical_id, ()):
                if _stage(character) >= definition.unlock_stage:
                    result.append(EnabledCharacterPassive(
                        definition=definition,
                        source_character_id=source_id,
                        source_character_name=source_name,
                    ))
        return tuple(result)

    @classmethod
    def rule_specs(
        cls,
        build: Mapping[str, Any] | None,
    ) -> tuple[CharacterPassiveRuleSpec, ...]:
        result = []
        for enabled in cls.enabled_passives(build):
            definition = enabled.definition
            source_character = next(
                (
                    row for row in (build or {}).get("characters") or ()
                    if int(row.get("character_id") or 0)
                    == enabled.source_character_id
                ),
                {},
            )
            for raw in _DIRECT_RULES.get(definition.passive_id, ()):
                modifiers = tuple(
                    BattleBuffModifierEvidence(
                        property_id=property_id,
                        modifier_operation="EGameplayModOp::Additive",
                        magnitude_kind="confirmed_character_passive",
                        magnitude_value=float(value),
                        calculation_asset_path="",
                        value_confidence="高",
                        application_requirement_asset_path=requirement,
                    )
                    for property_id, value, requirement in raw.get("modifiers", ())
                )
                result.append(CharacterPassiveRuleSpec(
                    passive_id=definition.passive_id,
                    passive_name=definition.name,
                    source_character_id=enabled.source_character_id,
                    source_character_name=enabled.source_character_name,
                    source_asset_path=definition.asset_path,
                    target_scope=str(raw["scope"]),
                    event_type=str(raw["event"]),
                    effect_type=str(raw.get("effect") or "ADD"),
                    duration_policy=str(raw.get("duration_policy") or ("HasDuration" if raw.get("duration") else "Infinite")),
                    duration_seconds=(None if raw.get("duration") is None else float(raw["duration"])),
                    modifiers=modifiers,
                    stacking_type=str(raw.get("stacking_type") or "AggregateByTarget"),
                    stack_limit_count=(
                        20
                        if definition.passive_id
                        == "PASSIVE-1070-GA_Mitsuki_Passive2"
                        and _awakening_enabled(source_character, "Effect6")
                        else max(1, int(raw.get("stack_limit_count") or 1))
                    ),
                    cooldown_seconds=(
                        None
                        if raw.get("cooldown_seconds") is None
                        else float(raw["cooldown_seconds"])
                    ),
                ))
        return tuple(result)

    @classmethod
    def load_rules(cls, build: Mapping[str, Any] | None, rule_type: Any) -> tuple[Any, ...]:
        return tuple(
            rule_type(
                rule_id=f"{row.passive_id}:direct:{ordinal}",
                source_effect_definition_id=(
                    f"character_passive:{row.source_character_id}:"
                    f"{row.passive_id.split('-', 2)[-1]}"
                ),
                source_kind="confirmed_character_passive",
                source_character_id=row.source_character_id,
                source_character_name=row.source_character_name,
                source_asset_path=row.source_asset_path,
                target_asset_path=(
                    MITSUKI_GRADUAL_BUFF_IDENTITY
                    if row.passive_id == "PASSIVE-1070-GA_Mitsuki_Passive2"
                    else f"confirmed:{row.passive_id}"
                ),
                target_name=row.passive_name,
                target_scope=row.target_scope,
                event_type=row.event_type,
                effect_type=row.effect_type,
                duration_policy=row.duration_policy,
                duration_seconds=row.duration_seconds,
                stack_count=1,
                modifiers=row.modifiers,
                stacking_type=row.stacking_type,
                stack_limit_count=row.stack_limit_count,
                cooldown_seconds=row.cooldown_seconds,
            )
            for ordinal, row in enumerate(cls.rule_specs(build))
        )

    @staticmethod
    def skill_multiplier_adjustment(
        character: Mapping[str, Any],
        *,
        damage_id: str,
        ability_id: str,
    ) -> tuple[float, str]:
        character_id = int(character.get("character_id") or 0)
        if (
            character_id == 1052
            and _stage(character) >= 4
            and ability_id == "GA_Jin_UltraSkill"
            and damage_id == "GE_Player_Jin_UltraSkill3_Damage"
        ):
            return 2.0, "돌파 패시브 「세상의 모든 진귀한 것」: 极轨终结의 종결 단계 기본 배율 ×2"
        return 1.0, ""
