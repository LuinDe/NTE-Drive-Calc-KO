# 怪物与玩法域的正式玩法 Buff、选择与掉落投影。
"""Qt-free gameplay projections with complete player-facing rule details."""

from __future__ import annotations

from collections import Counter
import re
from typing import Any

from src.services.static_catalog_monster_display import (
    NAME_UNAVAILABLE,
    display_buff_option,
    display_catalog_scalar,
    display_damage_type,
)
from src.services.static_catalog_monster_models import (
    CatalogDetail,
    CatalogEntry,
    CatalogSection,
    CatalogValue,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


_STATUS_LABELS = {
    "complete": ("드롭 확인됨", "정식 클로저가 확정된 드롭 아이템과 수량을 제공했습니다."),
    "partial": ("일부 정보 사용 가능", "정식 클로저에서 이미 확인된 드롭 아이템과 수량만 표시합니다."),
    "unavailable": ("드롭 현재 사용 불가", "현재 확인할 수 있는 고정 드롭 수량이 없습니다."),
}
_GAP_LABELS = {
    "name_missing": "일부 드롭 아이템 이름은 아직 제공되지 않았습니다. 확인된 수량은 정식 클로저 기준으로 표시합니다.",
    "drop_group_missing": "정식 드롭 그룹이 아직 제공되지 않았습니다.",
    "sequence_branch_divergent": "드롭 시퀀스에 서로 다른 분기가 있어 단일 수량을 확정할 수 없습니다.",
    "sequence_not_deterministic": "드롭 시퀀스가 고정 결과가 아니므로 추정 수량은 표시하지 않습니다.",
}
_TRIGGER_LABELS = {
    "whole_battle": "상시 적용",
    "corruption_damage_stack": "지정 피해를 입힌 후 중첩",
    "while_target_toppled": "대상 브레이크 중 적용",
}


def _clean_description(value: object) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


class StaticCatalogMonsterGameplayProjector:
    """Project formal gameplay rows without opening another DAO."""

    def __init__(self, terminology: StaticCatalogTerminologyService) -> None:
        self._terminology = terminology

    def witch_entries(self, rows: list[dict[str, Any]]) -> tuple[CatalogEntry, ...]:
        return tuple(
            CatalogEntry(
                key=f"witch_buff|{row['buff_id']}",
                domain="encounter",
                play_mode="witch_blessing",
                title=self._name(row.get("name_zh")),
                subtitle="전투 전 선택 가능한 전투 전체 축복",
                primary_id=str(row["buff_id"]),
                localization_available=self._available(row.get("name_zh")),
            )
            for row in rows
        )

    def witch_detail(self, row: dict[str, Any]) -> CatalogDetail:
        entry = self.witch_entries([row])[0]
        property_name = self._term_name("equipment_attribute", row.get("property_id"))
        amount = self._amount(row.get("property_value"), bool(row.get("is_percent")))
        section = CatalogSection(
            "마녀의 축복",
            (
                CatalogValue(
                    label="축복 효과",
                    value=str(row.get("property_id") or ""),
                    provenance="official_static",
                    display_label=property_name,
                    display_value=amount,
                ),
            ),
            _clean_description(row.get("description_zh")),
        )
        return CatalogDetail(entry, (section,))

    def outer_buff_detail(self, row: dict[str, Any]) -> CatalogDetail:
        title = self._name(row.get("buff_name_zh"))
        entry = CatalogEntry(
            key=f"outer_buff|{row['level_config_id']}",
            domain="encounter",
            play_mode="outer_realm",
            title=title,
            subtitle=self._name(row.get("season_name_zh")),
            primary_id=str(row["level_config_id"]),
            secondary_id=str(row.get("buff_id") or ""),
            localization_available=self._available(row.get("buff_name_zh")),
        )
        values = tuple(
            CatalogValue(
                label="시즌 Buff 구성 요소",
                value=str(component.get("property_id") or ""),
                provenance="official_static",
                display_label=self._term_name(
                    "equipment_attribute", component.get("property_id")
                ),
                display_value=self._component_description(component),
            )
            for component in row.get("components", ())
        )
        section = CatalogSection(
            "궤외 시즌 Buff",
            values,
            _clean_description(row.get("description_zh")),
        )
        return CatalogDetail(entry, (section,))

    def feast_option(self, category: str, option: dict[str, Any]) -> CatalogValue:
        effect_kind = str(option.get("effect_kind") or "")
        display_category = (
            f"{display_damage_type(self._terminology, option.get('damage_type'))} 증가"
            if effect_kind == "resistance_up"
            else category
        )
        return CatalogValue(
            label="쟁봉 보너스",
            value=str(option.get("option_id") or ""),
            provenance="official_static",
            display_label=display_category,
            display_value=display_buff_option(self._terminology, option),
            note=(
                "도전 시간 규칙은 Buff 곱연산 구간에 속하지 않습니다."
                if effect_kind == "time_limit"
                else ""
            ),
        )

    def drop_section(self, projection: dict[str, Any] | None) -> CatalogSection:
        status = str((projection or {}).get("status") or "unavailable")
        status_label, status_copy = _STATUS_LABELS.get(
            status, _STATUS_LABELS["unavailable"]
        )
        values = [CatalogValue(
            label="드롭 상태",
            value=status,
            provenance="official_static" if projection else "unavailable",
            display_label=status_label,
            display_value=status_copy,
        )]
        if projection is None:
            values.append(CatalogValue(
                label="정보 공백",
                value="",
                provenance="unavailable",
                display_label="보완 대기 정보",
                display_value="이 난이도에는 아직 정식 드롭 클로저가 없어 추정 수량을 표시하지 않습니다.",
            ))
        for item in (projection or {}).get("items", ()):
            values.append(CatalogValue(
                label="드롭 아이템",
                value=str(item.get("item_id") or ""),
                provenance="official_static",
                display_label=self._term_name("item", item.get("item_id")),
                display_value=f"× {int(item['quantity'])}",
            ))
        counts = Counter(
            str(gap.get("reason_code") or "")
            for gap in (projection or {}).get("gaps", ())
        )
        for reason_code, count in counts.items():
            message = _GAP_LABELS.get(
                reason_code,
                "정식 드롭 집합에 아직 해석되지 않은 정보가 있어 추정 수량을 표시하지 않습니다.",
            )
            values.append(CatalogValue(
                label="정보 공백",
                value=reason_code,
                provenance="unavailable",
                display_label="보완 대기 정보",
                display_value=(f"{count}개 항목: {message}" if count > 1 else message),
            ))
        return CatalogSection("정식 드롭", tuple(values), status_copy)

    def _term_name(self, entity_kind: str, stable_id: object) -> str:
        identity = str(stable_id or "").strip()
        if not identity:
            return NAME_UNAVAILABLE
        term = self._terminology.resolve(entity_kind, identity)
        return term.display_name if term.name_available else NAME_UNAVAILABLE

    @staticmethod
    def _amount(value: object, is_percent: bool) -> str:
        number = float(value)
        return f"{number * 100:g}% 증가" if is_percent else f"{number:g} 증가"

    @staticmethod
    def _component_description(component: dict[str, Any]) -> str:
        parts = [
            _TRIGGER_LABELS.get(
                str(component.get("trigger_kind") or ""),
                "적용 조건은 정식 설명 참조",
            ),
            f"수치 +{display_catalog_scalar(component.get('property_value'))}",
        ]
        if component.get("duration_seconds") is not None:
            parts.append(f"지속 {display_catalog_scalar(component['duration_seconds'])}초")
        if int(component.get("stack_limit_count") or 1) > 1:
            parts.append(f"최대 {int(component['stack_limit_count'])}중첩")
        if component.get("trigger_cooldown_seconds") is not None:
            parts.append(
                f"발동 간격 {display_catalog_scalar(component['trigger_cooldown_seconds'])}초"
            )
        return " · ".join(parts)

    @staticmethod
    def _available(value: object) -> bool:
        text = str(value or "").strip()
        return bool(text and "\ufffd" not in text)

    @classmethod
    def _name(cls, value: object) -> str:
        return str(value).strip() if cls._available(value) else NAME_UNAVAILABLE
