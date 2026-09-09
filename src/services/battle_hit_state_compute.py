# 调度 DOT 与残虹蓄焰状态计算并恢复原有中文证据。
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.domain.native_analysis import BattleComputeBackend


_NAMES = {
    "nightmare": "噩梦", "erosion": "蚀心", "venom": "鸩火",
    "scorch": "浊燃", "cang_field": "判予秋", "adler_skill": "诛恶护持",
}
_STATE_BASIS = {
    "nightmare": (
        "같은 대상의 히트를 순방향으로 리플레이해 히트 후 중첩을 추가합니다;"
        "라크리모사는 QTE와 학습 E를 제외한 모든 유효 직접 피해 hit마다 「噩梦」 1중첩을 부여합니다;"
        "「噩梦」 중첩이 실제로 틱 피해를 줄 때마다 잔홍 스코치 1중첩을 추가로 보충합니다; 최대 10중첩;"
        "각 중첩은 시간 정지를 제외한 시계로 독립적으로 만료 시간을 계산합니다;"
    ),
    "erosion": (
        "같은 대상의 히트를 순방향으로 리플레이해 「蚀心」 부여를 추적합니다;"
        "환상 형태의 일반/분기 히트는 1중첩, 강화 스킬 히트는 5중첩을 추가합니다; 최대 10중첩;"
        "30초 지속 시간은 시간 정지를 제외한 유효 전투 시계로 계산하며 시간 정지 중에는 흐르지 않습니다"
    ),
    "venom": (
        "같은 대상의 히트를 순방향으로 리플레이해 「鸩火」 부여를 추적합니다;"
        "「血宴入梦时」의 최종 부여 시점에 5중첩을 추가합니다; 환상 형태 일반 공격의 확산은 기존 「鸩火」의 지속 시간만 갱신하고,"
        "중첩은 늘리지 않습니다; 최대 10중첩;"
        "30초 지속 시간은 시간 정지를 제외한 유효 전투 시계로 계산하며 시간 정지 중에는 흐르지 않습니다"
    ),
    "cang_field": (
        "「判予秋」 전개 직접 피해를 우선 기준으로 Q 배치를 구분하며,"
        "같은 대상의 첫 틱은 해당 배치 상태에 1중첩이 적용되었다는 가시적 근거로 삼습니다; 전개 직접 피해가 없으면 정식 12/16초 영역을 기준으로,"
        "보수적 배치를 유지합니다; 실제로 보이는 각 DOT 틱 피해는 추가로 잔홍의 1중첩 보충을 발동하며,"
        "중간에 누락된 틱은 역추정하지 않습니다"
    ),
    "adler_skill": (
        "「诛恶护持」 초기 직접 피해를 우선 기준으로 E 배치를 구분하며,"
        "같은 대상의 첫 틱은 해당 배치 상태에 1중첩이 적용되었다는 가시적 근거로 삼습니다; 초기 직접 피해가 없으면 정식 10초 지속 시간을 기준으로,"
        "보수적 배치를 유지합니다; 실제로 보이는 각 DOT 틱 피해는 추가로 잔홍의 1중첩 보충을 발동하며,"
        "중간에 누락된 틱은 역추정하지 않습니다"
    ),
}
_LOWER_BASIS = {
    "nightmare": (
        "이번 히트 전에 부여 이벤트를 찾지 못했습니다; 이번 틱은 「噩梦」 상태가 최소 1개 존재함만 증명하며,"
        "정확한 중첩 수는 역추정하지 않습니다"
    ),
    "erosion": (
        "; 이번 히트 전에 가시적인 부여 이벤트가 없어 이번 틱은 「蚀心」 상태가 최소 1개 존재함만 증명하며,"
        "관측 하한을 정확한 1중첩으로 해석하지 않습니다"
    ),
    "venom": (
        "; 이번 히트 전에 가시적인 부여 이벤트가 없어 이번 틱은 「鸩火」 상태가 최소 1개 존재함만 증명하며,"
        "관측 하한을 정확한 1중첩으로 해석하지 않습니다"
    ),
}


def compute_hit_state(
    kind: str, inputs: dict[str, Any], *, backend: BattleComputeBackend,
    checkpoint: Callable[[], None] | None,
) -> list[dict[str, Any]]:
    if checkpoint:
        checkpoint()
    results = backend.compute_batch("hit_state_v1", ({**inputs, "kind": kind},),
                                    checkpoint=checkpoint)
    if len(results) != 1 or not isinstance(results[0].get("states"), list):
        raise ValueError("invalid_hit_state_result")
    rows = results[0]["states"]
    if not all(isinstance(row, dict) and isinstance(row.get("event_id"), str) for row in rows):
        raise ValueError("invalid_hit_state_result")
    return rows


