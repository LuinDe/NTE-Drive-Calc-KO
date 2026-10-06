# 仅用本次冻结图纸、候选和偏好解释可证明的分配失败原因。
"""Conservative Calc-side explanation of native allocation failures.

This does not search for a plan or change any allocation result.  A shape
shortage is reported only when every frozen blueprint has one.
"""

from __future__ import annotations

from collections import Counter

from src.domain.stat_catalog import StatCatalog
from src.models.equipment import Drive
from src.optimizer.allocation_kernel import AllocationKernelRequest


_RESERVATION_REASON = "앞선 동점 드라이브를 일대일로 채워 넣을 수 없습니다"
_GROUP_SHORTAGE_REASON = "같은 등급 그룹의 앞선 예약 채워 넣기를 통과하지 못해, 이 캐릭터 단독 가능 여부는 아직 판정되지 않았습니다."


def _mandatory_shape_counts(blueprints: list[dict]) -> dict[str, int]:
    if not blueprints:
        return {}
    mandatory: Counter[str] | None = None
    for blueprint in blueprints:
        pieces = (
            *(blueprint.get("set_pieces") or ()),
            *(blueprint.get("extra_pieces") or ()),
        )
        counts = Counter(str(shape) for shape in pieces)
        if mandatory is None:
            mandatory = counts
        else:
            mandatory &= counts
    return dict(mandatory or {})


def _stat_name(catalog: StatCatalog, raw: object) -> str:
    name = str(raw or "").strip()
    return catalog.normalize_stat_name(name, is_percent="%" in name) or name


def _shortage_reason(
    role: str, request: AllocationKernelRequest, catalog: StatCatalog,
    drives: list[Drive],
) -> str | None:
    blueprints = request.blueprints_db.get(role, [])
    if not blueprints:
        return None
    config = request.stat_priority_configs.get(role) or {}
    hard_blacklist = bool(config.get("blacklist")) and not config.get("blacklist_zero_weight")
    blacklist = (
        {_stat_name(catalog, stat) for stat in config["blacklist"]}
        if hard_blacklist else set()
    )
    by_shape = Counter(drive.shape_id for drive in drives)
    eligible = Counter(
        drive.shape_id for drive in drives
        if not blacklist or not any(
            _stat_name(catalog, stat) in blacklist for stat in drive.sub_stats
        )
    )

    def shortage(counts: Counter[str]):
        for shape, needed in sorted(counts.items()):
            if eligible[shape] < needed:
                return shape, needed, by_shape[shape], eligible[shape]
        return None

    mandatory = _mandatory_shape_counts(blueprints)
    common = shortage(Counter(mandatory))
    if common is not None:
        shape, needed, raw_count, eligible_count = common
    else:
        per_blueprint = [
            shortage(Counter(str(shape) for shape in (
                *(blueprint.get("set_pieces") or ()),
                *(blueprint.get("extra_pieces") or ()),
            )))
            for blueprint in blueprints
        ]
        if any(item is None for item in per_blueprint):
            return None
        shape, needed, raw_count, eligible_count = per_blueprint[0]
        cause = (
            "서브 스탯 블랙리스트 필터 후 적격"
            if hard_blacklist and raw_count >= needed else "후보"
        )
        return (
            f"청사진 {len(blueprints)}장 모두 드라이브 형태가 부족합니다;"
            f"예: {shape} {needed}개 필요, {cause} {eligible_count}개."
        )
    if hard_blacklist and raw_count >= needed:
        return (
            f"필수 {shape} 드라이브가 서브 스탯 블랙리스트 필터 후 부족합니다:"
            f"최소 {needed}개 필요, 적격 {eligible_count}개;"
            "블랙리스트를 조정하거나 적격 드라이브를 보충하세요."
        )
    return (
        f"이번 후보에 필수 {shape} 드라이브가 부족합니다:"
        f"최소 {needed}개 필요, 후보 {raw_count}개;"
        "필터를 확인하거나 해당 형태를 보충하세요."
    )


def explain_allocation_failures(
    plans: dict[str, dict], request: AllocationKernelRequest, catalog: StatCatalog,
) -> dict[str, dict]:
    """Replace only misleading failed reasons backed by a frozen-input proof."""
    drives = [item for item in request.inventory if isinstance(item, Drive)]
    proven = {
        role: reason
        for role, plan in plans.items()
        if isinstance(plan, dict) and plan.get("valid") is False
        and (
            str(plan.get("reason") or "").strip() == _RESERVATION_REASON
            or plan.get("search_status") in {
                "bounded_unproven", "budget_exhausted", "candidate_truncated",
            }
        )
        if (reason := _shortage_reason(role, request, catalog, drives)) is not None
    }
    if not proven:
        return plans

    explained = dict(plans)
    for role, reason in proven.items():
        explained[role] = {
            **plans[role], "reason": reason, "search_status": "constraint_proven",
        }
    for group in request.priority_groups:
        if not any(role in proven for role in group):
            continue
        for role in group:
            plan = explained.get(role)
            if (isinstance(plan, dict) and plan.get("valid") is False
                    and str(plan.get("reason") or "").strip() == _RESERVATION_REASON):
                explained[role] = {**plan, "reason": _GROUP_SHORTAGE_REASON}
    return explained
