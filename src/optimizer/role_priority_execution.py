# 编排角色优先分配、同级组结算及同分驱动的延迟归属。
"""Execution shell for the role-priority optimizer.

Keeping this orchestration outside the group-matching mixin keeps each source
file below the repository size limit while making reservation commits explicit.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from src.models.equipment import Drive, Tape
from src.optimizer.deferred_drive_reservations import DeferredDriveReservationState
from src.utils.logger import logger


_RESERVATION_SEARCH_LIMIT = 24


def execute_role_priority(
    strategy: Any,
    candidate_pool: dict,
    priority_list: list[str],
    custom_sets: dict[str, str],
    crit_priority_modes: dict[str, dict] | None = None,
    priority_groups: list[list[str]] | None = None,
    crit_rate_caps: dict[str, float] | None = None,
) -> dict:
    """Allocate priority groups while retaining only exact interchangeable UIDs."""

    logger.info("분배 모드 시작: 캐릭터 우선")
    drives_pool = sorted(list(candidate_pool.get("drives", [])), key=lambda drive: drive.uid)
    full_drives = list(candidate_pool.get("all_drives") or drives_pool)
    tapes_pool = candidate_pool.get("tapes", {})
    crit_priority_modes = crit_priority_modes or {}
    crit_rate_caps = crit_rate_caps or {}
    priority_groups = strategy._normalize_priority_groups(priority_list, priority_groups)
    assigned_tapes: dict[str, Tape | None] = {}
    final_allocation: dict = {}
    used_tape_uids: set[str] = set()
    occupied_drive_uids: set[str] = set()
    reservations = DeferredDriveReservationState()

    for group_index, group in enumerate(priority_groups):
        tape_eligible_group = [role for role in group if strategy.blueprints_db.get(role)]
        available_tapes = {
            role: [
                tape for tape in tapes_pool.get(role, [])
                if tape.uid not in used_tape_uids
            ]
            for role in tape_eligible_group
        }
        current_tapes = strategy._pre_allocate_tapes_for_groups(
            [tape_eligible_group] if tape_eligible_group else [],
            custom_sets,
            available_tapes,
            crit_priority_modes,
        )
        for role in group:
            assigned_tapes[role] = current_tapes.get(role)

        if len(group) > 1:
            group_allocation = _choose_group_allocation(
                strategy,
                group,
                drives_pool,
                full_drives,
                custom_sets,
                assigned_tapes,
                crit_priority_modes,
                crit_rate_caps,
                occupied_drive_uids,
                tapes_pool,
                used_tape_uids,
                reservations,
                int(candidate_pool.get("drive_screen_limit") or 15),
            )
        else:
            role_name = group[0]
            group_allocation = {
                role_name: _choose_single_role_plan(
                    strategy,
                    role_name,
                    drives_pool,
                    assigned_tapes,
                    tapes_pool,
                    used_tape_uids,
                    custom_sets,
                    crit_priority_modes,
                    crit_rate_caps,
                    reservations,
                )
            }

        final_allocation.update(group_allocation)
        used_tape_uids.update(
            tape.uid
            for plan in group_allocation.values()
            for tape in [plan.get("assigned_tape")]
            if plan.get("valid") and isinstance(tape, Tape)
        )
        for role in group:
            if not group_allocation.get(role, {}).get("valid"):
                assigned_tapes[role] = None

        used_uids = strategy._allocated_drive_uids(group_allocation)
        prior_reservation_uids = reservations.reservation_uids
        if not reservations.can_consume(used_uids):
            raise ValueError("캐릭터 우선 청사진이 앞선 동점 드라이브의 채워 넣기 매칭을 소진했습니다")
        reservations.commit(used_uids & prior_reservation_uids)
        registration = strategy._register_deferred_group_slots(
            reservations,
            group_index,
            group_allocation,
            full_drives,
            crit_priority_modes,
            crit_rate_caps,
            occupied_drive_uids,
        )
        occupied_drive_uids.update(registration.fixed_uids)
        drives_pool = _updated_drives_pool(
            drives_pool,
            registration.fixed_uids | reservations.consumed_uids,
            registration.exposed_drives,
        )

    _finalize_deferred_slots(final_allocation, reservations)
    return final_allocation


def _updated_drives_pool(
    current: list[Drive],
    removed_uids: frozenset[str],
    exposed: tuple[Drive, ...],
) -> list[Drive]:
    by_uid = {
        drive.uid: drive for drive in current if drive.uid not in removed_uids
    }
    for drive in exposed:
        if drive.uid not in removed_uids:
            by_uid.setdefault(drive.uid, drive)
    return [by_uid[uid] for uid in sorted(by_uid)]


def _choose_group_allocation(
    strategy: Any,
    group: list[str],
    drives_pool: list[Drive],
    full_drives: list[Drive],
    custom_sets: dict[str, str],
    assigned_tapes: dict[str, Tape | None],
    crit_priority_modes: dict[str, dict],
    crit_rate_caps: dict[str, float],
    occupied_drive_uids: set[str],
    tapes_pool: dict,
    used_tape_uids: set[str],
    reservations: DeferredDriveReservationState,
    candidate_limit: int,
) -> dict:
    """Pick the best reservation-feasible result instead of the first one.

    A tied earlier slot can have several valid return drives.  Excluding only
    one conflicting UID greedily may settle on a lower-scoring later plan even
    though another exclusion leaves the earlier slot matchable.  Search the
    small reservation frontier and keep the best feasible branch.
    """

    started = perf_counter()
    conflict_count = 0
    pending = [frozenset()]
    visited: set[frozenset[str]] = set()
    recovery_count = 0
    best_allocation: dict | None = None
    fallback: dict | None = None
    while pending and len(visited) < _RESERVATION_SEARCH_LIMIT:
        excluded_uids = pending.pop(0)
        if excluded_uids in visited:
            continue
        visited.add(excluded_uids)
        available = [drive for drive in drives_pool if drive.uid not in excluded_uids]
        allocation = strategy._find_best_group_fit(
            group, available, custom_sets, assigned_tapes,
            crit_priority_modes, crit_rate_caps,
        )
        failed_roles = [
            role for role in group if not allocation.get(role, {}).get("valid")
        ]
        if failed_roles:
            recovery_count += 1
            allocation = strategy._recover_equal_priority_group(
                group,
                available,
                custom_sets,
                assigned_tapes,
                crit_priority_modes,
                crit_rate_caps,
                full_drives=[
                    drive for drive in full_drives if drive.uid not in excluded_uids
                ],
                occupied_uids=occupied_drive_uids,
                tapes_pool=tapes_pool,
                used_tape_uids=used_tape_uids,
                candidate_limit=candidate_limit,
            )
        used_uids = strategy._allocated_drive_uids(allocation)
        if reservations.can_consume(used_uids):
            if (
                best_allocation is None
                or _allocation_quality_key(strategy, allocation, group)
                > _allocation_quality_key(strategy, best_allocation, group)
            ):
                best_allocation = allocation
            continue
        fallback = allocation
        conflict_count += 1
        for uid in sorted(used_uids & reservations.reservation_uids):
            next_excluded = excluded_uids | {uid}
            if next_excluded not in visited:
                pending.append(next_excluded)

    logger.info(
        "같은 등급 그룹 보존 탐색 완료: 캐릭터 수={}, 풀이 회차={}, 복구 횟수={}, "
        "충돌 횟수={}, 소요 시간={:.3f}s",
        len(group), len(visited), recovery_count,
        conflict_count, perf_counter() - started,
    )
    if best_allocation is not None:
        return best_allocation
    if (
        fallback is not None
        and not strategy._group_uses_crit_thresholds(group, crit_priority_modes)
        and not reservations.can_consume(strategy._allocated_drive_uids(fallback))
    ):
        # Keep every successful bounded-search result unchanged. Only the former
        # conflicting failure retries with all earlier slots in the same matching.
        recovered = strategy._find_best_group_fit(
            group, drives_pool, custom_sets, assigned_tapes,
            crit_priority_modes, crit_rate_caps,
            reservation_candidates=reservations.remaining_candidate_uids,
        )
        if all(recovered.get(role, {}).get("valid") for role in group) and reservations.can_consume(
            strategy._allocated_drive_uids(recovered)
        ):
            logger.info("같은 등급 그룹 보존 공동 매칭 복구 완료: 캐릭터 수={}", len(group))
            return recovered
    return fallback or {role_name: {"valid": False} for role_name in group}


def _choose_single_role_plan(
    strategy: Any,
    role_name: str,
    drives_pool: list[Drive],
    assigned_tapes: dict[str, Tape | None],
    tapes_pool: dict,
    used_tape_uids: set[str],
    custom_sets: dict[str, str],
    crit_priority_modes: dict[str, dict],
    crit_rate_caps: dict[str, float],
    reservations: DeferredDriveReservationState,
) -> dict:
    started = perf_counter()
    conflict_count = 0
    pending = [frozenset()]
    visited: set[frozenset[str]] = set()
    best_plan: dict | None = None
    fallback: dict | None = None
    while pending and len(visited) < _RESERVATION_SEARCH_LIMIT:
        excluded_uids = pending.pop(0)
        if excluded_uids in visited:
            continue
        visited.add(excluded_uids)
        plan = _best_single_role_plan(
            strategy,
            role_name,
            [drive for drive in drives_pool if drive.uid not in excluded_uids],
            assigned_tapes,
            tapes_pool,
            used_tape_uids,
            custom_sets,
            crit_priority_modes,
            crit_rate_caps,
        )
        if not plan.get("valid"):
            fallback = plan
            continue
        used_uids = strategy._allocated_drive_uids({role_name: plan})
        if reservations.can_consume(used_uids):
            if best_plan is None or _plan_quality_key(plan) > _plan_quality_key(best_plan):
                best_plan = plan
            continue
        fallback = plan
        conflict_count += 1
        for uid in sorted(used_uids & reservations.reservation_uids):
            next_excluded = excluded_uids | {uid}
            if next_excluded not in visited:
                pending.append(next_excluded)

    logger.info(
        "단일 캐릭터 보존 탐색 완료: 풀이 회차={}, 충돌 횟수={}, 소요 시간={:.3f}s",
        len(visited), conflict_count, perf_counter() - started,
    )
    result = best_plan or fallback or {"valid": False}
    result.pop("rank_score", None)
    return result


def _allocation_quality_key(strategy: Any, allocation: dict, group: list[str]) -> tuple:
    """Rank feasible group branches by completed roles then optimizer scores."""

    plans = [allocation.get(role_name, {}) for role_name in group]
    return (
        sum(bool(plan.get("valid")) for plan in plans),
        sum(float(plan.get("rank_score", plan.get("score", -1.0))) for plan in plans),
        sum(float(plan.get("score", -1.0)) for plan in plans),
        tuple(sorted(strategy._allocated_drive_uids(allocation))),
    )


def _plan_quality_key(plan: dict) -> tuple:
    """Rank feasible single-role branches with the optimizer's own order."""

    return (
        tuple(plan.get("stat_priority_key", ()) or ()),
        float(plan.get("rank_score", plan.get("score", -1.0))),
        float(plan.get("score", -1.0)),
    )


