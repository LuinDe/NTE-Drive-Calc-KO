# 联合匹配当前配装槽位和前序同分预留槽位，保留完整一对一回填。
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment


def match_reserved_group_slots(
    ranking_matrix: np.ndarray,
    profit_matrix: np.ndarray,
    drive_uids: Sequence[str],
    reservation_candidates: tuple[tuple[str, ...], ...],
) -> tuple[np.ndarray, np.ndarray] | None:
    """Maximize current ranking while filling every earlier reservation row.

    Current columns retain their original order and eligibility. Additional
    reservation-only UIDs have no legal edge to a current row. Earlier rows
    have zero cost and no dummy columns, so a complete match witnesses a valid
    one-to-one return assignment without fixing any earlier choice greedily.
    Only current row/column indices are returned; input matrices are untouched.
    """

    ranking = np.asarray(ranking_matrix, dtype=float)
    profit = np.asarray(profit_matrix, dtype=float)
    if ranking.ndim != 2 or profit.shape != ranking.shape:
        raise ValueError("공동 예약 매칭의 점수 행렬 형태가 일치하지 않습니다")
    current_rows, current_columns = ranking.shape
    uids = tuple(drive_uids)
    if current_columns != len(uids) or len(set(uids)) != len(uids):
        raise ValueError("공동 예약 매칭의 후보 열은 유일한 드라이브에 대응해야 합니다")
    legal = profit >= 0
    if not np.isfinite(ranking[legal]).all():
        raise ValueError("공동 예약 매칭의 유효 후보 점수는 유한한 값이어야 합니다")

    current_uid_set = set(uids)
    extra_uids = sorted({
        uid for candidates in reservation_candidates for uid in candidates
        if uid not in current_uid_set
    })
    all_uids = (*uids, *extra_uids)
    total_rows = current_rows + len(reservation_candidates)
    if total_rows > len(all_uids) or any(not candidates for candidates in reservation_candidates):
        return None
    uid_columns = {uid: column for column, uid in enumerate(all_uids)}
    costs = np.full((total_rows, len(all_uids)), np.inf)
    costs[:current_rows, :current_columns] = np.where(legal, -ranking, np.inf)
    for offset, candidates in enumerate(reservation_candidates):
        for uid in candidates:
            costs[current_rows + offset, uid_columns[uid]] = 0.0

    try:
        rows, columns = linear_sum_assignment(costs)
    except ValueError:
        # Costs contain only finite values and positive infinity; here SciPy's
        # failure means the required rows have no complete legal matching.
        return None
    if len(rows) != total_rows or not np.isfinite(costs[rows, columns]).all():
        return None
    selected = rows < current_rows
    return rows[selected], columns[selected]
