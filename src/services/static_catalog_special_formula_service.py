# 构建战斗机制图鉴的命名 DOT、全部环合与特殊结算公式。
"""Player-readable special formula records backed by read-only static curves."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.services.static_catalog_mechanics_models import PlayerField, PlayerSection
from src.storage.sqlite.static_catalog_formula_queries import (
    StaticCatalogFormulaQueries,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataError


@dataclass(frozen=True, slots=True)
class SpecialFormulaRecord:
    key: str
    family_key: str
    chapter: str
    title: str
    subtitle: str
    sections: tuple[PlayerSection, ...]
    aliases: tuple[str, ...] = ()
    status: str = "complete"
    notice: str = ""


def _field(label: str, value: str, tone: str = "neutral") -> PlayerField:
    return PlayerField(label, value, tone)


def _section(
    title: str,
    *fields: tuple[str, str] | tuple[str, str, str],
) -> PlayerSection:
    return PlayerSection(title, tuple(_field(*field) for field in fields))


def _formula_sections(
    formula: str,
    steps: tuple[str, ...],
    variables: tuple[tuple[str, str], ...],
    boundaries: tuple[str, ...],
) -> tuple[PlayerSection, ...]:
    return (
        _section(
            "전체 공식",
            ("公式", formula, "formula"),
            ("계산 순서", "\n".join(
                f"{index}. {step}" for index, step in enumerate(steps, start=1)
            ), "accent"),
        ),
        _section(
            "변수 출처",
            *((label, value, "accent") for label, value in variables),
        ),
        _section(
            "판정 및 제한",
            *(("경계", value, "warning") for value in boundaries),
        ),
    )


def _number(value: float) -> str:
    if value.is_integer():
        return f"{value:,.0f}"
    return f"{value:,.3f}".rstrip("0").rstrip(".")


class StaticCatalogSpecialFormulaService:
    """Load named formulas without reading account data or runtime battle state."""

    _CURVE_IDS = {
        "creation": "GE_ActorReaction_1_Damage",
        "creation_special": "GE_ActorReaction_1_1019_Damage",
        "scorch": "Buff_Reaction_5_new",
        "scorch_zankou": "Buff_Reaction_5_new_1036",
        "nova": "Buff_Reaction_4_new",
    }

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = database_path

    def load(self) -> tuple[SpecialFormulaRecord, ...]:
        with StaticCatalogFormulaQueries(self._database_path) as queries:
            curves = {
                curve.source_effect_id: curve.values
                for curve in queries.reaction_damage_curves()
            }
            topple_levels = queries.topple_level_curve()
        missing = set(self._CURVE_IDS.values()) - set(curves)
        if missing:
            raise StaticGameDataError(
                "정적 라이브러리에 전투 메커니즘 도감에 필요한 사이클 곡선이 없습니다:"
                + "、".join(sorted(missing))
            )
        return (
            self._reaction_tiers(curves),
            self._creation(),
            self._weave(),
            self._scorch(),
            self._nova(),
            self._infusion(),
            self._delay(),
            self._charge(),
            self._dissonance(),
            self._nightmare(),
            self._erosion(),
            self._pigeon_fire(),
            self._topple(topple_levels),
            self._daffodill_topple(),
            self._nightmare_max_hp(),
            self._fadia_shared_damage(),
        )

    def _reaction_tiers(
        self,
        curves: dict[str, tuple[float, ...]],
    ) -> SpecialFormulaRecord:
        columns = {
            label: curves[effect_id]
            for label, effect_id in (
                ("创生", self._CURVE_IDS["creation"]),
                ("블라썸 특수 항목", self._CURVE_IDS["creation_special"]),
                ("浊燃", self._CURVE_IDS["scorch"]),
                ("잔홍 스코치", self._CURVE_IDS["scorch_zankou"]),
                ("黯星", self._CURVE_IDS["nova"]),
            )
        }
        tier_sections = []
        for group_start in range(0, 16, 4):
            fields = []
            for tier in range(group_start, group_start + 4):
                level_start = tier * 5 + 1
                level_end = level_start + 4
                values = " ｜ ".join(
                    f"{label} {_number(points[tier])}"
                    for label, points in columns.items()
                )
                fields.append((
                    f"소스 티어 {tier} · 캐릭터 레벨 {level_start}–{level_end}",
                    values,
                    "tier",
                ))
            tier_sections.append(_section(
                f"공식 기본값 · 소스 티어 {group_start}–{group_start + 3}",
                *fields,
            ))
        return SpecialFormulaRecord(
            key="reaction_tiers",
            family_key="states",
            chapter="사이클 기본",
            title="사이클 귀속과 16티어 기본값",
            subtitle="블라썸/노바/스코치는 양측을 비교합니다. 헥스/스테인은 히트마다 실제 피해 출처를 읽습니다.",
            aliases=("사이클 기본", "0-5", "티어", "양측 속성", "취득 대상"),
            sections=(
                _section(
                    "귀속 규칙",
                    (
                        "먼저 비교하는 대상",
                        "블라썸·노바·스코치는 사이클 참여자 두 명의 각 “사이클 강도 × 캐릭터 레벨 기본값”을 비교해 더 높은 쪽을 취합니다. 헥스·스테인은 양측을 비교하지 않고, 해당 피해를 실제로 입힌 캐릭터를 히트마다 취합니다.",
                        "formula",
                    ),
                    (
                        "헥스와 스테인의 취득 대상",
                        "헥스는 기록된 각 원본 피해의 실제 출처 캐릭터를 취합니다. 스테인은 12초 구간 안의 각 수혜 피해 자체의 출처 캐릭터를 취합니다. 사이클을 발동한 QTE 캐릭터는 발동만 담당하며, 이후 히트별 사이클 강도를 고정적으로 제공하지 않습니다.",
                        "formula",
                    ),
                    (
                        "캐릭터 측 변수",
                        "블라썸/노바/스코치는 비교에서 이긴 캐릭터가 제공하고, 헥스/스테인은 실제 피해 출처 캐릭터가 제공합니다. 각 사이클 카드에 항목별로 명시됩니다.",
                        "accent",
                    ),
                    (
                        "피격 대상 제공",
                        "적 방어, 해당 피해 속성 저항, 동적 저항 감소, 받는 피해 증가. 중국어 몬스터 이름으로 추측하지 않습니다.",
                        "accent",
                    ),
                    (
                        "티어 공식",
                        "소스 티어 = floor((귀속 캐릭터 레벨 - 1) ÷ 5). 1–5레벨은 소스 티어 0, 76–80레벨은 소스 티어 15입니다.",
                        "formula",
                    ),
                ),
                *tier_sections,
                _section(
                    "정적 출처 설명",
                    (
                        "블라썸 특수 항목",
                        "정적 라이브러리에 독립적으로 존재하는 두 번째 블라썸 곡선입니다. 리소스 이름 접미사는 캐릭터 귀속 근거가 될 수 없으므로 도감에서는 “블라썸 특수 항목”으로만 표시합니다.",
                        "warning",
                    ),
                    (
                        "잔홍 스코치",
                        "정적 라이브러리에는 잔홍 전용 스코치 곡선이 따로 있습니다. 현재 16티어는 일반 스코치와 완전히 같지만, 독립된 정식 출처로 표시합니다.",
                        "warning",
                    ),
                    (
                        "수치 경계",
                        "표의 숫자는 현재 배포 정적 라이브러리의 정식 16티어 곡선에서 가져왔습니다. 공식이 이 숫자를 어떻게 사용하는지는 각 사이클 카드를 기준으로 합니다.",
                        "accent",
                    ),
                ),
            ),
        )

    @staticmethod
    def _creation() -> SpecialFormulaRecord:
        return SpecialFormulaRecord(
            key="reaction_creation",
            family_key="states",
            chapter="环合",
            title="사이클·블라썸",
            subtitle="레벨 기본값 × 사이클 강도 구간 × 방어 × 저항 × 취약. 치명타 없음.",
            aliases=("빛·령", "创生花", "블라썸 그루"),
            sections=_formula_sections(
                "블라썸 꽃 한 송이 피해 = 내림[레벨 기본값 × (1 + 사이클 강도/600) × 방어 구간 × 저항 구간 × 취약 구간]",
                (
                    "사이클 참여자 두 명 중에서 귀속 캐릭터를 선택합니다.",
                    "귀속 캐릭터 레벨에 따라 블라썸 16티어 기본값을 읽습니다.",
                    "귀속 캐릭터의 사이클 강도와 대상 측 방어/속성 저항을 사용합니다.",
                    "대상 취약을 곱한 뒤 최종 출력에서 내림합니다.",
                ),
                (
                    ("귀속 캐릭터", "사이클 참여자 두 명 중 “사이클 강도 × 레벨 기본값”이 더 높은 쪽을 취합니다."),
                    ("레벨 기초값", "귀속 캐릭터 레벨에 해당하는 블라썸 소스 티어를 취합니다. 80레벨은 9,000입니다."),
                    ("环合强度", "귀속 캐릭터의 히트 시점 사이클 강도를 취하며, 다른 참여자의 값을 취하거나 합산하지 않습니다."),
                    ("방어 구간", "캐릭터 레벨과 방어 관통은 귀속 캐릭터에서, 방어 속성과 방어 감소는 피격 대상에서 취합니다."),
                    ("저항 구간", "피해 속성은 정식 히트/피해 항목으로 확인합니다. 관통은 귀속 캐릭터에서, 저항과 저항 감소는 피격 대상에서 취합니다."),
                    ("취약 구간", "피격 대상의 해당 시점 받는 피해 증가를 취합니다."),
                ),
                (
                    "블라썸은 공격/HP/방어 패널을 읽지 않고, 통용 또는 속성 피해 증가도 읽지 않으며, 치명타가 발생하지 않습니다.",
                    "블라썸 그루 하나가 블라썸 꽃 5개를 생성하고 최대 3그루까지인 것은 상태와 히트 수에 속하며, 꽃 한 송이 공식에는 곱하지 않습니다.",
                ),
            ),
        )

    @staticmethod
    def _weave() -> SpecialFormulaRecord:
        return SpecialFormulaRecord(
            key="weave_followup",
            family_key="states",
            chapter="环合",
            title="사이클·헥스",
            subtitle="실제 령/주 직접 피해를 매번 기록하고, 12초 종료 시 원본 피해 속성에 따라 추가합니다.",
            aliases=("령·주", "弱点感应"),
            sections=_formula_sections(
                "헥스 추가 = 원본 피해 실제값 × [(1 + 기본 추가율) × (1 + 20%×사이클 강도/(사이클 강도+180)) - 1] × Π기타 특수 곱연산 구간",
                (
                    "고정 축에서 실제로 발생한 령/주 직접 피해를 매번 기록합니다.",
                    "기록된 각 원본 피해의 실제 출처 캐릭터 사이클 강도로 헥스 전용 강도 구간을 계산합니다.",
                    "각 원본 피해 자체의 피해 속성과 실제 피해를 계승합니다. 정식 DOT 등 기록된 출처에도 동일하게 적용됩니다.",
                    "12초 종료 시 각각 추가한 뒤 최종 출력에서 정수화합니다.",
                ),
                (
                    ("원본 피해 실제값", "헥스가 기록한 그 실제 피해를 취하며, 예상 패널로 다시 생성하지 않습니다."),
                    ("피해 속성", "각 원본 피해의 공격 측 속성을 계승합니다: 령 피해는 그대로 령, 주 피해는 그대로 주입니다."),
                    ("环合强度", "기록된 원본 피해의 실제 출처 캐릭터를 취합니다. 헥스를 발동한 QTE 캐릭터를 취하지 않으며, 사이클 양측을 비교하지도 않습니다."),
                    ("기본 추가율", "기본은 20%이며, 파티의 링코가 “약점 감응”을 해금하면 30%로 바뀝니다."),
                    ("한정 통용 피해", "링코 분기의 +10%는 이 추가 공격이 원래 가진 통용 피해 증가와 같은 구간에서 합산되며, 독립적인 ×1.10이 아닙니다."),
                    ("기타 특수 곱연산 구간", "헥스 추가 공격에 명확히 작용하는 독립 곱수만 취하며, 없으면 1입니다."),
                ),
                (
                    "헥스는 사이클 16티어 기본 피해 표를 읽지 않습니다. 이미 발생해 정식으로 기록된 실제 피해를 기준값으로 삼습니다.",
                    "상태가 조기에 제거되면 전용 효과가 명확히 요구하지 않는 한 기본적으로 즉시 정산하지 않습니다.",
                ),
            ),
        )

    @staticmethod
    def _scorch() -> SpecialFormulaRecord:
        return SpecialFormulaRecord(
            key="reaction_scorch",
            family_key="states",
            chapter="环合",
            title="사이클·스코치",
            subtitle="16티어 기본값 × 정산 전 중첩 수 × 사이클 강도 구간 × 대상 측 곱연산 구간 × DOT/치명타.",
            aliases=("주·암", "DOT", "持续伤害"),
            sections=_formula_sections(
                "스코치 1틱 = 내림[레벨 기본값 × 정산 전 중첩 수 × (1 + 사이클 강도/600) × 방어 구간 × 저항 구간 × 취약 구간 × DOT 전용 최종 구간 × 치명타 분기]",
                (
                    "사이클 참여자 두 명의 “레벨 기본값 × 사이클 강도”를 각각 계산해 더 높은 쪽을 귀속 캐릭터로 삼습니다.",
                    "같은 하프·같은 대상의 이번 틱 전 스코치 중첩 수를 읽습니다.",
                    "사이클 강도, 대상 방어/저항, DOT 전용 최종 구간을 계산합니다.",
                    "고정 50% 치명타 정책에 따라 비치명타/치명타 분기를 유지하고 최종 정수화합니다.",
                ),
                (
                    ("귀속 캐릭터", "사이클 참여자 두 명의 각 “레벨 기본값 × 사이클 강도”를 비교해 더 높은 쪽을 취합니다. QTE 캐릭터를 고정으로 취하지 않으며 합산하지도 않습니다."),
                    ("레벨 기초값", "각 참여자는 먼저 자신의 레벨에 따라 적용되는 스코치 소스 티어를 읽습니다. 일반 곡선과 잔홍 전용 곡선 모두 현재 80레벨에서 2,700입니다."),
                    ("정산 전 중첩 수", "일반 스코치는 1중첩 고정입니다. 잔홍 대체 상태는 같은 하프·같은 대상의 정식 DOT 부여 축으로 진행되며 최대 3중첩입니다."),
                    ("环合强度", "각 참여자는 자신의 사이클 강도로 귀속 비교에 참여합니다. 이긴 뒤에는 이번 스코치가 승자의 강도와 캐릭터 측 변수를 고정합니다."),
                    ("방어 구간", "캐릭터 레벨/관통은 귀속 캐릭터에서, 적 방어/방어 감소는 피격 대상에서 취합니다."),
                    ("저항 구간", "일반 스코치는 히트별로 확인된 실제 속성을 취하고, 잔홍 전용은 주속성으로 고정됩니다. 관통은 귀속 캐릭터에서, 저항은 대상에서 취합니다."),
                    ("DOT 전용 최종 구간", "히트 전 대상에 아직 유효한 정식 DOT 종류와 DOT로 명확히 한정된 최종 피해 증가를 취합니다."),
                    ("暴击", "치명 확률 50% 고정. 캐릭터 치명 확률은 읽지 않지만 귀속 캐릭터의 치명 피해는 읽습니다."),
                ),
                (
                    "스코치는 캐릭터 공격 패널을 읽지 않으며, 통용/속성 피해 증가도 읽지 않습니다.",
                    "스코치 자체의 틱 피해는 잔홍 스코치의 중첩 증가로 재귀되지 않습니다. 정식 부여 이벤트가 없으면 최종 피해에서 임의의 중첩 수를 역산할 수 없습니다.",
                ),
            ),
        )

    @staticmethod
    def _nova() -> SpecialFormulaRecord:
        return SpecialFormulaRecord(
            key="reaction_nova",
            family_key="states",
            chapter="环合",
            title="사이클·노바",
            subtitle="5초 후 정신 피해를 정산합니다. 방어 구간은 거치지 않고 정신 저항과 취약은 적용됩니다.",
            aliases=("암·혼", "心灵伤害"),
            sections=_formula_sections(
                "노바 1개 = 내림[레벨 기본값 × (1 + 사이클 강도/600) × 정신 저항 구간 × 취약 구간]",
                (
                    "암+혼이 발동할 때마다 참여자 두 명의 “레벨 기본값 × 사이클 강도”를 각각 계산합니다.",
                    "결과가 더 높은 쪽을 이번 노바 인스턴스의 귀속으로 삼고, 그 레벨 기본값을 고정합니다.",
                    "해당 귀속 캐릭터의 사이클 강도와 정신 관통을 계산합니다.",
                    "피격 대상의 정신 저항과 취약을 곱한 뒤 정수화합니다.",
                ),
                (
                    ("인스턴스 귀속", "사이클 참여자 두 명의 “레벨 기본값 × 사이클 강도”를 비교해 더 높은 쪽을 취하며, 발동 인스턴스마다 각자의 승자를 따로 유지합니다."),
                    ("레벨 기초값", "양쪽 모두 먼저 각자의 레벨에 따라 노바 원본 데이터를 읽고, 승리한 인스턴스가 해당 값을 고정합니다. 80레벨은 45,000입니다."),
                    ("环合强度", "양쪽은 각자의 사이클 강도로 비교에 참여하며, 승리한 인스턴스는 승자의 강도만 사용하고 합산하지 않습니다."),
                    ("방어 구간", "정신 피해는 방어 관통 100%로 처리하며, 방어 구간은 1로 고정됩니다."),
                    ("저항 구간", "정신 관통은 해당 인스턴스의 귀속 캐릭터에서, 정신 저항과 저항 감소는 피격 대상에서 가져옵니다."),
                    ("취약 구간", "피격 대상의 정산 시점 받는 피해 증가를 가져옵니다."),
                ),
                (
                    "노바는 공격력 패널, 통용 피해 증가, 치명타를 읽지 않습니다.",
                    "여러 인스턴스를 최종 일괄 정산할 때는 각 인스턴스 고유의 출처 속성을 유지해야 하며, 양쪽 사이클 강도를 합산해 한 번만 계산해서는 안 됩니다.",
                ),
            ),
        )

    @staticmethod
    def _infusion() -> SpecialFormulaRecord:
        return SpecialFormulaRecord(
            key="reaction_infusion",
            family_key="states",
            chapter="环合",
            title="사이클·스테인",
            subtitle="QTE 발동 후 12초 지속되며, 전원의 이후 개별 히트는 각각 자신의 피해 출처 캐릭터 사이클 강도를 읽습니다.",
            aliases=("혼상", "팀 피해 증가", "전원 피해 증가"),
            sections=_formula_sections(
                "이번 히트 스테인 최종 곱연산 구간 = 1.20 × [1 + 20% × 이번 히트 출처 사이클 강도 / (이번 히트 출처 사이클 강도 + 180)]; 수혜 히트 = 원래 전체 피해 × 이번 히트 스테인 최종 곱연산 구간",
                (
                    "정식 혼+상 QTE 정산으로 스테인 발동을 확인합니다.",
                    "QTE 정산 후부터 12초의 팀 피해 증가 구간을 설정하며, QTE를 발동한 해당 히트는 적용받지 않습니다.",
                    "구간 내 이후 개별 히트마다 해당 히트의 실제 피해 출처 캐릭터 사이클 강도를 읽습니다.",
                    "해당 히트의 원래 전체 공식 끝에 대응하는 스테인 최종 곱연산 구간을 곱합니다.",
                ),
                (
                    ("QTE 발동자", "스테인 상태의 발동 또는 갱신만 담당하며, 그 사이클 강도가 전원 12초 공용값으로 고정되지 않습니다."),
                    ("이번 히트 피해 출처", "구간 내 현재 수혜 피해의 실제 출처 캐릭터를 취하며, 그 캐릭터가 이번 히트 스테인 공식의 사이클 강도를 제공합니다."),
                    ("기본 피해 증가", "정식 사이클 상수 20%를 취해 선행 계수 1.20을 구성합니다."),
                    ("사이클 강도 보정", "이번 히트 피해 출처 캐릭터의 사이클 강도를 사용합니다: 1 + 20%×사이클 강도/(사이클 강도+180)."),
                    ("持续时间", "정식 사이클 상수 12초를 취하며, 발동한 QTE 정산 후부터 시작하고 이후 발동 시 구간이 갱신됩니다."),
                    ("수혜자", "구간 내 파티 전체 캐릭터의 이후 개별 히트이며, 캐릭터마다 사이클 강도가 달라 피해에 적용되는 곱연산 구간도 달라집니다."),
                ),
                (
                    "스테인은 팀 최종 피해 증가 버프로, 독립적인 “스테인 피해”를 생성하지 않으며 피해를 기록해 두었다가 12초 종료 시 일괄 정산하지도 않습니다.",
                    "팀 브레이크와 히트 단위가 아닌 최대 HP 정산은 현재 기본적으로 이 최종 곱연산 구간을 사용하지 않습니다.",
                    "히트의 실제 피해 출처를 확인할 수 없으면 상태는 표시할 수 있지만, 해당 히트의 사이클 강도 이득은 미분석 상태로 유지해야 하며 QTE 발동자나 인접 피해로 대체해서는 안 됩니다.",
                ),
            ),
        )

    @staticmethod
    def _delay() -> SpecialFormulaRecord:
        return SpecialFormulaRecord(
            key="reaction_delay",
            family_key="states",
            chapter="环合",
            title="사이클·레모라",
            subtitle="상+빛. 5초간 공격력 감소·감속이며, 자체 피해 공식은 없습니다.",
            aliases=("상빛", "공격력 감소", "감속"),
            status="not_applicable",
            sections=_formula_sections(
                "레모라 피해 = 해당 없음; 상태 5초 지속",
                ("상+빛 발동을 식별합니다.", "대상에게 5초 레모라 상태를 설정합니다."),
                (("상태 대상", "피격 대상이며, 이후 레모라 상태를 명시적으로 요구하는 메커니즘만 이를 소비합니다."),),
                ("레모라는 피해 히트를 생성하지 않으므로 직접 피해나 사이클 피해에 기입할 수 없습니다.",),
            ),
        )

    @staticmethod
    def _charge() -> SpecialFormulaRecord:
        return SpecialFormulaRecord(
            key="reaction_charge",
            family_key="states",
            chapter="环合",
            title="사이클·차지",
            subtitle="빛+령+상. 블라썸 꽃이 레모라 대상에 히트하면 추가 종결 에너지 10을 얻습니다.",
            aliases=("빛령상", "종결 에너지"),
            status="not_applicable",
            sections=_formula_sections(
                "추가 종결 에너지 = 10 (블라썸 꽃이 레모라 상태인 대상에 히트할 때)",
                ("대상의 레모라 상태를 확인합니다.", "히트가 블라썸 꽃에서 온 것인지 확인합니다.", "종결 에너지 10을 부여합니다."),
                (
                    ("레모라 상태", "이번 피격 대상의 히트 전 상태 축을 가져옵니다."),
                    ("블라썸 꽃 히트", "정식 블라썸 피해/히트 근거를 취하며, 중국어 스킬명으로 추측하지 않습니다."),
                ),
                ("차지는 자원을 변경할 뿐 직접 피해를 생성하지 않습니다.",),
            ),
        )

    @staticmethod
    def _dissonance() -> SpecialFormulaRecord:
        return SpecialFormulaRecord(
            key="reaction_dissonance",
            family_key="states",
            chapter="环合",
            title="사이클·디스코드",
            subtitle="암+혼+주. 대상에게 노바와 스코치가 동시에 있으면 브레이크 수치를 차감하며, 자체 피해는 없습니다.",
            aliases=("암혼주", "브레이크 차감"),
            status="partial",
            notice="15%는 현재 프로젝트의 약한 근거 기본값이며, 고정값·레벨 계수·멀티 보정은 아직 확인 대기 중입니다.",
            sections=_formula_sections(
                "현재 프로젝트 투영: 디스코드 브레이크 차감 = 피격 대상 브레이크 상한 × 15%",
                ("같은 대상에게 노바와 스코치가 동시에 있는지 확인합니다.", "해당 대상의 브레이크 상한을 읽습니다.", "15%를 차감하며, 피해 히트는 생성하지 않습니다."),
                (
                    ("노바/스코치 상태", "같은 하프·같은 대상의 상태 축을 취하며, 대상 간에 이어 붙이지 않습니다."),
                    ("브레이크 상한", "피격 대상의 고정 속성 묶음에 있는 브레이크 상한만 취하며, 참여 캐릭터 누구의 것도 취하지 않습니다."),
                ),
                ("15%는 아직 완전한 공식 실행 공식이 아니며, 브레이크 게이지 시퀀스에만 관여하고 브레이크 피해 곱연산 구간에는 들어가지 않습니다.",),
            ),
        )

    @staticmethod
    def _named_dot(
        *,
        key: str,
        title: str,
        aliases: tuple[str, ...],
        source: str,
        state: str,
        boundary: str,
    ) -> SpecialFormulaRecord:
        return SpecialFormulaRecord(
            key=key,
            family_key="states",
            chapter="지속 직접 피해",
            title=title,
            subtitle="정식 스킬 배율 × 이번 틱 상태 계수 × 공격 측 히트별 패널 × 대상 측 곱연산 구간 × DOT/치명타.",
            aliases=aliases,
            sections=_formula_sections(
                "이번 틱 피해 = 내림[스킬 기본 배율 × 이번 틱 상태 계수 × 공격 측 해당 패널 × 피해 증가 구간 × 방어 구간 × 저항 구간 × 취약 구간 × 독립 최종 구간 × DOT 전용 최종 구간 × 치명타 분기]",
                (
                    "정식 피해 항목과 유효 스킬 레벨에서 이번 틱 기본 배율을 읽습니다.",
                    "같은 하프·같은 대상 상태 축에서 히트 전 상태 계수를 읽습니다.",
                    "피해 출처 캐릭터의 히트 시점 패널과 버프를 읽습니다.",
                    "피격 대상의 방어력, 해당 저항, 디버프, DOT 상태를 읽은 뒤 정수화합니다.",
                ),
                (
                    ("스킬 기본 배율", source),
                    ("이번 틱 상태 계수", state),
                    ("패널/피해 증가/치명 피해", "이 지속 직접 피해의 출처 캐릭터가 히트 시점에 가진 고정 육성·장비·유효 버프를 가져옵니다."),
                    ("방어/저항/취약", "이 히트의 피격 대상을 취하며, 관통은 피해 출처 캐릭터에서, 방어 감소/저항 감소/취약은 대상에서 가져옵니다."),
                    ("DOT 전용 최종 구간", "히트 전 같은 대상에게 아직 유효한 정식 DOT 종류와 DOT로 명확히 한정된 최종 피해 증가를 가져옵니다."),
                    ("暴击", "치명 확률은 50% 고정입니다. 캐릭터 치명 확률은 사용하지 않지만, 치명타 분기는 여전히 출처 캐릭터의 치명 피해를 사용합니다."),
                ),
                (boundary,),
            ),
        )

    @classmethod
    def _nightmare(cls) -> SpecialFormulaRecord:
        return cls._named_dot(
            key="dot_nightmare",
            title="지속 직접 피해·악몽",
            aliases=("安魂曲", "악몽 중첩"),
            source="라크리모사 악몽 정식 피해 항목과 현재 유효한 일반 공격 스킬 레벨을 취하며, 일반/6각성 분기는 각자의 배율 단계를 사용합니다.",
            state="유효한 정식 직접 피해가 히트할 때마다 1중첩을 부여하며, 이번 틱은 그보다 앞선 중첩만 읽고 최대 10중첩입니다. 중첩마다 독립적으로 3초, 4각성 이후에는 6초 지속됩니다.",
            boundary="3각성 조기 정산은 “중첩별 남은 틱 수를 합산하고 초기화”하는 독립 공식이며, 현재 안정적인 연관이 없을 때는 일반 단일 틱으로 가장해서는 안 됩니다.",
        )

    @classmethod
    def _erosion(cls) -> SpecialFormulaRecord:
        return cls._named_dot(
            key="dot_erosion",
            title="지속 직접 피해·침심",
            aliases=("残虹", "침심 중첩"),
            source="잔홍 침심 정식 피해 항목과 현재 유효한 일반 공격 스킬 레벨을 취합니다.",
            state="정식 활성 중첩과 이번 틱 유효 계수는 분리합니다. “단일 1회분” 또는 “이번 히트 전 정식 중첩” 중에서만 실제 피해에 따라 선택하며, 최대 10중첩입니다.",
            boundary="1–10 임의 피팅을 허용해서는 안 됩니다. 부여 이벤트가 없을 때 계수 1은 최소 1회분만을 뜻하며 낮은 신뢰도를 유지합니다.",
        )

    @classmethod
    def _pigeon_fire(cls) -> SpecialFormulaRecord:
        return cls._named_dot(
            key="dot_pigeon_fire",
            title="지속 직접 피해·짐화",
            aliases=("짐화", "残虹", "짐화 중첩"),
            source="잔홍 짐화 정식 피해 항목과 현재 유효한 울티메이트 스킬 레벨을 취합니다.",
            state="“血宴入梦时” 최종 부여 시점에 한 번에 5중첩을 추가하며, 최대 10중첩·30초 지속입니다. 일반 공격과 DOT 틱 피해는 중첩을 추가하지 않습니다.",
            boundary="확산은 중첩·남은 시간·주기를 복제하지만, 단일 대상 축이 이를 근거로 다른 대상의 피해 히트를 만들어서는 안 됩니다.",
        )

    @staticmethod
    def _topple(level_values: tuple[float, ...]) -> SpecialFormulaRecord:
        level_sections = tuple(
            _section(
                f"공식 브레이크 기본값 · {start}–{start + 9}레벨",
                (
                    "레벨별 수치",
                    " ｜ ".join(
                        f"{level}레벨 {_number(level_values[level - 1])}"
                        for level in range(start, start + 10)
                    ),
                    "tier",
                ),
            )
            for start in range(1, 81, 10)
        )
        return SpecialFormulaRecord(
            key="topple_damage",
            family_key="settlement",
            chapter="倾陷",
            title="팀 브레이크 피해",
            subtitle="같은 하프의 캐릭터마다 5개 곱연산 구간을 따로 계산한 뒤 모든 캐릭터의 기여를 합산합니다.",
            aliases=("倾陷", "흰 게이지", "캐릭터별 합산"),
            sections=_formula_sections(
                "팀 브레이크 = Σ같은 하프 캐릭터 i [레벨 기본값_i × (1 + 브레이크 강도_i/300 + Σ브레이크 증가_i) × 적 브레이크 상한 구간 × 방어 구간_i × 저항 구간_i]",
                (
                    "이번 히트가 속한 하프의 전체 피해 출력 캐릭터 편성을 고정합니다.",
                    "캐릭터마다 자신의 레벨·브레이크 강도·속성·관통으로 한 칸씩 따로 계산합니다.",
                    "칸마다 같은 피격 대상의 브레이크 상한·방어력·해당 속성 저항을 읽습니다.",
                    "모든 캐릭터 칸을 합산한 뒤 최종적으로 내림합니다.",
                ),
                (
                    ("캐릭터 집합", "이번 히트와 같은 하프의 정식 피해 출력 캐릭터 전원을 취하며, Core에 이름이 걸린 발동 캐릭터가 유일한 피해 소유자는 아닙니다."),
                    ("레벨 기본값", "캐릭터마다 자신의 레벨에 해당하는 공식 1–80레벨 브레이크 곡선을 취하며, 80레벨은 3,603입니다."),
                    ("倾陷强度", "캐릭터마다 자신의 “기본×(1+증가)+고정 추가”를 취하며, 양쪽/전원을 먼저 합치지 않습니다."),
                    ("브레이크 피해 증가", "해당 효과를 제공하는 캐릭터 칸에만 들어가며, 브레이크 상태 자체가 자동으로 취약을 제공하지는 않습니다."),
                    ("브레이크 상한 구간", "같은 피격 대상의 UnbalMax를 취하며, 일반은 max(1, UnbalMax/3), 쟁봉 고계 Boss 등급은 25로 고정입니다."),
                    ("방어 구간", "칸마다 해당 캐릭터의 레벨과 방어 관통을 사용하며, 대상 방어력/방어 감소는 같은 피격 대상에서 가져옵니다."),
                    ("저항 구간", "칸마다 해당 캐릭터의 고유 피해 속성에 따라 자신의 관통을 취하고, 대상의 해당 속성 저항/저항 감소를 읽습니다."),
                ),
                (
                    "브레이크는 공격력/HP/방어력 스케일링 패널, 통용 피해 증가, 취약, 치명타, 독립 최종 피해 증가를 읽지 않습니다.",
                    "하프 편성이나 대상 프로필이 불완전하면 완전 리플레이 불가로 표시해야 하며, 다른 하프에서 보충해서는 안 됩니다.",
                ),
            ) + level_sections,
        )

    @staticmethod
    def _daffodill_topple() -> SpecialFormulaRecord:
        return SpecialFormulaRecord(
            key="daffodill_extra_topple",
            family_key="settlement",
            chapter="倾陷",
            title="다포딜·추가 브레이크",
            subtitle="다포딜 개인 브레이크 5개 곱연산 구간을 사용하며, 후보 5각성 추가 횟수는 1 + 통찰 중첩입니다.",
            aliases=("통찰", "5각성", "추가 브레이크 피해"),
            sections=_formula_sections(
                "1회 개인 브레이크 = 다포딜 레벨 기본값 × 다포딜 브레이크 강도 구간 × 대상 브레이크 상한 구간 × 다포딜 방어 구간 × 암속성 저항 구간; 후보 5각성 총 횟수 = 1 + 통찰 중첩",
                (
                    "기존 정식 브레이크 시점에 통찰 중첩을 읽습니다.",
                    "다포딜 자신의 개인 브레이크 5개 곱연산 구간으로 1회 값을 계산합니다.",
                    "0각성에서 이미 1회가 있으며, 후보 5각성은 통찰 중첩마다 1회씩 추가합니다.",
                ),
                (
                    ("캐릭터 속성", "레벨·브레이크 강도·방어 관통·암속성 관통은 모두 다포딜에서 가져오며, 팀의 다른 캐릭터에서 가져오지 않습니다."),
                    ("대상 속성", "브레이크 상한·방어력·암 저항과 해당 디버프는 이번 피격 대상에서 가져옵니다."),
                    ("통찰 중첩", "다포딜 전용 고정 축 상태를 취하며, 3각성 전에는 1중첩, 3각성 후에는 최대 2중첩입니다."),
                ),
                (
                    "추가 브레이크 GE가 나타나는 것은 공식 앵커만 증명할 뿐, 5각성이 활성화되었음을 자동으로 증명하지는 않습니다.",
                    "후보 신규 행의 기준선은 0이며, 원래 팀 브레이크 피해를 대체하지 않습니다.",
                ),
            ),
        )

    @staticmethod
    def _nightmare_max_hp() -> SpecialFormulaRecord:
        return SpecialFormulaRecord(
            key="nightmare_max_hp_settlement",
            family_key="settlement",
            chapter="HP 정산",
            title="라크리모사 5각성·악몽 HP 상한 감소",
            subtitle="출처 악몽 본체 피해의 200%이며, 유효 HP 손실은 정산 전 HP 비율로 다시 환산합니다.",
            aliases=("최대 HP", "라크리모사 5각성", "악몽200%"),
            sections=_formula_sections(
                "예상 최대 HP 감소 = 출처 악몽 피해 × 200%; 유효 HP 손실 = 최대 HP 하락 × clamp(정산 전 HP/이전 최대 HP, 0, 1)",
                (
                    "정식 악몽 히트에만 바인딩하며, 인접 HP 샘플이나 일반 직접 피해는 받지 않습니다.",
                    "해당 출처 악몽의 후보/원본 공식 비율에 따라 감소값을 연동합니다.",
                    "대상의 정산 전 HP 비율에 따라 유효 피해로 환산합니다.",
                ),
                (
                    ("출처 악몽", "같은 대상에 대해 귀속이 명확한 라크리모사 정식 악몽 히트를 취하며, 피해 변화는 해당 히트의 공식 비율을 계승합니다."),
                    ("최대 HP 경계", "이전/신규 최대 HP와 정산 전 HP는 모두 같은 하프·같은 대상의 연속 HP 축에서 가져옵니다."),
                    ("귀속 캐릭터", "라크리모사에 귀속되며, 대상 HP 데이터는 유효 손실만 결정할 뿐 출처 캐릭터를 바꾸지 않습니다."),
                ),
                ("설명 기반 추정과 Core 정식 최대 HP 하락은 분리해야 하며, 대상 연속 축이 없을 때는 부분/사용 불가 상태를 유지합니다.",),
            ),
        )

    @staticmethod
    def _fadia_shared_damage() -> SpecialFormulaRecord:
        return SpecialFormulaRecord(
            key="fadia_shared_damage",
            family_key="settlement",
            chapter="共享伤害",
            title="파디아·파멸 체험 공유 피해",
            subtitle="파디아가 실제로 받은 피해의 300%/600%를 공유하며, 파디아 HP 상한의 8%/25% 누적 상한 제약을 받습니다.",
            aliases=("法帝娅", "파멸 체험", "共享伤害"),
            status="partial",
            sections=_formula_sections(
                "이번 공유 가능량 = min(파디아 실제 받은 피해 × 공유 비율, 누적 상한 - 기존 누적); 누적 상한 = 파디아 HP 상한 × 상한 비율",
                (
                    "먼저 파디아가 이번에 실제로 받은 피해를 가져옵니다.",
                    "일반 300%/8% 또는 2각성 600%/25%로 계산합니다.",
                    "누적 상한으로 절단되며, 효과 제거 시 정식 규칙에 따라 차액을 보충합니다.",
                ),
                (
                    ("실제 받은 피해", "파디아 본인이 실드와 파티 분담을 거친 뒤 실제로 받은 신뢰할 수 있는 피해만 취하며, 적의 원본 피해 패킷은 취하지 않습니다."),
                    ("공유 비율/상한 비율", "파디아 해당 메커니즘의 일반 또는 2각성 분기를 취합니다."),
                    ("HP 상한", "파디아의 현재 HP 상한을 취하며, 피격 적이나 공유 대상의 것은 취하지 않습니다."),
                    ("누적값", "이번 메커니즘 인스턴스가 이미 공유한 누적 이력을 가져옵니다."),
                ),
                ("현재 전투 리포트의 받은 피해 근거는 실드/분담 정산보다 앞설 수 있으며, 근거가 부족할 때는 바로 3배나 6배를 곱해서는 안 됩니다.",),
            ),
        )


__all__ = ["SpecialFormulaRecord", "StaticCatalogSpecialFormulaService"]