def _best_single_role_plan(
    strategy: Any,
    role_name: str,
    drives_pool: list[Drive],
    assigned_tapes: dict[str, Tape | None],
    tapes_pool: dict,
    used_tape_uids: set[str],
    custom_sets: dict[str, str],
    crit_priority_modes: dict[str, dict],
    crit_rate_caps: dict[str, float],
) -> dict:
    blueprints = strategy._dedupe_blueprints_for_role_priority(
        strategy.blueprints_db.get(role_name, []),
    )
    target_set = strategy._target_set(role_name, custom_sets)
    required_shapes = strategy._required_shapes_for_role_blueprints(
        role_name, blueprints, custom_sets,
    )
    role_drives_pool = strategy._filter_drives_by_shapes(drives_pool, required_shapes)
    logger.info(
        "  [{}] 매칭 중... (청사진 수: {}, 후보 풀: {})",
        role_name, len(blueprints), len(role_drives_pool),
    )
    best_plan: dict = {
        "valid": False, "score": -1.0, "rank_score": -1.0,
        "stat_priority_key": (),
    }
    failure_reasons: list[str] = []
    role_crit_config = crit_priority_modes.get(role_name)
    retry_tape_candidates = (
        strategy._crit_rate_cap(role_name, crit_rate_caps) is not None
        or strategy._crit_floor_threshold(role_crit_config) is not None
    )
    tape_candidates = (
        strategy._tape_candidates_for_capped_role(
            role_name, assigned_tapes, tapes_pool, used_tape_uids,
            custom_sets, role_crit_config,
        )
        if retry_tape_candidates else [assigned_tapes.get(role_name)]
    )
    for blueprint in blueprints:
        for role_tape in tape_candidates:
            tape_score = role_tape.role_scores.get(role_name, 0.0) if role_tape else 0.0
            if retry_tape_candidates:
                plan = strategy._find_best_fit(
                    role_name, blueprint, role_drives_pool, target_set,
                    role_crit_config, role_tape, crit_rate_caps,
                )
            else:
                plan = strategy._find_best_fit(
                    role_name, blueprint, role_drives_pool, target_set,
                    role_crit_config,
                )
            if not plan["valid"]:
                reason = str(plan.get("reason") or "").strip()
                if reason:
                    failure_reasons.append(reason)
                continue
            total_score = plan["score"] + tape_score
            total_rank_score = plan.get("rank_score", plan["score"]) + tape_score
            priority_key = tuple(plan.get("stat_priority_key", ()) or ())
            best_priority_key = tuple(best_plan.get("stat_priority_key", ()) or ())
            if (priority_key, total_rank_score, total_score) > (
                best_priority_key,
                best_plan.get("rank_score", best_plan["score"]),
                best_plan["score"],
            ):
                plan["score"] = total_score
                plan["rank_score"] = total_rank_score
                plan["assigned_tape"] = role_tape
                best_plan = plan
    if best_plan["valid"]:
        return best_plan
    assigned_tapes[role_name] = None
    reason = next(
        iter(dict.fromkeys(failure_reasons)),
        "사용 가능한 청사진이 없거나 청사진에 필요한 형태를 맞출 수 없습니다",
    )
    return {"valid": False, "reason": reason}


def _reservation_blocker(
    reservations: DeferredDriveReservationState,
    used_uids: set[str],
) -> str | None:
    candidates = sorted(used_uids & reservations.reservation_uids)
    for uid in candidates:
        if reservations.can_consume(used_uids - {uid}):
            return uid
    return candidates[0] if candidates else None


def _finalize_deferred_slots(
    allocation: dict,
    reservations: DeferredDriveReservationState,
) -> None:
    if not reservations.has_slots:
        return
    for key, drive in reservations.finalize().items():
        slot = reservations.slot(key)
        plan = allocation[slot.role_name]
        field = "assigned_set_drives" if slot.slot_type == "set" else "assigned_extra_drives"
        plan[field][slot.slot_index] = drive
