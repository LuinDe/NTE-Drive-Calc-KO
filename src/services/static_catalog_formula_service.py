# 构建“游戏资料库”伤害公式与反事实支持状态的只读领域投影。
"""Auditable read-only formula catalog and counterfactual support matrix."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.storage.sqlite.static_catalog_formula_queries import (
    StaticCatalogFormulaQueries,
    StaticFormulaEvidenceSnapshot,
)

EvidenceKind = Literal[
    "project_contract",
    "implementation",
    "public_behavior_test",
    "official_static",
    "repository_audit",
]
DataBoundary = Literal[
    "project_rule",
    "official_static_input",
    "runtime_derived",
    "observed_runtime",
]
SupportStatus = Literal["complete", "partial", "unavailable", "not_applicable"]


@dataclass(frozen=True, slots=True)
class CatalogEvidenceReference:
    kind: EvidenceKind
    path: str
    symbol: str
    note: str


@dataclass(frozen=True, slots=True)
class FormulaVariable:
    symbol: str
    meaning: str


@dataclass(frozen=True, slots=True)
class FormulaEntry:
    key: str
    section: str
    title: str
    expression: str
    boundary: DataBoundary
    variables: tuple[FormulaVariable, ...]
    applicable_when: tuple[str, ...]
    limitations: tuple[str, ...]
    evidence: tuple[CatalogEvidenceReference, ...]


@dataclass(frozen=True, slots=True)
class CounterfactualSupportEntry:
    key: str
    category: str
    mechanism: str
    scope: str
    status: SupportStatus
    modeling_scheme: str
    evidence: tuple[CatalogEvidenceReference, ...]
    consumer_entries: tuple[str, ...]
    gap_codes: tuple[str, ...]
    covered_dataset: str
    covered_entities: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StaticCatalogFormulaDomain:
    projection_version: str
    readonly: bool
    evidence_snapshot: StaticFormulaEvidenceSnapshot
    formulas: tuple[FormulaEntry, ...]
    counterfactual_support: tuple[CounterfactualSupportEntry, ...]


def _ref(
    kind: EvidenceKind,
    path: str,
    symbol: str,
    note: str,
) -> CatalogEvidenceReference:
    return CatalogEvidenceReference(kind=kind, path=path, symbol=symbol, note=note)


def _formula_entries(
    snapshot: StaticFormulaEvidenceSnapshot,
) -> tuple[FormulaEntry, ...]:
    contract = "docs/reference/damage-calculation.md"
    calculation = "src/services/damage_calculation_service.py"
    replay = "src/services/battle_hit_replay_service.py"
    static_note = (
        f"dataset={snapshot.dataset_id}; normalized skill_damage rows="
        f"{snapshot.skill_damage_rows}; these are inputs, not the project formula"
    )
    return (
        FormulaEntry(
            key="panel_attribute",
            section="기본값과 캐릭터 패널",
            title="캐릭터 패널 속성",
            expression="Panel = Base × (1 + Up) + Add",
            boundary="project_rule",
            variables=(
                FormulaVariable("Base", "캐릭터 본체·아크 등 기본값 합계"),
                FormulaVariable("Up", "같은 속성 백분율 보너스 합계"),
                FormulaVariable("Add", "같은 속성 고정값 보너스 합계"),
            ),
            applicable_when=("공격력·HP·방어력 패널 재구성",),
            limitations=(
                "출처가 Base, Up, Add 중 어디에 들어가는지는 각 출처의 정식 속성 정의로 결정됩니다.",
                "문서 공식은 프로젝트 규칙이며 SQLite 필드가 아닙니다.",
            ),
            evidence=(
                _ref("project_contract", contract, "기본 속성 구간", "Base/Up/Add 분류 정의"),
                _ref(
                    "implementation",
                    calculation,
                    "calculate_attribute_value",
                    "순수 함수로 패널 합계 구현",
                ),
                _ref(
                    "public_behavior_test",
                    "tests/test_damage_calculation_service.py",
                    "test_direct_damage_returns_every_confirmed_multiplier",
                    "공격력·HP·방어력 세 패널 값 검증",
                ),
            ),
        ),
        FormulaEntry(
            key="skill_multiplier",
            section="기본값과 캐릭터 패널",
            title="스킬 배율과 CoefModify",
            expression="SkillCoef = SourceTierCoef × (1 + Σ CoefModify)",
            boundary="project_rule",
            variables=(
                FormulaVariable("SourceTierCoef", "유효 스킬 레벨에 대응하는 정식 배율 단계"),
                FormulaVariable("CoefModify", "해당 스킬 태그에 정식으로 적용되는 배율 계수 보정"),
            ),
            applicable_when=("히트별로 정식 skill_damage와 스킬 레벨 근거가 바인딩됨",),
            limitations=(
                "유일한 스킬/피해 항목 바인딩이 없으면 unknown을 유지하며, 기본으로 공격력 배율을 적용하지 않습니다.",
                "정적 배율 배열은 입력일 뿐이며, 레벨 매핑과 보정 공식은 프로젝트 규칙에 속합니다.",
            ),
            evidence=(
                _ref("project_contract", contract, "스킬 배율 계수 보정", "곱셈 보정 정의"),
                _ref(
                    "implementation",
                    "src/services/battle_skill_damage_evidence_service.py",
                    "BattleSkillDamageEvidenceService.load",
                    "정식 배율·속성·치명 전략 근거 조립",
                ),
                _ref("official_static", "data/game_static.sqlite3", "skill_damage", static_note),
                _ref(
                    "official_static",
                    "data/game_static.sqlite3",
                    "skill_damage_modifier",
                    f"normalized modifier rows={snapshot.skill_damage_modifier_rows}",
                ),
            ),
        ),
        FormulaEntry(
            key="direct_damage",
            section="직접 피해 곱연산 구간",
            title="직접 피해 총공식",
            expression=(
                "Direct = SkillCoef × ScalingPanel × DamageUp × Crit × Defense "
                "× Resistance × Vulnerability × Π Independent"
            ),
            boundary="project_rule",
            variables=(
                FormulaVariable("ScalingPanel", "피해 항목이 지정한 공격력/HP/방어력 패널"),
                FormulaVariable("Independent", "명시적으로 독립된 최종 출처별 배수"),
            ),
            applicable_when=("일반 직접 피해이면서 필수 스킬·패널·대상 공식 입력을 해석할 수 있음",),
            limitations=("DOT·브레이크·헥스·HP 상한 정산은 각자의 출구를 사용합니다.",),
            evidence=(
                _ref("project_contract", contract, "직접 피해 총공식", "프로젝트 골드 스탠더드 곱연산 구간 순서"),
                _ref(
                    "implementation",
                    calculation,
                    "DamageCalculationService.calculate_direct",
                    "확인된 모든 곱연산 구간 반환",
                ),
                _ref(
                    "implementation",
                    replay,
                    "BattleHitReplayService._replay_direct",
                    "고정 축 히트별 리플레이 소비자",
                ),
            ),
        ),
        FormulaEntry(
            key="damage_increase",
            section="직접 피해 곱연산 구간",
            title="피해 증가 구간",
            expression="DamageUp = 1 + Σ damage_increase",
            boundary="project_rule",
            variables=(FormulaVariable("damage_increase", "통용/속성/스킬/상태 피해 증가"),),
            applicable_when=("출처 태그가 현재 히트의 적용 범위와 일치함",),
            limitations=("적 취약과 FinalDamageUp은 이 구간에 포함되지 않습니다.",),
            evidence=(
                _ref("project_contract", contract, "피해 증가 구간", "같은 구간 내 가산 정의"),
                _ref("implementation", calculation, "calculate_additive_multiplier", "1+합계"),
            ),
        ),
        FormulaEntry(
            key="vulnerability",
            section="직접 피해 곱연산 구간",
            title="취약 구간",
            expression="Vulnerability = 1 + Σ target_damage_taken_up",
            boundary="project_rule",
            variables=(
                FormulaVariable("target_damage_taken_up", "대상에게 걸린 받는 피해 증가 디버프"),
            ),
            applicable_when=("대상 신원/적용 범위와 디버프 구간을 바인딩할 수 있음",),
            limitations=("플레이어 측 피해 증가와 DOT 전용 FinalDamageUp은 이 구간에 포함되지 않습니다.",),
            evidence=(
                _ref("project_contract", contract, "취약 구간", "적 독립 가산 구간 정의"),
                _ref("implementation", calculation, "calculate_additive_multiplier", "공유 가산 곱연산 구간 함수"),
            ),
        ),
        FormulaEntry(
            key="critical",
            section="직접 피해 곱연산 구간",
            title="치명 분기와 기댓값",
            expression=(
                "NonCrit = floor(FullPrecision); Crit = floor(FullPrecision × "
                "(1 + CritDamage)); Expected = (1-r)×NonCrit + r×Crit"
            ),
            boundary="project_rule",
            variables=(
                FormulaVariable("r", "캐릭터·고정·비활성 또는 unknown 치명 전략이 제공하는 치명 확률"),
                FormulaVariable("CritDamage", "치명 피해 보너스"),
            ),
            applicable_when=("한 번의 실제 정산은 다른 모든 곱연산 구간이 완료된 뒤 치명 분기를 선택함",),
            limitations=("unknown 치명 전략은 기댓값을 생성하거나 비치명타로 가장해서는 안 됩니다.",),
            evidence=(
                _ref("project_contract", contract, "치명타 구간", "실제 분기와 통계 기댓값 구분"),
                _ref("implementation", replay, "BattleHitReplayService._replay_direct", "두 후보를 각각 내림"),
                _ref(
                    "public_behavior_test",
                    "tests/test_battle_counterfactual_marginal_integration.py",
                    "test_unknown_crit_policy_remains_unquantified",
                    "unknown 치명은 정량화하지 않음",
                ),
            ),
        ),
        FormulaEntry(
            key="defense",
            section="대상 피해 감소",
            title="방어 구간",
            expression=(
                "EnemyDef = [DefBase×(1+DefUp)+DefAdd]/6 × (1-Pen) × "
                "(1-Reduction); Defense = (Level+100)/(EnemyDef+Level+100)"
            ),
            boundary="project_rule",
            variables=(
                FormulaVariable("Pen", "공격자 방어 관통/무시"),
                FormulaVariable("Reduction", "대상 방어력 감소"),
            ),
            applicable_when=("고정된 적 속성 팩을 우선 사용하고, 없을 때만 명시적 장면 근사를 사용",),
            limitations=("대상 프로필을 알 수 없고 후보 방어력이 동등하지 않으면 정량화할 수 없습니다.",),
            evidence=(
                _ref("project_contract", contract, "방어 구간", "속성 팩 우선과 장면 폴백"),
                _ref("implementation", calculation, "calculate_enemy_defense_from_profile", "DefBase/6"),
                _ref("implementation", calculation, "calculate_defense_multiplier", "레벨 방어 곱연산 구간"),
            ),
        ),
        FormulaEntry(
            key="resistance",
            section="대상 피해 감소",
            title="저항 구간",
            expression="X = BaseRes-Reduction-Pen; X≥0: 1-X; X<0: 1-X/1.10",
            boundary="project_rule",
            variables=(
                FormulaVariable("BaseRes", "고정된 대상 프로필에서 해당 피해 속성의 전투 전 최종 저항"),
                FormulaVariable("Reduction/Pen", "동적 저항 감소와 공격자 속성 관통"),
            ),
            applicable_when=("히트별 피해 속성과 대상 프로필을 해석할 수 있음",),
            limitations=("몬스터 약점 목록은 추가 피해 증가가 아니며, 중국어 이름으로 저항을 추측해서는 안 됩니다.",),
            evidence=(
                _ref("project_contract", contract, "저항 구간", "양/음 저항 구간 분할"),
                _ref("implementation", calculation, "calculate_resistance_multiplier", "구간 분할 순수 함수"),
            ),
        ),
        FormulaEntry(
            key="independent_final_damage",
            section="특수 최종 곱연산 구간",
            title="통용 독립 FinalDamageUp",
            expression="Independent = Π(1 + each explicit FinalDamageUp)",
            boundary="runtime_derived",
            variables=(
                FormulaVariable("FinalDamageUp", "정식 효과이면서 적용 태그/조건이 해석된 독립 최종 피해 증가"),
            ),
            applicable_when=("정식 속성과 히트별 Source/Target 조건이 모두 일치함",),
            limitations=(
                "속성 이름이 비슷하다고 조건이 충족된 것은 아닙니다.",
                "DOT 양단 태그로 한정된 FinalDamageUp은 반드시 DOT 전용 슬롯을 거쳐야 합니다.",
            ),
            evidence=(
                _ref("project_contract", contract, "독립 곱연산 구간", "독립 항목을 항목별로 곱함"),
                _ref("implementation", replay, "BattleHitReplayService._replay_direct", "최종 피해 속성 식별"),
                _ref(
                    "official_static",
                    "data/game_static.sqlite3",
                    "buff_modifier.property_id=FinalDamageUp",
                    f"formal modifier rows={snapshot.final_damage_up_modifier_rows}",
                ),
            ),
        ),
        FormulaEntry(
            key="dot_damage",
            section="特殊伤害",
            title="DOT 단일 틱과 DOT 전용 최종 곱연산 구간",
            expression=(
                "DotTick = DirectLikeFormula(fixed crit policy) × StateStacks × "
                "[1 + min(PreHitDotKinds×25%, 100%)]"
            ),
            boundary="runtime_derived",
            variables=(
                FormulaVariable("StateStacks", "같은 대상/하프의 정산 전 DOT 중첩 또는 계수"),
                FormulaVariable("PreHitDotKinds", "정산 전에 여전히 유효한 감사 완료 DOT 종류 수"),
            ),
            applicable_when=("정식 State.Damage.Dot 태그가 피해 분류를 가짐",),
            limitations=(
                "현재 상태 리플레이는 噩梦·蚀心·鸩火·스코치 및 문서에 나열된 짧은 창 근거만 다룹니다.",
                "DOT 태그가 있으면 DOT이며, 수동 피해 채널 화이트리스트로 대체할 수 없습니다.",
            ),
            evidence=(
                _ref("project_contract", contract, "DOT 전용 최종 곱연산 구간", "정산 전 종류 집계"),
                _ref(
                    "implementation",
                    "src/services/battle_dot_stack_state_service.py",
                    "reconstruct_dot_stack_states",
                    "하프/대상별로 히트마다 네 가지 상태 리플레이",
                ),
                _ref(
                    "official_static",
                    "data/game_static.sqlite3",
                    "combat_blueprint_tag:State.Damage.Dot",
                    f"formal tag assets={snapshot.formal_dot_tag_assets}",
                ),
                _ref(
                    "official_static",
                    "data/game_static.sqlite3",
                    "buff_modifier:DOT-scoped FinalDamageUp",
                    f"formal tag-scoped rows={snapshot.dot_scoped_final_damage_up_modifier_rows}",
                ),
            ),
        ),
        FormulaEntry(
            key="topple_damage",
            section="特殊伤害",
            title="倾陷伤害",
            expression=(
                "Topple = LevelCurve × (1+Strength/300+ΣToppleUp) × "
                "max(1, UnbalMax/3) × Defense × Resistance"
            ),
            boundary="project_rule",
            variables=(
                FormulaVariable("LevelCurve", "캐릭터 레벨에 대응하는 정식 브레이크 곡선"),
                FormulaVariable("UnbalMax", "대상 브레이크 상한 또는 고정 단계 오버라이드"),
            ),
            applicable_when=("브레이크 정산 행에 같은 하프의 완전한 캐릭터 및 대상 프로필 근거가 있음",),
            limitations=("디스코드 15% 상한 감소는 프로젝트 기본 모델이며, 공식 검증된 기본 비율로 가장하지 않습니다.",),
            evidence=(
                _ref("project_contract", contract, "倾陷伤害", "5개 곱연산 구간 규칙"),
                _ref("implementation", calculation, "DamageCalculationService.calculate_topple", "5개 곱연산 구간 순수 함수"),
                _ref(
                    "public_behavior_test",
                    "tests/test_damage_calculation_service.py",
                    "test_topple_damage_uses_only_its_five_confirmed_multipliers",
                    "5개 곱연산 구간 범위 고정",
                ),
            ),
        ),
        FormulaEntry(
            key="weave_followup",
            section="特殊伤害",
            title="헥스 추가 피해",
            expression="Weave = ActualDirect × [1.20×(1+0.20×S/(S+180))-1] × Π Special",
            boundary="project_rule",
            variables=(FormulaVariable(
                "S",
                "헥스에 기록된 원본 피해의 실제 출처 캐릭터의 사이클 강도",
            ),),
            applicable_when=("정식 헥스 추가 피해는 기록된 원본 피해의 속성을 계승함",),
            limitations=("예상 직접 피해로 액션 축을 재생성하지 않고, 고정 축의 발동 히트만 소비합니다.",),
            evidence=(
                _ref("project_contract", contract, "사이클 기본 규칙", "헥스 강도 곱연산 구간"),
                _ref("implementation", calculation, "calculate_weave_followup_damage", "실제 직접 피해에 대한 추가 공식"),
            ),
        ),
        FormulaEntry(
            key="settlement_rounding",
            section="정산",
            title="최종 피해 내림",
            expression="Settlement = floor(max(0, FullPrecisionDamage))",
            boundary="project_rule",
            variables=(FormulaVariable("FullPrecisionDamage", "적용되는 모든 곱연산 구간의 전체 정밀도 곱"),),
            applicable_when=("한 번의 실제 직접 피해·DOT·특수 피해·헥스 또는 브레이크 출구",),
            limitations=("중간 속성과 곱연산 구간은 내림하지 않으며, 치명 기댓값은 소수를 허용합니다.",),
            evidence=(
                _ref("project_contract", contract, "직접 피해 총공식", "최종 출구에서 일괄 내림"),
                _ref(
                    "implementation",
                    "src/services/battle_hit_replay_support.py",
                    "settle_replay_damage",
                    "고정 축 결정론적 정산 출구",
                ),
                _ref(
                    "public_behavior_test",
                    "tests/test_battle_hit_replay_service.py",
                    "test_replay_settlement_floors_only_after_all_factors",
                    "소수 피해는 최종 출구에서만 내림",
                ),
            ),
        ),
        FormulaEntry(
            key="max_hp_settlement",
            section="정산",
            title="HP 상한 감소 정산",
            expression=(
                "HpRatio = clamp(PreSettlementHp/OldMax, 0, 1); "
                "EffectiveHpLoss = (OldMax-NewMax) × HpRatio; "
                "EffectiveDamage = HitDamage + ΣEffectiveHpLoss"
            ),
            boundary="observed_runtime",
            variables=(
                FormulaVariable("OldMax/NewMax", "같은 하프·같은 대상의 확인된 최대 HP 경계값"),
                FormulaVariable("PreSettlementHp", "감소 직전 인근 히트의 최소 신뢰 현재 HP"),
            ),
            applicable_when=("Core 정식 감소 또는 귀속 가능한 연속 최대 HP 관측이 존재함",),
            limitations=(
                "설명 기반 추정은 따로 표시하며 정식 유효 피해에 포함하지 않습니다.",
                "대상 신원이 혼합되거나 축 연속성이 부족하면 대상을 넘나들어 귀속해서는 안 됩니다.",
            ),
            evidence=(
                _ref("project_contract", contract, "전투 리포트 최대 HP 감소 정산", "HP 상한 정산 기준"),
                _ref(
                    "implementation",
                    "src/services/battle_target_vital_analysis_service.py",
                    "BattleTargetVitalAnalysisService.derive",
                    "(scope_half,target_id) 단위로 관측 경계값 유지",
                ),
                _ref(
                    "implementation",
                    "src/services/battle_counterfactual_analysis_service.py",
                    "BattleCounterfactualAnalysisService.analyze",
                    "정식 HP 정산을 유효 피해에 포함",
                ),
            ),
        ),
    )


def _support_entries(
    snapshot: StaticFormulaEvidenceSnapshot,
) -> tuple[CounterfactualSupportEntry, ...]:
    dataset = snapshot.dataset_id
    static_db = "data/game_static.sqlite3"
    return (
        CounterfactualSupportEntry(
            key="fixed_axis_replay",
            category="핵심 불변 조건",
            mechanism="고정 축 히트별 반사실",
            scope="원본 액션·히트·시간·하프·대상은 유지하고 고정된 빌드 입력만 교체",
            status="complete",
            modeling_scheme="히트별 공식 후보를 원본 히트와 짝지어 리플레이하며, 알 수 없는 성분은 0으로 채우지 않고 정량화 상태로 들어갑니다.",
            evidence=(
                _ref("implementation", "src/services/battle_build_counterfactual_service.py", "BattleBuildCounterfactualService.compare", "고정 축 빌드 진입점"),
                _ref("public_behavior_test", "tests/test_battle_build_counterfactual_service.py", "BattleBuildCounterfactualServiceTests", "고정 축 결과와 갭 전파 커버"),
            ),
            consumer_entries=("BattleBuildCounterfactualService.compare", "BattleBuffCounterfactualService.calculate"),
            gap_codes=(),
            covered_dataset=dataset,
            covered_entities=("고정된 히트", "고정된 대상 프로필", "고정된 치명 분기"),
            limitations=("누락된 액션이나 누락된 히트를 생성하지 않습니다.",),
        ),
        CounterfactualSupportEntry(
            key="character_passives",
            category="캐릭터 메커니즘",
            mechanism="캐릭터 돌파 패시브",
            scope="명시적으로 등록된 상시·히트 한정·중첩·HP 상한 패시브",
            status="partial",
            modeling_scheme="돌파 단계에 따라 활성화하며, 상시 속성·상태 어댑터·명시적 파생 피해를 각각 투영합니다.",
            evidence=(
                _ref("implementation", "src/services/battle_character_passive_service.py", "BattleCharacterPassiveService", "명시적 패시브 목록과 해금 판정"),
                _ref("implementation", "src/services/battle_creation_passive_counterfactual_service.py", "BattleCreationPassiveCounterfactualService", "파생 피해의 partial/unavailable 전파"),
                _ref("public_behavior_test", "tests/test_battle_character_passive_service.py", "BattleCharacterPassiveServiceTests", "캐릭터/스킬 적용 범위와 중첩 경계"),
            ),
            consumer_entries=("BattleBuffCounterfactualPlanService.prepare", "BattleCreationPassiveCounterfactualService.calculate"),
            gap_codes=("passive_event_evidence_missing", "creation_downstream_unresolved"),
            covered_dataset=dataset,
            covered_entities=("白藏", "阿德勒", "法帝娅", "零", "达芙蒂尔", "翳", "薄荷", "哈尼娅", "海月", "真红", "浔"),
            limitations=("대상 상태·자원·공간 또는 소환 생명주기에 정식 이벤트가 없으면 여전히 알 수 없는 상태입니다.",),
        ),
        CounterfactualSupportEntry(
            key="awakening_six_effects",
            category="캐릭터 메커니즘",
            mechanism="6개 각성 효과와 3/6각성 공명",
            scope="구조화된 선택 효과·스킬 레벨 화이트리스트 및 감사 완료 캐릭터 전용 상태",
            status="partial",
            modeling_scheme="구체적인 effect_id를 고정하며, 통용 구조화 수정과 캐릭터 전용 타이밍은 분리해서 소비합니다.",
            evidence=(
                _ref("official_static", static_db, "character_awaken_effect", f"normalized effects={snapshot.awakening_effect_rows}"),
                _ref("official_static", static_db, "character_awaken_skill_level_bonus", f"structured bonuses={snapshot.awakening_skill_level_bonus_rows}"),
                _ref("implementation", "src/services/battle_daffodill_awakening_service.py", "BattleDaffodillAwakeningService", "다포딜 통찰/브레이크 전용 리플레이"),
                _ref("public_behavior_test", "tests/test_battle_daffodill_awakening_service.py", "BattleDaffodillAwakeningServiceTests", "효과 선택·중첩·정산 제한"),
            ),
            consumer_entries=("BattleConfirmedAwakeningBuffService.get", "BattleDaffodillAwakeningService.infer"),
            gap_codes=("awakening_special_adapter_missing", "awakening_runtime_state_unresolved"),
            covered_dataset=dataset,
            covered_entities=("구조화된 스킬 레벨 수정", "잔홍 Q 자격", "다포딜 통찰", "민트 각성 버프", "우미츠키 6각성 중첩"),
            limitations=("정적 각성 기록이 존재한다고 해서 런타임 발동과 소비가 모델링된 것은 아닙니다.",),
        ),
        CounterfactualSupportEntry(
            key="fork_and_weapon_skills",
            category="장비 메커니즘",
            mechanism="아크/콘솔/무기 스킬",
            scope="정식 매개변수·정적 수정·감사 완료 발동/주기/상태/잔여 규칙",
            status="partial",
            modeling_scheme="기본 패널과 히트별 동적 상태를 분리하며, 액션·태그·유효 시계·대상 조건에 따라 구간을 생성합니다.",
            evidence=(
                _ref("official_static", static_db, "fork_item", f"normalized forks={snapshot.fork_rows}"),
                _ref("official_static", static_db, "fork_modify_value", f"normalized modifiers={snapshot.fork_modifier_rows}"),
                _ref("implementation", "src/services/battle_fork_damage_completion_service.py", "BattleForkDamageCompletionService", "명시적 아크 완료 규칙 목록"),
                _ref("public_behavior_test", "tests/test_battle_fork_damage_completion_service.py", "test_remaining_catalog_entries_have_explicit_completion_rules", "등록 항목에 명시적 완료 전략이 있음"),
            ),
            consumer_entries=("BattleForkDamageStateService.infer_specialized", "BattleBuffCounterfactualPlanService.prepare"),
            gap_codes=("fork_runtime_condition_unresolved", "fork_random_outcome_unobserved", "weapon_state_axis_missing"),
            covered_dataset=dataset,
            covered_entities=("발동형", "주기형", "상태형", "치명타 발동형", "정적 속성형"),
            limitations=("랜덤·AND 조건·플레이어 상태 또는 대상 제어 상태는 partial/unavailable에 그칠 수 있습니다.",),
        ),
        CounterfactualSupportEntry(
            key="buff_ge_attributes",
            category="Buff/GE",
            mechanism="구조화된 Buff/GameplayEffect 속성",
            scope="해석된 property·연산·수치·Source/Target 태그와 구간",
            status="partial",
            modeling_scheme="정적 정의는 후보만 제공하며, 런타임 어댑터가 발동과 적용 범위를 증명한 뒤에야 히트에 투영합니다.",
            evidence=(
                _ref("official_static", static_db, "buff_definition", f"buff={snapshot.buff_definition_rows}; GE={snapshot.gameplay_effect_definition_rows}"),
                _ref("official_static", static_db, "buff_modifier", f"normalized modifiers={snapshot.buff_modifier_rows}"),
                _ref("implementation", "src/services/battle_buff_attribute_projection_service.py", "BattleBuffAttributeProjectionService", "히트별 속성 소비자"),
            ),
            consumer_entries=("BattleBuffCounterfactualBatchExecutor.calculate_ratios", "BattleHitCounterfactualRatioService.compare"),
            gap_codes=("buff_trigger_unresolved", "buff_target_condition_unresolved", "buff_property_consumer_missing"),
            covered_dataset=dataset,
            covered_entities=("패널 속성", "피해 증가", "취약", "防御无视", "저항 관통", "정식 최종 피해 속성"),
            limitations=("함수나 GE가 존재한다고 해서 발동·대상·중첩·지속 시간이 완전하다는 증명은 아닙니다.",),
        ),
        CounterfactualSupportEntry(
            key="formal_dot_classification",
            category="DOT",
            mechanism="정식 DOT 분류",
            scope="가져온 Gameplay Tag가 State.Damage.Dot인 피해",
            status="complete",
            modeling_scheme="정식 태그가 분류를 우선 소유하며, 이름과 수동 채널은 태그를 덮어쓰지 않습니다.",
            evidence=(
                _ref("official_static", static_db, "combat_blueprint_tag", f"DOT rows={snapshot.formal_dot_tag_rows}; assets={snapshot.formal_dot_tag_assets}"),
                _ref("implementation", "src/services/battle_axis_hit_projection_service.py", "_classification", "정식 태그 분류 진입점"),
                _ref("public_behavior_test", "tests/test_battle_counterfactual_analysis_service.py", "test_formal_damage_tags_own_dot_and_attachment_classification", "정식 태그가 DOT/부착물 분류를 가짐"),
            ),
            consumer_entries=("project_battle_axis_hits", "BattleSkillDamageEvidenceService.load"),
            gap_codes=(),
            covered_dataset=dataset,
            covered_entities=("State.Damage.Dot",),
            limitations=("complete는 분류만 뜻하며, 모든 DOT 종류의 중첩/부여/정산 상태가 리플레이됐다는 뜻은 아닙니다.",),
        ),
        CounterfactualSupportEntry(
            key="dot_state_replay",
            category="DOT",
            mechanism="DOT 중첩·지속 시간과 전용 FinalDamageUp",
            scope="噩梦·蚀心·鸩火·스코치 및 사키리 DOT 종류 곱연산 구간",
            status="partial",
            modeling_scheme="(scope_half,target_id) 단위로 정산 전 상태를 사용하며, 발동 히트 정산 후에야 갱신합니다.",
            evidence=(
                _ref("implementation", "src/services/battle_dot_stack_state_service.py", "reconstruct_dot_stack_states", "네 가지 상태 히트별 재구성"),
                _ref("public_behavior_test", "tests/test_battle_dot_stack_state_service.py", "test_sagiri_dot_final_multiplier_counts_kinds_not_layers", "중첩이 아닌 종류 기준 집계"),
                _ref("official_static", static_db, "buff_modifier:DOT-scoped FinalDamageUp", f"formal rows={snapshot.dot_scoped_final_damage_up_modifier_rows}"),
            ),
            consumer_entries=("BattleHitReplayService._replay_direct", "BattleHitCounterfactualRatioService.compare"),
            gap_codes=("dot_application_event_missing", "dot_state_kind_unmodeled", "dot_axis_incomplete"),
            covered_dataset=dataset,
            covered_entities=("噩梦", "蚀心", "鸩火", "浊燃", "State.Damage.Dot"),
            limitations=("감사되지 않은 DOT 유형과 정식 부여 이벤트 누락은 unknown을 유지합니다.",),
        ),
        CounterfactualSupportEntry(
            key="topple_base_formula",
            category="倾陷",
            mechanism="기본 브레이크 5개 곱연산 구간",
            scope="완전한 같은 하프 편성과 고정된 대상 브레이크 프로필에서의 캐릭터 기여",
            status="complete",
            modeling_scheme="캐릭터별로 레벨·브레이크 강도·대상 상한·방어력·저항을 리플레이하고 합산합니다.",
            evidence=(
                _ref("implementation", "src/services/damage_calculation_service.py", "DamageCalculationService.calculate_topple", "5개 곱연산 구간 공식"),
                _ref("implementation", "src/services/battle_topple_hit_replay_service.py", "BattleToppleHitReplayService.replay", "전투 리포트 캐릭터별 소비자"),
                _ref("public_behavior_test", "tests/test_battle_topple_hit_replay_service.py", "test_split_topple_uses_only_the_complete_same_half_roster", "편성 완전성 경계"),
            ),
            consumer_entries=("BattleToppleHitReplayService.replay", "battle_topple_marginal.topple_ratio"),
            gap_codes=(),
            covered_dataset=dataset,
            covered_entities=("레벨 곡선", "倾陷强度", "UnbalMax", "防御", "저항"),
            limitations=("complete는 기본 5개 곱연산 구간에만 해당하며, 특수 캐릭터 정산은 별도로 partial로 표시합니다.",),
        ),
        CounterfactualSupportEntry(
            key="topple_special_states",
            category="倾陷",
            mechanism="브레이크 전용 각성·창·추가 정산",
            scope="다포딜 통찰/각성·궤외 브레이크 창 및 감사 완료 추가 피해",
            status="partial",
            modeling_scheme="기본 브레이크 리플레이 위에 명시적 창·후보 정산·신뢰할 수 있는 지속 시간 요건을 겹쳐 적용합니다.",
            evidence=(
                _ref("implementation", "src/services/battle_daffodill_awakening_service.py", "BattleDaffodillAwakeningService", "통찰 중첩과 각성 전용 규칙"),
                _ref("public_behavior_test", "tests/test_battle_daffodill_awakening_service.py", "test_resonance_six_requires_reliable_topple_duration", "신뢰할 수 있는 지속 시간이 없으면 완료로 판정하지 않음"),
            ),
            consumer_entries=("BattleDaffodillMarginalService.derived_rows", "BattleBuildCounterfactualService.compare"),
            gap_codes=("topple_duration_unreliable", "topple_special_settlement_unobserved"),
            covered_dataset=dataset,
            covered_entities=("达芙蒂尔", "궤외 브레이크 버프"),
            limitations=("브레이크 GE가 존재한다고 해서 캐릭터 전용 소비 체인이 완료된 것은 아닙니다.",),
        ),
        CounterfactualSupportEntry(
            key="attachments",
            category="소환물/부착물",
            mechanism="정식 부착물 피해",
            scope="State.Damage.Attachment 분류와 감사 완료 부착물 배율/패시브",
            status="partial",
            modeling_scheme="정식 태그 분류이며, 기존 히트는 명확한 출처와 배율만 리플레이하고 누락된 공격을 지어내지 않습니다.",
            evidence=(
                _ref("official_static", static_db, "combat_blueprint_tag:State.Damage.Attachment", f"formal assets={snapshot.formal_attachment_tag_assets}"),
                _ref("public_behavior_test", "tests/test_battle_skill_damage_evidence_service.py", "test_kuhara_effect_two_doubles_only_attachment_damage", "부착물 전용 각성 적용 범위"),
                _ref("implementation", "src/services/battle_fork_trigger_refinement_service.py", "BattleForkTriggerRefinementService", "부착물 한정 동적 규칙"),
            ),
            consumer_entries=("project_battle_axis_hits", "BattleSkillDamageEvidenceService.load"),
            gap_codes=("attachment_owner_unresolved", "attachment_lifecycle_unobserved"),
            covered_dataset=dataset,
            covered_entities=("State.Damage.Attachment", "구원 씨앗", "등록된 부착물 아크 규칙"),
            limitations=("소환물의 등장·소멸·빈도와 누락된 히트는 정적 정의로 지어낼 수 없습니다.",),
        ),
        CounterfactualSupportEntry(
            key="summon_lifecycle",
            category="소환물/부착물",
            mechanism="소환물 생명주기와 파생 히트 생성",
            scope="완전한 정식 이벤트 축이 없는 소환 생성·존속·공간 및 자원 순환",
            status="unavailable",
            modeling_scheme="관측된 히트만 유지하며, 생명주기가 누락되면 후보 히트를 생성하지 않습니다.",
            evidence=(
                _ref("implementation", "src/services/battle_creation_passive_evaluation_service.py", "BattleCreationPassiveEvaluationService", "명시적으로 partial/unavailable 반환"),
                _ref("public_behavior_test", "tests/test_battle_creation_passive_counterfactual_service.py", "test_lifecycle_spatial_and_resource_passives_preserve_unknown_state", "생명주기 갭은 unknown 유지"),
            ),
            consumer_entries=("BattleCreationPassiveCounterfactualService.calculate",),
            gap_codes=("summon_lifecycle_axis_unavailable", "summon_spatial_state_unavailable", "summon_resource_state_unavailable"),
            covered_dataset=dataset,
            covered_entities=("관측된 생성 피해 분류",),
            limitations=("unavailable은 피해 0이나 메커니즘 미발동을 뜻하지 않습니다.",),
        ),
        CounterfactualSupportEntry(
            key="healing_damage_coupling",
            category="치료/보호막",
            mechanism="치료 이벤트 기반 피해 버프",
            scope="감사 완료 치료 이벤트·주기·치료 후 속성 구간",
            status="partial",
            modeling_scheme="먼저 치료 이벤트를 생성한 뒤 피해 버프가 소비하며, 스킬 액션으로 치료를 추측하는 폴백은 사용하지 않습니다.",
            evidence=(
                _ref("implementation", "src/services/battle_treatment_event_service.py", "BattleTreatmentEventService", "독립 치료 이벤트 축"),
                _ref("implementation", "src/services/battle_treatment_buff_service.py", "BattleTreatmentBuffService", "치료 후 피해 버프 소비자"),
                _ref("public_behavior_test", "tests/test_battle_character_passive_service.py", "test_eiroi_healing_passive_is_not_materialized_from_actions", "액션을 치료로 가장하지 않음"),
            ),
            consumer_entries=("BattleTreatmentReplayService.infer", "BattleBuffCounterfactualPlanService.prepare"),
            gap_codes=("treatment_event_evidence_missing", "treatment_formula_source_unresolved"),
            covered_dataset=dataset,
            covered_entities=("伊洛伊", "错误的门", "감사 완료 피해→치료 전환 이벤트"),
            limitations=("감사되지 않은 캐릭터, 애니메이션/스킬 근거 부족 또는 알 수 없는 치료 공식은 여전히 정량화할 수 없습니다.",),
        ),
        CounterfactualSupportEntry(
            key="healing_without_damage_consumer",
            category="치료/보호막",
            mechanism="피해 공식에 영향을 주지 않는 순수 치료량",
            scope="HP 회복만 바꾸고 피해 버프/정산 소비자가 없는 이벤트",
            status="not_applicable",
            modeling_scheme="이벤트 근거로 표시할 수는 있지만 피해 반사실 증분에는 포함되지 않습니다.",
            evidence=(
                _ref("implementation", "src/services/battle_treatment_replay_service.py", "BattleTreatmentReplayProjection", "치료 이벤트와 피해 버프 투영 분리"),
            ),
            consumer_entries=("StaticCatalogFormulaService",),
            gap_codes=(),
            covered_dataset=dataset,
            covered_entities=("순수 치료 출력"),
            limitations=("not_applicable은 피해 반사실에만 해당하며, 치료 시뮬레이션이 완전하다는 뜻이 아닙니다.",),
        ),
        CounterfactualSupportEntry(
            key="shield_state",
            category="치료/보호막",
            mechanism="플레이어 보호막 상태와 보호막량 반사실",
            scope="신뢰할 수 있는 과거 보호막 획득·소모 및 히트 전 상태 축이 필요한 메커니즘",
            status="unavailable",
            modeling_scheme="현재는 UI 문구·액션 또는 피해 오차로 보호막 상태를 역추론하지 않습니다.",
            evidence=(
                _ref("public_behavior_test", "tests/test_battle_fork_damage_completion_service.py", "test_missing_player_state_uses_high_hp_and_no_shield_defaults", "플레이어 상태 누락 시의 제한적 기본값을 명시적으로 기록"),
                _ref("repository_audit", "src/services/battle_fork_damage_state_service.py", "BattleForkDamageStateService", "상태 규칙은 통용 과거 보호막 축을 구성하지 않음"),
            ),
            consumer_entries=("BattleForkDamageStateService.infer_specialized",),
            gap_codes=("player_shield_axis_unavailable",),
            covered_dataset=dataset,
            covered_entities=(),
            limitations=("기본 보호막 없음은 명시적 규칙 분기에만 해당하며, 실제로 보호막이 없었음을 증명하지 않습니다.",),
        ),
        CounterfactualSupportEntry(
            key="max_hp_settlement",
            category="HP 정산",
            mechanism="대상 최대 HP 감소와 유효 HP 손실",
            scope="Core v4 정식 감소·단일 대상 연속 관측 및 감사 완료 출처 귀속",
            status="partial",
            modeling_scheme="(scope_half,target_id) 단위로 최대 HP 경계값을 유지하며, 정식 이벤트는 피해로 집계하고 설명 기반 추정은 따로 표시합니다.",
            evidence=(
                _ref("implementation", "src/services/battle_target_vital_analysis_service.py", "BattleTargetVitalAnalysisService.derive", "정식/관측 HP 경계값"),
                _ref("public_behavior_test", "tests/test_battle_target_vital_analysis_service.py", "test_lacrimosa_description_estimate_is_excluded_from_formal_damage", "추정치를 정식 피해에 섞지 않음"),
                _ref("public_behavior_test", "tests/test_battle_counterfactual_settlement_analysis.py", "test_max_hp_settlement_is_not_reported_as_hp_overlap_correction", "정산 채널 독립"),
            ),
            consumer_entries=("BattleCounterfactualAnalysisService.analyze", "BattleBuildCounterfactualService.compare"),
            gap_codes=("max_hp_axis_continuity_unavailable", "max_hp_source_attribution_unresolved"),
            covered_dataset=dataset,
            covered_entities=("Core v4 max_hp_reduction", "파디아 감사 완료 패시브", "라크리모사 5각성 관측 귀속"),
            limitations=("다중 대상 신원 충돌, 축 누락 또는 설명 텍스트만 있는 경우 정식 피해를 생성하지 않습니다.",),
        ),
        CounterfactualSupportEntry(
            key="native_counterfactual_core",
            category="실행기",
            mechanism="독립 C++ 반사실 코어",
            scope="독립 C++20 차분 슬라이스 및 아직 연결되지 않은 프로덕션 실행 진입점",
            status="partial",
            modeling_scheme="고정 축 무상태 직접 피해 슬라이스는 독립 검증되었으며, 매트릭스는 실행기를 선택·활성화·폴백하지 않습니다.",
            evidence=(
                _ref("implementation", "native/counterfactual-core/src/engine.cpp", "counterfactual::calculate", "제한된 순수 계산 sidecar"),
                _ref("public_behavior_test", "tools/counterfactual/run_cpp_differential.py", "main", "Python oracle 히트별 차분"),
                _ref("repository_audit", "src/services/battle_marginal_counterfactual_projection_service.py", "BattleMarginalCounterfactualProjectionService.apply", "현재 소비자는 여전히 Python 서비스 사용"),
                _ref("repository_audit", "src/services/battle_buff_counterfactual_batch_executor.py", "BattleBuffCounterfactualBatchExecutor", "현재 일괄 공식 실행은 Python"),
            ),
            consumer_entries=(),
            gap_codes=("native_production_consumer_unavailable", "native_stateful_mechanics_unavailable"),
            covered_dataset=dataset,
            covered_entities=("가산 패널/피해 증가/치명", "대상 저항", "DefIgnore", "Buff 8개 / 히트 56회 공개 차분"),
            limitations=("DOT·브레이크·반응·상태 머신·프로세스 생명주기는 이전되지 않았으며, unavailable을 ratio=1로 변환해서는 안 됩니다.",),
        ),
        CounterfactualSupportEntry(
            key="unknown_preservation",
            category="핵심 불변 조건",
            mechanism="알 수 없음/해당 없음 상태 전파",
            scope="complete/partial/unavailable/not_applicable 정량화 상태",
            status="complete",
            modeling_scheme="정량화된 피해 버킷과 갭에 따라 전파하며, unavailable 수치는 nullable을 유지합니다.",
            evidence=(
                _ref("implementation", "src/domain/battle_counterfactual_quantification.py", "BattleCounterfactualRatio", "4상태 정량화 contract"),
                _ref("public_behavior_test", "tests/test_battle_partial_quantification_buff_ui.py", "test_unavailable_does_not_render_zero_gain", "사용 불가는 이득 0으로 표시하지 않음"),
            ),
            consumer_entries=("BattleBuffCounterfactualService.calculate", "BattleMarginalCalculationService.calculate"),
            gap_codes=(),
            covered_dataset=dataset,
            covered_entities=("complete", "partial", "unavailable", "not_applicable"),
            limitations=("complete는 상태 전파 contract에만 해당하며, 구체적인 메커니즘의 근거 등급을 올리지 않습니다.",),
        ),
    )


class StaticCatalogFormulaService:
    """Load a release-static/code audit projection without touching account data."""

    PROJECTION_VERSION = "static-catalog-formula-v1"

    def __init__(self, database_path: str | Path | None = None) -> None:
        self._database_path = database_path

    def load(self) -> StaticCatalogFormulaDomain:
        with StaticCatalogFormulaQueries(self._database_path) as queries:
            snapshot = queries.evidence_snapshot()
        return self.from_snapshot(snapshot)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: StaticFormulaEvidenceSnapshot,
    ) -> StaticCatalogFormulaDomain:
        return StaticCatalogFormulaDomain(
            projection_version=cls.PROJECTION_VERSION,
            readonly=True,
            evidence_snapshot=snapshot,
            formulas=_formula_entries(snapshot),
            counterfactual_support=_support_entries(snapshot),
        )