def _dot_basis(row: dict[str, Any]) -> str:
    kind = row["kind"]
    if row["early"]:
        return (
            "일반 공격 마지막 단이 3각 잔여 피해 정산을 발동합니다;"
            "현재는 잔여 정산 횟수를 중첩별로 아직 리플레이하지 않았으므로 이번 히트는 일반 「噩梦」 틱 피해로 추정하지 않습니다"
        )
    if kind == "scorch":
        if row["effect"] == "buff_reaction_5_new_1036":
            return (
                "하프와 대상별로 격리해 잔홍 스코치 공유 상태를 리플레이합니다; 이번 틱은 먼저 정산 전 중첩을 읽습니다;"
                "잔홍 돌파 패시브는 상한을 3으로 바꿉니다; 스코치 반응이 일어날 때마다 먼저 비활성 스코치 1중첩을 저장하고,"
                "이후 전투 리포트에 비스코치 DOT 틱 피해 hit가 실제로 1개 나타날 때마다 스코치도 함께 1중첩 증가하며 전체 피해가 활성화됩니다;"
                "중간에 누락된 틱은 역추정하지 않으며, 스코치 자체의 틱 피해는 재귀적으로 중첩을 쌓지 않습니다."
                "이 투영은 로컬 기록 전투 리포트의 잔차 회귀로 제약됩니다"
            )
        return (
            "일반 스코치는 최대 1중첩입니다; 정식 발동은 전체 15초 지속 시간만 갱신하고,"
            "실제 주기 틱 피해는 다음 틱을 초기화하지 않으며 중첩을 잔홍의 3중첩으로 올리지도 않습니다"
        )
    return _STATE_BASIS[kind] + (_LOWER_BASIS.get(kind, "") if row["lower"] else "")


def _dot_final_basis(row: dict[str, Any]) -> str:
    if not row["final_enabled"]:
        return "사키리 돌파 2 패시브 「먹어도 돼?」 미활성; DOT 전용 최종 곱연산 구간은 1로 고정"
    if not row["final_active"]:
        return (
            "사키리 돌파 2 패시브 「먹어도 돼?」는 활성화되었으나,"
            "이번 히트 정산 전 대상의 스코치 상태가 아직 확인되지 않아 DOT 전용 최종 곱연산 구간은 1로 고정"
        )
    kinds = "、".join(_NAMES[kind] for kind in row["active_kinds"])
    recent = row["recent_only"]
    return (
        "사키리 돌파 2 패시브 「먹어도 돼?」; 대상이 정산 전에 이미 스코치 상태; "
        f"활성 DOT 종류: {kinds}, 총 {row['active_dot_kind_count']}종; "
        "1 + min(종류 수 × 25%, 100%)"
        + ("; 그중" + "、".join(_NAMES[kind] for kind in recent)
           + " 항목은 이번 히트 전 1.5초 이내의 최근 정식 틱 피해로 확인된 것이며, 이를 근거로 전체 지속 시간을 갱신하지는 않습니다"
           if recent else "")
    )


def restore_dot_states(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from src.services.battle_dot_stack_state_service import BattleDotStackState
    results = {}
    for row in rows:
        kind = row.get("kind")
        if (
            kind not in _NAMES or type(row.get("coefficient")) is not int
            or not 0 <= row["coefficient"] <= 10
            or type(row.get("active_dot_kind_count")) is not int
            or not 0 <= row["active_dot_kind_count"] <= 4
            or row.get("confidence") not in ("未解析", "中", "低")
            or not all(type(row.get(flag)) is bool for flag in ("lower", "early", "final_enabled", "final_active"))
            or type(row.get("dot_final_multiplier")) not in (int, float)
            or row["dot_final_multiplier"] not in (1.0, 1.25, 1.5, 1.75, 2.0)
            or not isinstance(row.get("effect"), str)
            or not all(isinstance(row.get(field), list) and all(value in _NAMES for value in row[field])
                       for field in ("active_kinds", "recent_only"))
        ):
            raise ValueError("invalid_hit_state_result")
        label = (
            "3각: 「噩梦」 조기 정산" if row["early"] else
            f"{_NAMES[kind]} 관측 하한" if row["lower"] else
            "스코치 정산 전 중첩" if kind == "scorch" else
            f"{_NAMES[kind]} 현재 상태" if kind in ("cang_field", "adler_skill") else
            f"{_NAMES[kind]} 현재 중첩"
        )
        results[row["event_id"]] = BattleDotStackState(
            event_id=row["event_id"], coefficient=row["coefficient"], label=label,
            confidence=row["confidence"], evidence_basis=_dot_basis(row),
            active_dot_kind_count=row["active_dot_kind_count"],
            dot_final_multiplier=row["dot_final_multiplier"], dot_final_multiplier_basis=_dot_final_basis(row),
        )
    return results


def restore_q_final_states(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from src.services.battle_zankou_awakening_state_service import ZankouQFinalDamageEvidence
    results = {}
    for row in rows:
        if row.get("branch") not in ("magic", "force") or row.get("multiplier") != 2.5:
            raise ValueError("invalid_hit_state_result")
        branch = row["branch"]
        name = "血宴入梦时" if branch == "magic" else "焚天烬灭舞"
        results[row["event_id"]] = ZankouQFinalDamageEvidence(
            event_id=row["event_id"], multiplier=row["multiplier"],
            evidence_basis=(
                "2각성 「심연의 입맞춤」 蓄焰(헥스/스코치 발동 후 획득):"
                f"{name} 이번 발동의 독립 최종 피해 +150%, 즉 ×2.5"
                + ("; 4각성 「악몽의 꽃」으로 血宴 분기가 자격을 독립적으로 보유·소비" if branch == "magic" else "")
            ),
        )
    return results
