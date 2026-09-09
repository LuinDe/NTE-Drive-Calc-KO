# 定义战斗机制图鉴的 Qt-free 公开投影契约与稳定分类。
"""Immutable contracts and taxonomy for the combat-mechanics catalog."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote

from src.domain.static_catalog import CatalogLink


@dataclass(frozen=True, slots=True)
class MechanicsFamily:
    key: str
    title: str
    subtitle: str
    glyph: str
    accent: str


@dataclass(frozen=True, slots=True)
class MechanicsCard:
    record_id: str
    family_key: str
    card_kind: str
    eyebrow: str
    title: str
    subtitle: str
    badges: tuple[str, ...]
    status: str | None = None
    owner_label: str = "공통 메커니즘"


@dataclass(frozen=True, slots=True)
class PlayerField:
    label: str
    value: str
    tone: str = "neutral"


@dataclass(frozen=True, slots=True)
class PlayerSection:
    title: str
    fields: tuple[PlayerField, ...]


@dataclass(frozen=True, slots=True)
class EvidenceStage:
    key: str
    label: str
    status: str
    summary: str


@dataclass(frozen=True, slots=True)
class MechanicsDetail:
    record_id: str
    card_kind: str
    title: str
    subtitle: str
    family_key: str
    badges: tuple[str, ...]
    status: str | None
    owner_label: str
    owner_link: CatalogLink | None
    redirect_only: bool
    sections: tuple[PlayerSection, ...]
    identity_fields: tuple[PlayerField, ...]
    evidence_stages: tuple[EvidenceStage, ...]
    related_links: tuple[tuple[str, CatalogLink], ...]
    audit_references: tuple[str, ...]
    notice: str = ""


FAMILIES = (
    MechanicsFamily("damage", "직접 피해 체인", "패널, 스킬 배율 및 직접 피해 총공식", "✦", "#58a6ff"),
    MechanicsFamily("multipliers", "통용 곱연산 구간", "피해 증가, 취약, 치명타, 방어 및 저항", "◈", "#bc8cff"),
    MechanicsFamily("states", "DOT 및 사이클", "악몽, 식심, 짐화 및 전체 사이클", "◌", "#ff7b72"),
    MechanicsFamily("settlement", "브레이크 및 정산", "팀 브레이크, 특수 정산 및 HP 변화", "◇", "#39d0d8"),
)
FAMILY_BY_KEY = {family.key: family for family in FAMILIES}

FORMULA_FAMILY_BY_KEY = {
    "panel_attribute": "damage",
    "skill_multiplier": "damage",
    "direct_damage": "damage",
    "damage_increase": "multipliers",
    "vulnerability": "multipliers",
    "critical": "multipliers",
    "defense": "multipliers",
    "resistance": "multipliers",
    "dot_damage": "states",
    "topple_damage": "states",
    "weave_followup": "states",
    "independent_final_damage": "settlement",
    "settlement_rounding": "settlement",
    "max_hp_settlement": "settlement",
}

FORMULA_CHAPTER_BY_KEY = {
    "panel_attribute": "패널",
    "skill_multiplier": "技能倍率",
    "direct_damage": "直伤",
    "damage_increase": "피해 증가",
    "vulnerability": "취약",
    "critical": "暴击",
    "defense": "防御",
    "resistance": "저항",
    "independent_final_damage": "독립 피해 증가",
    "dot_damage": "持续伤害",
    "topple_damage": "倾陷",
    "weave_followup": "覆纹",
    "settlement_rounding": "최종 내림",
    "max_hp_settlement": "HP 정산",
}
FORMULA_CHAPTER_ORDER = {
    chapter: index
    for index, chapter in enumerate((
        "패널", "技能倍率", "直伤", "피해 증가", "취약", "防御", "저항",
        "暴击", "持续伤害", "倾陷", "覆纹", "독립 피해 증가", "최종 내림",
        "HP 정산",
    ))
}
FORMULA_CHAPTER_ORDER.update({
    "持续直伤": 8,
    "环合基础": 9,
    "环合": 10,
    "倾陷": 11,
    "共享伤害": 12,
    "生命结算": 13,
})
# 反事实模型仍供仓库审计使用，但不属于玩家图鉴的分类或卡墙。
STATUS_ORDER = {"complete": 0, "partial": 1, "unavailable": 2, "not_applicable": 3}
MODEL_FAMILY_BY_KEY = {
    "buff_ge_attributes": "attributes",
    "formal_dot_classification": "dot",
    "dot_state_replay": "dot",
    "topple_base_formula": "topple",
    "topple_special_states": "topple",
    "max_hp_settlement": "topple",
    "attachments": "events",
    "summon_lifecycle": "events",
    "healing_damage_coupling": "events",
    "healing_without_damage_consumer": "events",
    "shield_state": "events",
    "fixed_axis_replay": "formula",
    "native_counterfactual_core": "formula",
    "unknown_preservation": "formula",
}
PLACEHOLDER_NAME = "名称暂未提供"


def encode_record(kind: str, key: str) -> str:
    return f"{kind}|{quote(str(key), safe='')}"


def decode_record(record_id: str) -> tuple[str, str]:
    kind, separator, encoded = str(record_id).partition("|")
    if not separator or not kind or not encoded:
        raise ValueError("전투 메커니즘 레코드 키 형식이 잘못되었습니다")
    return kind, unquote(encoded)
