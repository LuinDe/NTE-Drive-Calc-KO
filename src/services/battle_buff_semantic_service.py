# 将已确认的自定义 Calculation 绑定到发行静态参数，不把导入占位值当公式结果。
"""Typed semantic adapters for battle Buff calculation assets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BattleBuffCalculationResolution:
    calculation_asset_path: str
    parameter_id: str
    value: float | None
    confidence: str
    reason: str


_CALCULATION_PARAMETER_IDS = {
    "/game/blueprints/abilities/calculation/fork/fork_arachne/"
    "cau_fork_arachne_hp": "buff_Arachne_Hp",
    "/game/blueprints/abilities/calculation/fork/fork_arachne/"
    "cau_fork_arachne_up": "buff_Arachne_Up",
    "/game/blueprints/abilities/calculation/fork/fork_demonblade/"
    "cau_fork_demonblade": "buff_DemonBlade_Crit",
    "/game/blueprints/abilities/calculation/fork/fork_demonblade/"
    "cau_fork_demonblade_cirtdmg": "buff_DemonBlade_CritDamageUp",
    "/game/blueprints/abilities/calculation/fork/fork_rose/"
    "cau_fork_rose_atkup": "buff_Rose_AtkUp",
    "/game/blueprints/abilities/calculation/fork/fork_rose/"
    "cau_fork_rose_critdmgup": "buff_Rose_CritDamageUp",
    "/game/blueprints/abilities/calculation/fork/fork_mofeikesi/"
    "cau_fork_mofeikesi1_1": "buff_mofeikesi_ChargeGetEfficiency",
    "/game/blueprints/abilities/calculation/fork/fork_mofeikesi/"
    "cau_fork_mofeikesi1_2": "buff_mofeikesi_Atk",
    "/game/blueprints/abilities/calculation/fork/fork_mofeikesi/"
    "cau_fork_mofeikesi1_3": "buff_mofeikesi_Up",
    "/game/blueprints/abilities/calculation/fork/fork_time/"
    "cau_fork_time_atkup": "buff_Time_AtkUp",
}

_BUFF_DISPLAY_NAMES = {
    "buff_fork_mofeikesi_5": "「墨菲克斯」",
    "buff_fork_mofeikesi_1_3": "好狗狗走四方Ⅰ",
    "buff_fork_mofeikesi_1_4": "好狗狗走四方Ⅱ",
    "buff_fork_rose_lv1": "「落拓玫瑰」",
    "buff_fork_rose_effect": "「暗棘」",
    "buff_fork_demonblade_lv1": "「妖刀·缚命」",
    "buff_fork_demonblade_critdmgup": "噬心诡刃",
    "buff_fork_arachne_lv1": "「阿拉克涅」",
    "buff_fork_arachne_effect": "永恒圆舞曲",
    "buff_fork_time_lv1": "「时间之外的时间」",
    "buff_fork_time_save": "「荒时」",
    "buff_fork_time_state": "「荒时迷宫」",
    "buff_fork_tigertally_lv1": "「司令虎符」",
    "buff_fork_tigertally_effect": "预备备Ⅰ",
    "buff_fork_tigertally_e": "「左虎符」",
    "buff_fork_tigertally_q": "「右虎符」",
    "buff_fork_butterfly_lv5": "「斑蝶」",
    "buff_fork_butterfly_effect": "现实避难所",
    "buff_fork_blackbook_lv5": "「黑之书」",
    "buff_blackbook_cantuse": "「검은 책」: 사슬 봉쇄",
    "buff_equipment_chaos2_4_effect": "「迪亚波罗斯」",
    "buff_equipment_chaos2_4_effect_power": "「迪亚波罗斯」",
    "buff_equipment_cosmos2_4_effect": "「失落光芒」",
    "buff_equipment_incantation_4_1": "「真红：双生蝶」",
    "buff_equipment_getefficiency2_4_1": "「音速蓝刺猬」",
    "buff_zankou_listenbattle": "잔홍: 蚀心/鸩火 중첩 배율 감시",
    "buff_lacrimosa_meleetotal": "「噩梦」",
    "buff_lacrimosa004_level6": "1각성: 噩梦 중첩 배율",
    "buff_lacrimosa004_level5_cure": "5각성: 噩梦 HP 상한 정산",
    "buff_fadia_nodiesharedamage": "观众目击的祭献: 분담 보호",
    "buff_fadia_shareoutteammatesdamage": "观众目击的祭献: 피해 분담",
    "buff_daffodillunbalup": "다포딜: 브레이크 피해 상승",
    "buff_female051_level3": "奇异记叙",
    "buff_female051_level4": "未决迷数",
    "buff_female051_level5_1": "默示赋命",
    "buff_female051_levelextra1_1": "零示",
}

_FORK_REFINEMENT_DISPLAY_NAMES = {
    "buff_fork_mofeikesi_": "「墨菲克斯」",
    "buff_fork_rose_lv": "「落拓玫瑰」",
    "buff_fork_demonblade_lv": "「妖刀·缚命」",
    "buff_fork_arachne_lv": "「阿拉克涅」",
    "buff_fork_time_lv": "「时间之外的时间」",
    "buff_fork_tigertally_lv": "「司令虎符」",
    "buff_fork_butterfly_lv": "「斑蝶」",
    "buff_fork_blackbook_lv": "「黑之书」",
}

_TEAM_TARGET_BUFFS = frozenset({
    "buff_fork_mofeikesi_1_3",
    "buff_fork_mofeikesi_1_4",
    "buff_female051_levelextra1_1",
})

_CALCULATION_DAMAGE_IDS = {
    "/game/blueprints/abilities/calculation/zankou/"
    "calc_zankoudotstackcoef": frozenset({
        "ge_player_zankou_dotdamage",
        "ge_player_zankou_dotultradamage",
    }),
    "/game/blueprints/abilities/calculation/lacrimosa/"
    "cau_lacrimosablooddam": frozenset({
        "ge_player_lacrimosa_blood_damage",
    }),
    "/game/blueprints/abilities/calculation/lacrimosa/"
    "cau_lacrimosablooddamlv6": frozenset({
        "ge_player_lacrimosa_blood_damage_lv6",
    }),
}

_SPECIALIZED_CALCULATION_REASONS = {
    "/game/blueprints/abilities/calculation/zankou/"
    "calc_zankoudotstackcoef": (
        "이 히트는 蚀心/鸩火 현재 중첩 계수를 사용합니다. 중첩당 계수 1, 상한 10중첩"
    ),
    "/game/blueprints/abilities/calculation/lacrimosa/"
    "cau_lacrimosablooddam": "이 히트는 噩梦 현재 중첩 계수를 사용합니다. 상한 10중첩",
    "/game/blueprints/abilities/calculation/lacrimosa/"
    "cau_lacrimosablooddamlv6": "이 히트는 1각성 噩梦 현재 중첩 계수를 사용합니다. 상한 10중첩",
}


def source_effect_parameter(
    source_definition: Mapping[str, Any] | None,
    name_id: str,
) -> float | None:
    parameters = (source_definition or {}).get("parameters") or ()
    if isinstance(parameters, Mapping):
        value = parameters.get(name_id)
        return float(value) if isinstance(value, (int, float)) else None
    if not isinstance(parameters, Sequence) or isinstance(parameters, str):
        return None
    for row in parameters:
        if not isinstance(row, Mapping) or str(row.get("name_id") or "") != name_id:
            continue
        value = row.get("value")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def resolve_buff_calculation(
    calculation_asset_path: str,
    source_definition: Mapping[str, Any] | None,
) -> BattleBuffCalculationResolution:
    path = str(calculation_asset_path or "").strip()
    parameter_id = _CALCULATION_PARAMETER_IDS.get(path.casefold(), "")
    if not parameter_id:
        return BattleBuffCalculationResolution(
            calculation_asset_path=path,
            parameter_id="",
            value=None,
            confidence="低",
            reason="Calculation에 의미 적응이 아직 등록되지 않아 가져온 자리 표시 값은 공식에 참여하지 않습니다",
        )
    value = source_effect_parameter(source_definition, parameter_id)
    if value is None:
        return BattleBuffCalculationResolution(
            calculation_asset_path=path,
            parameter_id=parameter_id,
            value=None,
            confidence="低",
            reason=f"매개변수 {parameter_id}은(는) 등록되었지만 현재 효과 정의에 대응하는 수치가 없습니다",
        )
    return BattleBuffCalculationResolution(
        calculation_asset_path=path,
        parameter_id=parameter_id,
        value=value,
        confidence="中",
        reason=f"현재 재련 효과 매개변수 {parameter_id}에서 해석",
    )


def render_buff_name(definition_id: str, fallback: str) -> str:
    """Render known runtime IDs while retaining the raw ID in evidence fields."""

    stable_id = str(definition_id or "").strip()
    normalized_id = stable_id.casefold()
    exact_name = _BUFF_DISPLAY_NAMES.get(normalized_id)
    if exact_name is not None:
        return exact_name
    for prefix, display_name in _FORK_REFINEMENT_DISPLAY_NAMES.items():
        if normalized_id.startswith(prefix):
            refinement = normalized_id.removeprefix(prefix)
            if refinement in {"1", "2", "3", "4", "5"}:
                return display_name
    return str(fallback or stable_id)


def confirmed_buff_target_scope(definition_id: str, fallback: str) -> str:
    stable_id = str(definition_id or "").strip().casefold()
    return "team" if stable_id in _TEAM_TARGET_BUFFS else fallback


def calculation_applies_to_damage(
    calculation_asset_path: str,
    damage_id: str,
) -> bool | None:
    """Return an exact static relevance decision for specialized coefficients."""

    allowed = _CALCULATION_DAMAGE_IDS.get(
        str(calculation_asset_path or "").strip().casefold()
    )
    if allowed is None:
        return None
    return str(damage_id or "").strip().casefold() in allowed


def specialized_calculation_reason(calculation_asset_path: str) -> str:
    """Explain calculations consumed by a non-additive replay adapter."""

    return _SPECIALIZED_CALCULATION_REASONS.get(
        str(calculation_asset_path or "").strip().casefold(),
        "",
    )
