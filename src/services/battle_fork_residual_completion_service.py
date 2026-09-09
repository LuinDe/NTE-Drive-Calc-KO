# 为剩余条件型、生存型和非伤害弧盘建立显式证据边界。
"""Residual fork rules that must not fabricate unavailable runtime state."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.battle_report import BattleBuffModifierEvidence
from src.services.battle_fork_default_policy import (
    DEFAULT_PLAYER_HP_ABOVE_HALF,
    JINGMO_DEFAULT_CRIT_STACKS,
    MAMEN_DEFAULT_STACKS,
)


FORK_RESIDUAL_COMPLETION_MODEL_VERSION = "battle-fork-residual-completion-v2"

_DUSTBIN = "upgradestar_pack_fork_dustbin"
_JINGMO = "upgradestar_pack_fork_jingmotingyuan"
_KOINOBORI = "upgradestar_pack_fork_koinobori"
_ENERGY = "upgradestar_pack_fork_lingganzhongjiezhe"
_MAMEN = "upgradestar_pack_fork_mamen"
_SNOWMAN = "upgradestar_pack_fork_snowman"
_TUANSANLANG = "upgradestar_pack_fork_tuansanlang"
_VINE = "upgradestar_pack_fork_vine"
_WUHUAKUANG = "upgradestar_pack_fork_wuhuakuang"
_YAODAO = "upgradestar_pack_fork_yaodao"
_YUREN = "upgradestar_pack_fork_yuren"
_AUDITED_MARKERS = frozenset({
    _DUSTBIN,
    _JINGMO,
    _KOINOBORI,
    _ENERGY,
    _MAMEN,
    _SNOWMAN,
    _TUANSANLANG,
    _VINE,
    _WUHUAKUANG,
    _YAODAO,
    _YUREN,
})


def _parameter(definition: Mapping[str, Any] | None, name_id: str) -> float | None:
    parameters = (definition or {}).get("parameters") or ()
    if isinstance(parameters, Mapping):
        value = parameters.get(name_id)
        return float(value) if isinstance(value, (int, float)) else None
    if not isinstance(parameters, Sequence) or isinstance(parameters, str):
        return None
    for row in parameters:
        if not isinstance(row, Mapping) or row.get("name_id") != name_id:
            continue
        value = row.get("value")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _modifier(property_id: str, value: float) -> BattleBuffModifierEvidence:
    return BattleBuffModifierEvidence(
        property_id=property_id,
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind="confirmed_fork_parameter",
        magnitude_value=float(value),
        calculation_asset_path="",
        value_confidence="高",
    )


def _rule(
    selected: Any,
    factory: type[Any],
    *,
    suffix: str,
    name: str,
    modifiers: tuple[BattleBuffModifierEvidence, ...],
    scope: str = "unknown",
    stack_count: int = 1,
    stack_limit: int = 1,
) -> Any:
    effect_id = str(selected.effect_definition_id)
    return factory(
        rule_id=f"{effect_id}:confirmed-fork:{suffix}",
        source_effect_definition_id=effect_id,
        source_kind="confirmed_fork_refinement",
        source_character_id=int(selected.character_id),
        source_character_name=str(selected.character_name),
        source_asset_path=f"combat-effect:{effect_id}",
        target_asset_path=f"confirmed-fork:{suffix}",
        target_name=name,
        target_scope=scope,
        event_type="STATIC_EQUIPPED_SOURCE",
        effect_type="ADD",
        duration_policy="Equipped",
        duration_seconds=None,
        stack_count=stack_count,
        modifiers=modifiers,
        stacking_type="AggregateBySource",
        stack_limit_count=stack_limit,
    )


def _values(selected: Any, *names: str) -> tuple[float, ...] | None:
    values = tuple(_parameter(selected.definition, name) for name in names)
    return None if any(value is None for value in values) else tuple(
        float(value) for value in values if value is not None
    )


def _rules_dustbin(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    values = _values(
        selected,
        "buff_dustbin_Unbal",
        "buff_dustbin_CD",
        "buff_dustbin_CD2",
    )
    return () if values is None else (_rule(
        selected,
        factory,
        suffix="dustbin-topple-reduction-trigger",
        name=(
            f"危险游戏: 브레이크 수치 감소 후 브레이크 강도 ({values[1]:g}초 지속,"
            f"재사용 대기시간 {values[2]:g}초; 강인도 감소 이벤트 없음)"
        ),
        modifiers=(_modifier("UnbalIntensityAdd", values[0]),),
    ),)


def _rules_jingmo(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    values = _values(
        selected,
        "buff_tingyuan_CritDamageUp",
        "buff_tingyuan_HPreduce",
        "buff_tingyuan_CD",
        "buff_tingyuan_CD2",
        "GE_Fork_serenity_Skill1_Damage1",
        "GE_Fork_serenity_Skill2_Damage",
    )
    if values is None:
        return ()
    return (
        _rule(
            selected,
            factory,
            suffix="jingmo-default-crit-stacks",
            name="茶花会: 비피격 HP 감소 치명 피해 (사용자 기본값 4중첩)",
            modifiers=(_modifier("CritDamageBase", values[0]),),
            scope="self",
            stack_count=JINGMO_DEFAULT_CRIT_STACKS,
            stack_limit=4,
        ),
        _rule(
            selected,
            factory,
            suffix="jingmo-active-damage",
            name=(
                f"茶花会: 무작위 능동 공격 (재사용 대기시간 {values[3]:g}초;"
                "플레이어 HP 변화 및 능동 이벤트 없음)"
            ),
            modifiers=(
                _modifier("HPCurrentReductionRatio", values[1]),
                _modifier("DerivedDamageCoefficient", values[4]),
                _modifier("DerivedDamageCoefficient", values[5]),
            ),
        ),
    )


def _rules_koinobori(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    values = _values(
        selected,
        "buff_koinobori_AtkUp",
        "buff_koinobori_DefUp",
        "buff_koinobori_HpMaxUp",
    )
    return () if values is None else (_rule(
        selected,
        factory,
        suffix="koinobori-three-elements",
        name="终有时: 팀 내 최소 3종 속성 (고정 팀 속성 목록 없음)",
        modifiers=(
            _modifier("AtkUp", values[0]),
            _modifier("DefUp", values[1]),
            _modifier("HPMaxUp", values[2]),
        ),
    ),)


def _rules_energy(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    values = _values(selected, "buff_xiangdao_energy", "buff_xiangdao_CD")
    return () if values is None else (_rule(
        selected,
        factory,
        suffix="energy-after-e",
        name=(
            f"灵感大逃杀: E 후 종결 에너지 (재사용 대기시간 {values[1]:g}초;"
            "고정 축은 후속 Q를 보충 생성하지 않음)"
        ),
        modifiers=(_modifier("UltraEnergyAdd", values[0]),),
    ),)


def _rules_mamen(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    values = _values(selected, "buff_mamen_fons", "buff_mamen_CosmosUp")
    return () if values is None else (_rule(
        selected,
        factory,
        suffix="mamen-fons-stack",
        name=(
            f"思考喵: 方斯 {values[0]:g}마다 빛속성 피해 1중첩"
            "(전투 리포트에 方斯 사실이 없어 사용자 기본값 10중첩)"
        ),
        modifiers=(_modifier("DamageUpCosmosBase", values[1]),),
        scope="self",
        stack_count=MAMEN_DEFAULT_STACKS,
        stack_limit=10,
    ),)


def _rules_snowman(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    values = _values(selected, "buff_snowman_AtkUp")
    return () if values is None else (_rule(
        selected,
        factory,
        suffix="snowman-shield-state",
        name="愚者种春: 보호막 보유 시 공격력 (전투 리포트에 플레이어 보호막 사실 없음)",
        modifiers=(_modifier("AtkUp", values[0]),),
    ),)


def _rules_tuansanlang(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    values = _values(selected, "buff_tuansanlang_Unbal")
    return () if values is None else (_rule(
        selected,
        factory,
        suffix="tuansanlang-three-same-elements",
        name="该死的邂逅: 같은 속성 캐릭터 3명 이상 (고정 팀 속성 목록 없음)",
        modifiers=(_modifier("UnbalIntensityAdd", values[0]),),
    ),)


def _rules_vine(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    values = _values(selected, "buff_vine_Hp", "buff_vine_CD")
    return () if values is None else (_rule(
        selected,
        factory,
        suffix="vine-kill-heal",
        name=(
            f"笑口常开: 처치 시 회복 (재사용 대기시간 {values[1]:g}초;"
            "전투 리포트에 정식 처치 이벤트 없음)"
        ),
        modifiers=(_modifier("HPCurrentRestoreRatio", values[0]),),
    ),)


def _rules_wuhuakuang(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    values = _values(selected, "buff_kudangao_Atk", "buff_kudangao_Def")
    return () if values is None else (
        _rule(
            selected,
            factory,
            suffix="wuhuakuang-high-hp",
            name="被遗忘者: HP 50% 초과 시 공격력 (사용자 기본값: 높은 HP)",
            modifiers=(_modifier("AtkUp", values[0]),),
            scope="self" if DEFAULT_PLAYER_HP_ABOVE_HALF else "unknown",
        ),
        _rule(
            selected,
            factory,
            suffix="wuhuakuang-low-hp",
            name="被遗忘者: HP 50% 미만 시 방어력 (전투 리포트에 플레이어 HP 사실 없음)",
            modifiers=(_modifier("DefUp", values[1]),),
        ),
    )


def _rules_yaodao(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    values = _values(selected, "GE_Fork_yaodao_Skill_Damage")
    return () if values is None else (_rule(
        selected,
        factory,
        suffix="yaodao-parry-derived-hit",
        name="拔刀: 承轨 반격 추가타 (정식 추가 히트만 소비하며, 동작 기준으로 보충 생성하지 않음)",
        modifiers=(_modifier("DerivedDamageCoefficient", values[0]),),
    ),)


def _rules_yuren(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    values = _values(
        selected,
        "buff_bowenchanggui_HP",
        "buff_bowenchanggui_shield",
    )
    if values is None:
        return ()
    return (
        _rule(
            selected,
            factory,
            suffix="yuren-hp",
            name="勿忘伞: HP",
            scope="self",
            modifiers=(_modifier("HPMaxUp", values[0]),),
        ),
        _rule(
            selected,
            factory,
            suffix="yuren-high-hp-shield",
            name="勿忘伞: HP 50% 초과 시 보호막 강화 (사용자 기본값: 높은 HP)",
            modifiers=(_modifier("ShieldUp", values[1]),),
            scope="self" if DEFAULT_PLAYER_HP_ABOVE_HALF else "unknown",
        ),
    )


_BUILDERS = (
    (_DUSTBIN, _rules_dustbin),
    (_JINGMO, _rules_jingmo),
    (_KOINOBORI, _rules_koinobori),
    (_ENERGY, _rules_energy),
    (_MAMEN, _rules_mamen),
    (_SNOWMAN, _rules_snowman),
    (_TUANSANLANG, _rules_tuansanlang),
    (_VINE, _rules_vine),
    (_WUHUAKUANG, _rules_wuhuakuang),
    (_YAODAO, _rules_yaodao),
    (_YUREN, _rules_yuren),
)


class BattleForkResidualCompletionService:
    """Own every remaining fork and expose unavailable evidence explicitly."""

    @staticmethod
    def owns_effect(effect_definition_id: str) -> bool:
        normalized = str(effect_definition_id or "").casefold()
        return any(marker in normalized for marker in _AUDITED_MARKERS)

    @classmethod
    def rules_for_selected_effect(
        cls,
        selected: Any,
        rule_factory: type[Any],
    ) -> tuple[Any, ...]:
        effect_id = str(selected.effect_definition_id).casefold()
        for marker, builder in _BUILDERS:
            if marker in effect_id:
                return builder(selected, rule_factory)
        return ()
