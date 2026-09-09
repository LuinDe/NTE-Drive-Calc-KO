# 构建公式与反事实模型详情，隔离于目录查询和效果关系编排。
"""Pure detail projections for formula and counterfactual mechanics."""

from __future__ import annotations

from src.services.static_catalog_formula_presenters import (
    CounterfactualMatrixRow,
    FormulaDetailView,
)
from src.services.static_catalog_mechanics_models import (
    CatalogLink,
    EvidenceStage,
    FORMULA_CHAPTER_BY_KEY,
    FORMULA_FAMILY_BY_KEY,
    MODEL_FAMILY_BY_KEY,
    MechanicsDetail,
    PLACEHOLDER_NAME,
    PlayerField,
    PlayerSection,
    encode_record,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


_PLAYER_FORMULAS: dict[
    str,
    tuple[str, str, tuple[tuple[str, str], ...]],
] = {
    "panel_attribute": (
        "캐릭터 패널 속성",
        "패널 값 = 기본값 × (1 + 백분율 보너스) + 고정 보너스",
        (("기본값", "현재 캐릭터 본체·아크 및 기타 정식 기본값을 사용하며, 피격 대상은 사용하지 않습니다."),
         ("백분율 보너스", "현재 캐릭터의 장비·육성 및 히트 시점에 유효한 버프의 같은 속성 백분율 항목을 사용합니다."),
         ("고정 보너스", "현재 캐릭터의 장비·계정 가구 보너스 및 히트 시점에 유효한 버프의 같은 속성 고정 항목을 사용합니다.")),
    ),
    "skill_multiplier": (
        "技能倍率",
        "스킬 배율 = 레벨 배율 × (1 + 배율 보정 합계)",
        (("레벨 배율", "공격자의 해당 정식 피해 항목을 사용하며, 해당 스킬의 현재 유효 레벨에 따라 배율 단계를 읽습니다."),
         ("배율 보정", "공격자의 현재 육성과 버프 중 해당 스킬 태그에 정식으로 적용되는 배율 보정을 사용합니다.")),
    ),
    "direct_damage": (
        "직접 피해 계산",
        "최종 직접 피해 = 내림[max(0, 스킬 배율 × 대응 패널 × 피해 증가 × 치명타 × 방어 × 저항 × 취약 × Π독립 피해 증가)]",
        (("技能倍率", "공격자의 해당 정식 피해 항목과 현재 유효 스킬 레벨을 사용합니다."),
         ("대응 패널", "공격자의 히트 시점 공격력, HP 또는 방어력 패널을 사용하며, 어느 것을 쓸지는 피해 항목이 지정합니다."),
         ("피해 증가 구간", "공격자의 히트 시점에 이번 타격에 적용되는 통용·속성·스킬·상태 피해 증가를 사용합니다."),
         ("暴击", "치명 확률/고정 정책과 치명 피해는 공격자 또는 해당 메커니즘의 정식 규칙을 따릅니다."),
         ("방어 구간", "캐릭터 레벨과 방어 관통은 공격자에서, 방어력과 방어 감소는 피격 대상에서 가져옵니다."),
         ("저항 구간", "속성 관통은 공격자에서, 대응 속성 저항과 저항 감소는 피격 대상에서 가져옵니다."),
         ("취약 구간", "피격 대상이 이번 타격 시점에 지니고 있는 받는 피해 증가를 사용합니다."),
         ("독립 피해 증가", "공격자의 이번 타격에 명시적으로 작용하는 모든 독립 최종 배수를 항목별로 곱합니다.")),
    ),
    "damage_increase": (
        "피해 증가",
        "피해 증가 배율 = 1 + 각종 피해 증가 합계",
        (("피해 증가 합계", "공격자의 히트 시점에 이번 타격에 적용되는 통용·속성·스킬·상태 피해 증가를 사용하며, 같은 구간끼리 더합니다."),),
    ),
    "vulnerability": (
        "취약",
        "취약 배율 = 1 + 대상 받는 피해 증가 합계",
        (("받는 피해 증가", "이번 피격 대상에게 히트 시점에 유효한 받는 피해 증가 Debuff만 사용하며, 공격자의 피해 증가는 포함하지 않습니다."),),
    ),
    "critical": (
        "暴击",
        "비치명타 = 내림(완전 정밀도 피해); 치명타 = 내림[완전 정밀도 피해 × (1 + 치명 피해)]; 기댓값 = (1-r)×비치명타 + r×치명타",
        (("暴击率", "일반 직접 피해는 공격자의 히트 시점 치명 확률을 사용하며, 고정 50% 또는 치명타 불가 메커니즘은 정식 정책으로 덮어씁니다."),
         ("暴击伤害", "공격자의 히트 시점 치명 피해를 사용하며, 피격 대상은 이 변수를 제공하지 않습니다.")),
    ),
    "defense": (
        "防御",
        "대상 유효 방어력 = [기초 방어력×(1+방어력 증가)+고정 방어력]÷6×(1-방어 관통)×(1-방어 감소); 방어 배율 = (캐릭터 레벨+100)÷(대상 유효 방어력+캐릭터 레벨+100)",
        (("캐릭터 레벨", "이번 피해 출처 캐릭터의 레벨을 사용합니다."),
         ("방어 관통", "이번 피해 출처 캐릭터의 히트 시점 방어 관통 또는 방어 무시를 사용합니다."),
         ("대상 방어력", "이번 피격 대상의 고정 속성 팩에 있는 기초·백분율·고정 방어력을 우선 사용합니다."),
         ("방어 감소", "이번 피격 대상에게 히트 시점에 유효한 방어 감소를 사용합니다.")),
    ),
    "resistance": (
        "저항",
        "유효 저항 X = 기초 저항-저항 감소-속성 관통; X≥0이면 저항 배율=1-X; X<0이면 저항 배율=1-X÷1.10",
        (("피해 속성", "해당 정식 피해 항목 또는 히트별로 확인된 실제 속성을 사용합니다."),
         ("기초 저항", "이번 피격 대상의 해당 피해 속성에 대한 고정된 전투 전 저항을 사용합니다."),
         ("저항 감소", "피격 대상에게 히트 시점에 유효하고 해당 속성과 일치하는 저항 감소를 사용합니다."),
         ("속성 관통", "피해 출처 캐릭터의 히트 시점에 해당 속성과 일치하는 관통을 사용합니다.")),
    ),
    "independent_final_damage": (
        "독립 최종 피해 증가",
        "독립 피해 증가 = 명시적인 독립 최종 피해 증가 각각을 항목별로 곱함",
        (("독립 최종 피해 증가", "정식 효과 중 공격자, 피격 대상, 히트별 태그와 발동 조건을 모두 만족하는 최종 피해 증가를 사용하며, 항목별로 곱합니다."),),
    ),
    "dot_damage": (
        "持续伤害",
        "지속 피해 1틱 = 직접 피해 동종 곱연산 구간 × 상태 중첩 수 × 지속 피해 전용 피해 증가",
        (("직접 피해 동종 곱연산 구간", "공격자는 스킬 배율, 패널, 피해 증가, 관통과 치명 피해를 제공하고, 피격 대상은 방어력, 저항, 디버프와 취약을 제공합니다."),
         ("상태 중첩 수", "같은 하프, 같은 피격 대상의 이번 틱 이전 정식 상태 중첩 수 또는 제한된 정산 계수를 사용합니다."),
         ("지속 피해 종류", "같은 피격 대상에게 이번 틱 이전에 여전히 유효하고 정식 DOT 식별 정보를 가진 종류 수를 사용합니다.")),
    ),
    "topple_damage": (
        "倾陷伤害",
        "브레이크 피해 = 레벨 곡선 × 브레이크 강도 × 브레이크 상한 × 방어 × 저항",
        (("레벨 곡선", "팀 내 같은 하프의 각 캐릭터가 각자 자신의 레벨에 대응하는 브레이크 곡선을 사용합니다."),
         ("倾陷强度", "각 캐릭터가 각자 자신의 브레이크 강도와 브레이크 피해 증가를 사용합니다."),
         ("브레이크 상한", "팀 전체의 각 칸이 공통으로 이번 피격 대상의 브레이크 상한 또는 고정 단계 오버라이드를 사용합니다."),
         ("방어/저항", "각 칸의 관통은 해당 캐릭터에서, 방어력·대응 속성 저항·디버프는 피격 대상에서 가져옵니다.")),
    ),
    "weave_followup": (
        "헥스 추가 피해",
        "헥스 추가 = 기록된 원본 피해 × 사이클 강도 보정 × 특수 피해 증가",
        (("원본 피해 실제값", "헥스에 기록된 공격자의 실제 피해를 사용하며, 정식 DOT 등 기록 가능한 출처도 그대로 유지하고 예상값을 다시 계산하지 않습니다."),
         ("环合强度", "기록된 원본 피해의 실제 출처 캐릭터를 사용하며, 사이클 양측을 비교하지도 않고 QTE 발동자를 고정적으로 취하지도 않습니다."),
         ("피해 속성", "각 원본 피해의 실제 속성을 계승하며, 피격 대상이 해당 속성 저항을 제공합니다.")),
    ),
    "settlement_rounding": (
        "최종 피해 내림",
        "최종 피해 = 내림(0 이상의 완전 정밀도 피해)",
        (("완전 정밀도 피해", "해당 공식의 모든 적용 곱연산 구간을 곱한 결과를 사용하며, 중간 과정에서는 내림하지 않습니다."),),
    ),
    "max_hp_settlement": (
        "HP 상한 감소 정산",
        "유효 피해 = 이번 피해 + 현재 HP 비율로 환산한 HP 상한 감소",
        (("HP 상한 변화", "같은 하프, 같은 피격 대상에서 확인된 신·구 최대 HP 경계를 사용합니다."),
         ("정산 전 HP", "해당 대상의 HP 상한 감소 직전 인근 히트의 신뢰할 수 있는 최소 현재 HP를 사용합니다."),
         ("출처 귀속", "정식 이벤트 또는 감사를 거친 메커니즘이 출처를 바인딩할 수 있을 때만 캐릭터에 귀속하며, 대상 데이터 자체는 출처를 증명하지 않습니다.")),
    ),
}


_PLAYER_FORMULA_STEPS: dict[str, tuple[str, ...]] = {
    "panel_attribute": (
        "현재 캐릭터의 본체·아크 등 정식 기초값을 집계합니다.",
        "같은 속성의 백분율 출처를 같은 구간끼리 더한 뒤 기초값에 곱합니다.",
        "마지막으로 같은 속성의 고정값 출처를 더합니다.",
    ),
    "skill_multiplier": (
        "공격자의 해당 피해 항목에 바인딩된 스킬과 유효 스킬 레벨로 배율 단계를 선택합니다.",
        "이 스킬 태그에 정식으로 작용하는 배율 보정을 선별해 더합니다.",
        "레벨 배율에 (1 + 보정 합계)를 곱합니다.",
    ),
    "direct_damage": (
        "이번 타격의 피해 항목, 스케일링 패널, 속성, 치명타 정책과 공격자를 확인합니다.",
        "스킬 배율, 패널, 피해 증가, 치명타, 방어, 저항, 취약을 순서대로 계산합니다.",
        "적용되는 각 독립 최종 피해 증가를 항목별로 곱합니다.",
        "전 과정에서 완전 정밀도를 유지하고 최종 출구에서만 내림합니다.",
    ),
    "damage_increase": (
        "이번 타격의 출처, 속성, 스킬, 상태 태그로 공격자의 피해 증가를 선별합니다.",
        "같은 피해 증가 구간 안에서 먼저 더합니다.",
        "합계에 1을 더해 피해 증가 배율을 얻습니다.",
    ),
    "vulnerability": (
        "이번 타격의 피격 대상을 바인딩합니다.",
        "히트 시점에 여전히 유효하고 이번 타격에 적용되는 대상의 받는 피해 증가를 선별합니다.",
        "같은 구간끼리 더한 뒤 1을 더해 취약 배율을 얻습니다.",
    ),
    "critical": (
        "먼저 치명타를 제외한 완전 정밀도 피해를 완성합니다.",
        "일반, 고정 치명 확률, 치명타 불가 또는 미확인 정책에 따라 분기를 선택합니다.",
        "비치명타와 치명타 후보를 각각 최종 출구에서 내림한 뒤 치명 확률로 기댓값을 계산합니다.",
    ),
    "defense": (
        "피격 대상의 고정 속성 팩에서 원래 방어력을 재구성합니다.",
        "공격자의 방어 관통과 대상의 방어 감소를 적용합니다.",
        "피해 출처 캐릭터의 레벨로 최종 방어 배율을 계산합니다.",
    ),
    "resistance": (
        "이번 타격의 피해 속성에 따라 피격 대상의 대응 기초 저항을 읽습니다.",
        "대상의 동적 저항 감소와 공격자의 대응 속성 관통을 뺍니다.",
        "유효 저항의 양음에 따라 구간별 공식을 선택합니다.",
    ),
    "independent_final_damage": (
        "정식 효과의 출처, 대상, 태그, 발동 조건을 항목별로 대조합니다.",
        "조건을 만족하는 각 최종 피해 증가를 먼저 (1 + 증가량)으로 변환합니다.",
        "모든 독립 항목을 항목별로 곱하며, 통용 피해 증가 구간에 합치지 않습니다.",
    ),
    "dot_damage": (
        "정식 DOT 식별 정보, 출처 캐릭터와 이번 피격 대상을 확인합니다.",
        "같은 하프, 같은 대상의 히트 전 상태에서 중첩 수 또는 제한 계수를 읽습니다.",
        "지속 직접 피해 곱연산 구간과 정식 치명타 정책으로 이번 틱을 계산한 뒤 DOT 전용 최종 구간을 적용합니다.",
        "이번 틱의 최종 출구에서만 내림합니다.",
    ),
    "topple_damage": (
        "같은 하프의 전체 편성과 같은 피격 대상을 고정합니다.",
        "각 캐릭터가 각자 레벨 곡선, 브레이크 강도, 관통과 고유 피해 속성을 읽습니다.",
        "캐릭터별로 계산해 합산하고 최종 출구에서 내림합니다.",
    ),
    "weave_followup": (
        "조건에 맞는 각 실제 직접 피해와 그 속성을 기록합니다.",
        "기록된 각 원본 피해의 실제 출처 캐릭터의 사이클 강도를 읽습니다.",
        "12초 종료 시 항목별로 추가한 뒤 최종적으로 내림합니다.",
    ),
    "settlement_rounding": (
        "공식 중간 곱연산 구간의 완전 정밀도를 유지합니다.",
        "최종 결과를 0 이상으로 제한합니다.",
        "메커니즘이 정의한 최종 출구에서만 내림합니다.",
    ),
    "max_hp_settlement": (
        "같은 하프, 같은 대상의 연속된 구/신 최대 HP 경계를 바인딩합니다.",
        "정산 전 현재 HP가 구 최대 HP에서 차지하는 비율로 유효 손실을 환산합니다.",
        "출처가 확인된 정산만 해당 캐릭터에 귀속합니다.",
    ),
}


class StaticCatalogMechanicsDetailProjector:
    """Project formula/model domain facts without owning catalog lookup state."""

    def __init__(self, terminology_service: StaticCatalogTerminologyService) -> None:
        self._terminology = terminology_service

    def formula_detail(self, formula: FormulaDetailView) -> MechanicsDetail:
        title, expression, player_variables = self.player_formula(formula)
        variables = tuple(
            PlayerField(label, meaning, "accent")
            for label, meaning in player_variables
        )
        steps = _PLAYER_FORMULA_STEPS.get(formula.key, ())
        sections = (
            PlayerSection("전체 공식", (PlayerField("公式", expression, "formula"),)),
            PlayerSection("계산 순서", (PlayerField(
                "단계별 과정",
                "\n".join(
                    f"{index}. {step}"
                    for index, step in enumerate(steps, start=1)
                ),
                "accent",
            ),)),
            PlayerSection("변수 출처", variables),
            PlayerSection(
                "판정 및 제한",
                tuple(
                    PlayerField("적용 조건", value, "accent")
                    for value in formula.applicable_when
                ) + tuple(
                    PlayerField("경계", value, "warning")
                    for value in formula.limitations
                ),
            ),
        )
        return MechanicsDetail(
            record_id=encode_record("formula", formula.key),
            card_kind="formula",
            title=title,
            subtitle=expression,
            family_key=FORMULA_FAMILY_BY_KEY[formula.key],
            badges=(FORMULA_CHAPTER_BY_KEY.get(formula.key, "公式"),),
            status=None,
            owner_label="전역 공식",
            owner_link=None,
            redirect_only=False,
            sections=sections,
            identity_fields=(),
            evidence_stages=(),
            related_links=(),
            audit_references=tuple(
                f"{source.location}::{source.symbol}" for source in formula.sources
            ),
        )

    @staticmethod
    def player_formula(
        formula: FormulaDetailView,
    ) -> tuple[str, str, tuple[tuple[str, str], ...]]:
        """Return the curated Chinese projection; internal symbols never reach Qt."""
        try:
            return _PLAYER_FORMULAS[formula.key]
        except KeyError as exc:
            raise LookupError(f"공식에 플레이어용 중국어 투영이 없습니다: {formula.key}") from exc

    def model_detail(self, model: CounterfactualMatrixRow) -> MechanicsDetail:
        consumer = (
            "전투 리포트 반사실 연결됨"
            if model.consumer_entries
            else "프로덕션 반사실 미연결"
        )
        if model.key == "native_counterfactual_core":
            consumer = "차분 검증 컴포넌트, 프로덕션 미연결"
        covered_entities = (
            (model.covered_entities,)
            if isinstance(model.covered_entities, str)
            else model.covered_entities
        )
        sections = (
            PlayerSection("작동 방식", (PlayerField(
                "모델링 방안",
                self.player_model_text(model.modeling_scheme),
            ),)),
            PlayerSection(
                "현재 커버리지",
                tuple(
                    PlayerField(
                        "커버리지",
                        self.player_model_text(value),
                        "success",
                    )
                    for value in covered_entities
                ) or (PlayerField("커버리지", "확인 가능한 대상 없음", "warning"),),
            ),
            PlayerSection(
                "갭 및 제한",
                tuple(
                    PlayerField("갭", self._human_gap(code), "warning")
                    for code in model.gap_codes
                ) + tuple(
                    PlayerField(
                        "제한",
                        self.player_model_text(value),
                        "warning",
                    )
                    for value in model.limitations
                ),
            ),
        )
        return MechanicsDetail(
            record_id=encode_record("model", model.key),
            card_kind="model",
            title=model.mechanism,
            subtitle=model.scope,
            family_key=MODEL_FAMILY_BY_KEY[model.key],
            badges=(model.status_label, model.category),
            status=model.status,
            owner_label="공통 메커니즘",
            owner_link=None,
            redirect_only=False,
            sections=sections,
            identity_fields=(),
            evidence_stages=self._evidence_stages(model, consumer),
            related_links=self._model_links(model),
            audit_references=model.evidence,
            notice=consumer,
        )

    def player_model_text(self, value: str) -> str:
        text = str(value)
        for property_id in ("DefIgnore", "UnbalMax"):
            if property_id not in text:
                continue
            label = self._formal_attribute_name(property_id)
            replacement = (
                label
                if label == PLACEHOLDER_NAME
                else f"{label} ({property_id})"
            )
            text = text.replace(property_id, replacement)
        replacements = (
            ("(scope_half,target_id)", "(하프, 대상 식별 정보)"),
            ("Core v4 max_hp_reduction", "Core v4 최대 HP 감소 이벤트"),
            ("Buff 8개 / 히트 56회 공개 차분", "Buff 8개, 히트별 공개 차분 56회"),
            (
                "complete/partial/unavailable/not_applicable",
                "완전, 부분, 사용 불가, 해당 없음",
            ),
            ("not_applicable", "해당 없음"),
            ("unavailable", "不可用"),
            ("partial", "부분 커버리지"),
            ("complete", "완전"),
            ("unknown", "未知"),
            ("nullable", "미정량화 상태"),
            ("ratio=1", "배율 1"),
            ("contract", "계약"),
            ("UI 문구", "인터페이스 문구"),
        )
        for source, target in replacements:
            text = text.replace(source, target)
        return text

    def _formal_attribute_name(self, property_id: str) -> str:
        term = self._terminology.resolve("equipment_attribute", property_id)
        if term.name_available and term.display_name:
            return term.display_name
        return PLACEHOLDER_NAME

    @staticmethod
    def _model_links(
        model: CounterfactualMatrixRow,
    ) -> tuple[tuple[str, CatalogLink], ...]:
        formula_by_model = {
            "buff_ge_attributes": "damage_increase",
            "formal_dot_classification": "dot_damage",
            "dot_state_replay": "dot_damage",
            "topple_base_formula": "topple_damage",
            "topple_special_states": "topple_damage",
            "max_hp_settlement": "max_hp_settlement",
            "fixed_axis_replay": "direct_damage",
        }
        key = formula_by_model.get(model.key)
        if key is None:
            return ()
        return ((
            "관련 공식 보기",
            CatalogLink(
                "combat_mechanics",
                encode_record("formula", key),
                "formula",
            ),
        ),)

    @staticmethod
    def _human_gap(code: str) -> str:
        translations = {
            "native_production_consumer_unavailable": "프로덕션 반사실 미연결",
            "native_stateful_mechanics_unavailable": "상태 메커니즘 미이전",
            "summon_lifecycle_axis_unavailable": "소환물 생명주기 축 없음",
            "summon_spatial_state_unavailable": "소환물 공간 상태 없음",
            "summon_resource_state_unavailable": "소환물 자원 상태 없음",
            "player_shield_axis_unavailable": "플레이어 보호막 상태 축 없음",
            "dot_application_event_missing": "정식 DOT 부여 이벤트 없음",
            "dot_state_kind_unmodeled": "아직 모델링되지 않은 DOT 유형 존재",
            "dot_axis_incomplete": "히트별 축 불완전",
            "attachment_lifecycle_unobserved": "부착물 생명주기 관측 미완료",
            "attachment_owner_unresolved": "부착물 출처 귀속 미확인",
            "buff_property_consumer_missing": "Buff 속성에 확인된 공식 소비자 없음",
            "buff_target_condition_unresolved": "Buff 대상 조건 미확인",
            "buff_trigger_unresolved": "Buff 발동 조건 미확인",
            "max_hp_axis_continuity_unavailable": "최대 HP 상태 축 불연속",
            "max_hp_source_attribution_unresolved": "최대 HP 변화 출처 미확인",
            "topple_duration_unreliable": "브레이크 지속 시간 근거 신뢰 불가",
            "topple_special_settlement_unobserved": "브레이크 특수 정산 미관측",
            "treatment_event_evidence_missing": "정식 치료 이벤트 근거 없음",
            "treatment_formula_source_unresolved": "치료 공식 출처 미확인",
        }
        return translations.get(code, "누락 설명이 아직 현지화되지 않음")

    @staticmethod
    def _evidence_stages(
        model: CounterfactualMatrixRow,
        consumer_summary: str,
    ) -> tuple[EvidenceStage, ...]:
        gaps = " ".join(model.gap_codes)
        unavailable = model.status == "unavailable"
        partial = model.status == "partial"

        def state(*tokens: str) -> str:
            if any(token in gaps for token in tokens):
                return "unavailable" if unavailable else "partial"
            return "complete" if not partial else "partial"

        return (
            EvidenceStage(
                "definition",
                "정식 정의",
                "complete",
                "메커니즘 식별 정보와 적용 범위 등록됨",
            ),
            EvidenceStage(
                "trigger",
                "발동 근거",
                state("trigger", "event"),
                "정식 히트별 데이터 또는 감사된 이벤트로 판단하며, 문구에서 추측해 보충하지 않음",
            ),
            EvidenceStage(
                "state",
                "상태 축",
                state("state", "axis", "lifecycle", "duration"),
                "중첩 수, 지속 시간, 대상 범위는 현재 근거에 따라 전파",
            ),
            EvidenceStage(
                "hit",
                "히트별 투영",
                state("owner", "target", "spatial"),
                "실제 히트별 데이터를 유지하며, 누락된 동작이나 히트를 생성하지 않음",
            ),
            EvidenceStage(
                "formula",
                "공식 소비",
                state("formula", "consumer"),
                "확인된 피해 곱연산 구간 또는 전용 정산에만 진입",
            ),
            EvidenceStage(
                "production",
                "프로덕션 반사실",
                "complete" if model.status == "complete" and model.consumer_entries
                else "partial" if model.consumer_entries
                else "unavailable",
                consumer_summary,
            ),
        )
