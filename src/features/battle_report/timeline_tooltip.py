# 构建统一时间轴点击和悬停共享的证据详情。
"""Qt-free tooltip projection for timeline selections."""

from __future__ import annotations

from collections.abc import Callable

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredAction,
    BattleInferredInput,
    BattleTimelineDamageGroup,
)
from src.features.battle_report.timeline_layout import (
    TimelineSelection,
    format_damage,
    format_time,
)
from src.services.skill_name_rendering_service import (
    preferred_battle_damage_name,
    render_battle_event_type,
)


def build_timeline_tooltip(
    selected: TimelineSelection,
    *,
    projected_time: Callable[[int], int],
    hit_heading: str = "정식 히트",
) -> str:
    if selected.kind == "hit":
        hit = selected.payload
        assert isinstance(hit, BattleAnalysisHit)
        damage_name = preferred_battle_damage_name(
            hit.damage_name,
            hit.skill_name,
            hit.ability_id,
        )
        source = (
            f"\n출처 스킬: {hit.skill_name}"
            if hit.skill_name not in {"", damage_name, "미식별 스킬"}
            else ""
        )
        return (
            f"{hit_heading} · {format_time(projected_time(hit.relative_time_us))} · "
            f"{hit.character_name}\n{damage_name}{source}\n"
            f"히트 ID: {hit.event_id}\n"
            f"{render_battle_event_type(hit.classification, hit.attack_type, hit.damage_attribute)}"
            f" · {hit.target_name} · {format_damage(hit.damage)}"
        )
    if selected.kind == "damage_group":
        group = selected.payload
        assert isinstance(group, BattleTimelineDamageGroup)
        source = (
            f"\n출처 스킬: {group.source_skill_name}"
            if group.source_skill_name
            not in {"", group.damage_name, "미식별 스킬"}
            else ""
        )
        vital_group = group.channel_key in {
            "max_hp_reduction",
            "max_hp_reduction_estimated",
        }
        title = (
            "HP 상한 설명 추정"
            if group.channel_key == "max_hp_reduction_estimated"
            else "HP 상한 파생 정산"
            if vital_group
            else "스킬 피해 그룹"
        )
        details = "" if not group.detail_lines else "\n" + "\n".join(group.detail_lines)
        footer = (
            "관측된 최대 HP 감소와 메커니즘 귀속은 별도로 저장됩니다. 이 값은 정식 히트를 덮어쓰지 않습니다."
            if group.channel_key == "max_hp_reduction"
            else "스킬 설명 기반의 약한 근거입니다. 기본적으로 정식 유효 피해에 포함하지 않습니다."
            if group.channel_key == "max_hp_reduction_estimated"
            else "가로 막대 두께는 그룹 전체 피해에, 원 크기는 단일 히트 피해에 비례합니다."
        )
        return (
            f"{title} · {group.character_name} · {group.channel_label}\n"
            f"{group.damage_name}{source}\n"
            f"{format_time(projected_time(group.start_us))}—"
            f"{format_time(projected_time(group.end_us))} · "
            f"{group.hits}회 · {format_damage(group.damage)}{details}\n"
            f"{footer}"
        )
    if selected.kind == "action":
        action = selected.payload
        assert isinstance(action, BattleInferredAction)
        return (
            f"추정 동작 · {action.character_name} · 신원 신뢰도{action.identity_confidence} / "
            f"시간 신뢰도{action.timing_confidence}\n"
            f"{action.input_sequence} · {action.action_name}\n"
            f"{format_time(projected_time(action.start_us))}—"
            f"{format_time(projected_time(action.end_us))} · "
            f"{action.hits}히트 · {format_damage(action.damage)}\n{action.inference_basis}"
        )
    item = selected.payload
    assert isinstance(item, BattleInferredInput)
    if item.is_character_switch:
        return (
            f"추정 QTE 전환 · {item.character_name}\n"
            f"{format_time(projected_time(item.start_us))} · "
            f"시간 신뢰도{item.timing_confidence}\n"
            "아바타는 전환 결과를 나타냅니다. 현재 실제 키보드 슬롯 근거는 없습니다."
        )
    return (
        f"추정 입력 · {item.character_name} · {item.display_text}\n"
        f"{format_time(projected_time(item.start_us))}—"
        f"{format_time(projected_time(item.end_us))} · 시간 신뢰도{item.timing_confidence}\n"
        + (
            "정적 길게 누르기 절차상 피해는 누르고 있는 동안 발생할 수 있습니다. 손을 떼는 시점은 여전히 추정입니다."
            if item.hold_damage_mode == "during_hold"
            else "정적 길게 누르기 절차상 피해는 손을 떼거나 임계값에 도달한 뒤의 출력 구간에서 발생합니다."
            if item.hold_damage_mode == "after_hold"
            else "현재 실제 키보드·마우스 이벤트가 없어 이를 근거로 반복 클릭 횟수를 판단할 수 없습니다."
        )
    )
