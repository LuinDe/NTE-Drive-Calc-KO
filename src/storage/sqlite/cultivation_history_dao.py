# 按账号原子保存养成历史、冻结全选集合并执行修订号保护的批量删除。
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from functools import lru_cache
from typing import Any

from src.domain.cultivation_history import (
    PAYLOAD_VERSION, HistoryConflict, HistoryPage, HistoryPayload, HistoryRecord,
    HistorySelection, HistorySummary, integer, text_value,
    MAX_PAYLOAD_BYTES, MAX_COLLECTION_SIZE, configuration_character_labels, configuration_search_names,
)
from src.domain.name_search import match_pinyin
from src.storage.sqlite.protocols import UserDataDaoMixinHost
from src.storage.sqlite.user_data_support import UserDataError, UserDataValidationError, _utc_now

_SUMMARY_COLUMNS = (
    "history_id, mode, revision, first_calculated_at_utc, last_calculated_at_utc, "
    f"CASE WHEN length(CAST(summary_json AS BLOB)) <= {MAX_PAYLOAD_BYTES} "
    "THEN summary_json ELSE '{}' END AS summary_json"
)


def _predicate(mode: str | None, search: str) -> tuple[str, tuple[str, ...]]:
    clauses: list[str] = []
    parameters: list[str] = []
    if mode is not None:
        if mode not in {"single", "batch"}:
            raise UserDataValidationError("육성 기록 필터 모드가 유효하지 않습니다")
        clauses.append("mode = ?")
        parameters.append(mode)
    if not isinstance(search, str) or len(search) > 256:
        raise UserDataValidationError("육성 기록 검색어가 유효하지 않습니다")
    if search.strip():
        clauses.append(
            "nte_history_name_match("
            f"CASE WHEN length(CAST(configuration_json AS BLOB)) <= {MAX_PAYLOAD_BYTES} "
            "THEN configuration_json ELSE NULL END, "
            f"CASE WHEN length(CAST(search_text AS BLOB)) <= {MAX_PAYLOAD_BYTES} "
            "THEN search_text ELSE NULL END, ?) = 1"
        )
        parameters.append(search.strip())
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), tuple(parameters)


def _register_search(connection: sqlite3.Connection, check: Callable[[], None]) -> None:
    # Cache only name matches for this connection/request, never account payloads globally.
    @lru_cache(maxsize=512)
    def matches_name(name: str, query: str) -> bool:
        return match_pinyin(name, query)

    def matches(configuration: str | None, legacy: str | None, query: str) -> int:
        check()
        try:
            names = configuration_search_names(configuration or "")
        except ValueError:
            # A damaged record stays searchable/deletable by its bounded legacy names.
            names = tuple(legacy.splitlines()) if isinstance(legacy, str) else ()
            if len(names) > MAX_COLLECTION_SIZE:
                return 0
        return int(any(matches_name(name, query) for name in names if 0 < len(name) <= 512))

    connection.create_function("nte_history_name_match", 3, matches)


def _summary(row: Any) -> HistorySummary:
    labels: tuple[str, ...] = ()
    if "display_configuration_json" in row.keys() and row["display_configuration_json"] is not None:
        try:
            labels = configuration_character_labels(row["display_configuration_json"])
        except ValueError:
            pass  # Keep the row selectable even when its configuration is damaged.
    return HistorySummary(character_labels=labels, **{key: row[key] for key in (
        "history_id", "mode", "revision", "first_calculated_at_utc",
        "last_calculated_at_utc", "summary_json",
    )})


class CultivationHistoryDaoMixin(UserDataDaoMixinHost):
    """SQL 与账号一致性的唯一持久化入口；更新缺失行绝不补插。"""

    def _history_account(self, account_id: str, check: Callable[[], None]) -> None:
        check()
        if self.profile()["account_id"] != account_id:
            raise UserDataValidationError("육성 기록이 현재 계정과 일치하지 않습니다")

    def save_cultivation_history(
        self, account_id: str, history_id: str, payload: HistoryPayload, *,
        expected_revision: int | None = None, check: Callable[[], None] = lambda: None,
    ) -> HistorySummary:
        self._history_account(account_id, check)
        text_value(history_id, maximum=128)
        if expected_revision is not None:
            integer(expected_revision, 1)
        # A caller can construct the frozen DTO directly: validate its contents here too.
        verified = HistoryPayload.decode(payload.configuration_json, payload.result_snapshot_json)
        if verified != payload:
            raise UserDataValidationError("육성 기록 요약 또는 지문이 일치하지 않습니다")
        configuration = json.loads(payload.configuration_json)
        search_text = "\n".join(target["name"] for target in configuration["targets"])
        connection = self._db()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                check()
                existing = connection.execute(
                    "SELECT * FROM cultivation_history WHERE history_id = ?", (history_id,),
                ).fetchone()
                if expected_revision is None:
                    if existing is not None:
                        raise HistoryConflict("육성 기록 식별 정보가 이미 있습니다. 저장 바인딩을 새로 고치세요")
                    now = _utc_now()
                    connection.execute(
                        "INSERT INTO cultivation_history "
                        "(history_id, mode, revision, first_calculated_at_utc, last_calculated_at_utc, "
                        "payload_version, configuration_json, result_snapshot_json, summary_json, search_text, content_sha256) "
                        "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (history_id, payload.mode, now, now, PAYLOAD_VERSION, payload.configuration_json,
                         payload.result_snapshot_json, payload.summary_json, search_text, payload.content_sha256),
                    )
                else:
                    if existing is None or existing["revision"] != expected_revision:
                        raise HistoryConflict("육성 기록이 업데이트되었거나 삭제되었습니다. 새로 고친 후 다시 시도하세요")
                    if existing["mode"] != payload.mode:
                        raise UserDataValidationError("육성 기록 모드는 초안 간에 바꿔 업데이트할 수 없습니다")
                    if existing["content_sha256"] != payload.content_sha256:
                        cursor = connection.execute(
                            "UPDATE cultivation_history SET revision = revision + 1, last_calculated_at_utc = ?, "
                            "payload_version = ?, configuration_json = ?, result_snapshot_json = ?, "
                            "summary_json = ?, search_text = ?, content_sha256 = ? "
                            "WHERE history_id = ? AND revision = ?",
                            (_utc_now(), PAYLOAD_VERSION, payload.configuration_json, payload.result_snapshot_json,
                             payload.summary_json, search_text, payload.content_sha256, history_id, expected_revision),
                        )
                        if cursor.rowcount != 1:
                            raise HistoryConflict("육성 기록 저장 바인딩이 더 이상 유효하지 않습니다")
                saved = connection.execute(
                    f"SELECT {_SUMMARY_COLUMNS} FROM cultivation_history WHERE history_id = ?", (history_id,),
                ).fetchone()
                check()
                return _summary(saved)
        except sqlite3.Error as error:
            raise UserDataError("육성 기록 저장에 실패했습니다. 기존 기록은 변경되지 않았습니다") from error

    def list_cultivation_histories(
        self, account_id: str, *, mode: str | None = None, search: str = "",
        page: int = 1, page_size: int = 20, check: Callable[[], None] = lambda: None,
    ) -> HistoryPage:
        self._history_account(account_id, check)
        integer(page, 1, 2**31 - 1)
        integer(page_size, 1, 100)
        where, parameters = _predicate(mode, search)
        connection = self._db()
        _register_search(connection, check)
        try:
            with connection:
                connection.execute("BEGIN")
                total = connection.execute(
                    f"SELECT COUNT(*) FROM cultivation_history{where}", parameters,
                ).fetchone()[0]
                items = connection.execute(
                    f"SELECT {_SUMMARY_COLUMNS}, CASE WHEN length(CAST(configuration_json AS BLOB)) "
                    f"<= {MAX_PAYLOAD_BYTES} THEN configuration_json ELSE NULL END AS display_configuration_json "
                    f"FROM cultivation_history{where} "
                    "ORDER BY last_calculated_at_utc DESC, history_id DESC LIMIT ? OFFSET ?",
                    (*parameters, page_size, (page - 1) * page_size),
                ).fetchall()
                check()
                return HistoryPage(tuple(_summary(row) for row in items), total, page, page_size)
        except sqlite3.Error as error:
            raise UserDataError("육성 기록 목록 읽기 실패") from error

    def get_cultivation_history(
        self, account_id: str, history_id: str, *, check: Callable[[], None] = lambda: None,
    ) -> HistoryRecord | None:
        self._history_account(account_id, check)
        text_value(history_id, maximum=128)
        try:
            row = self._one("SELECT * FROM cultivation_history WHERE history_id = ?", (history_id,))
            check()
            if row is None:
                return None
            if row["payload_version"] != PAYLOAD_VERSION:
                raise ValueError("아직 지원하지 않는 육성 기록 형식 버전입니다")
            payload = HistoryPayload.decode(row["configuration_json"], row["result_snapshot_json"])
            if (payload.content_sha256 != row["content_sha256"] or payload.mode != row["mode"]
                    or payload.summary_json != row["summary_json"]):
                raise ValueError("육성 기록 내용 검증 실패")
            return HistoryRecord(_summary(row), payload)
        except sqlite3.Error as error:
            raise UserDataError("육성 기록 상세 읽기 실패") from error

    def select_cultivation_histories(
        self, account_id: str, *, mode: str | None = None, search: str = "",
        check: Callable[[], None] = lambda: None,
    ) -> tuple[HistorySelection, ...]:
        self._history_account(account_id, check)
        where, parameters = _predicate(mode, search)
        _register_search(self._db(), check)
        try:
            selected = self._rows(
                f"SELECT history_id, revision FROM cultivation_history{where} "
                "ORDER BY last_calculated_at_utc DESC, history_id DESC", parameters,
            )
            check()
            return tuple(HistorySelection(row["history_id"], row["revision"]) for row in selected)
        except sqlite3.Error as error:
            raise UserDataError("육성 기록 선택 집합 읽기 실패") from error

    def delete_cultivation_histories(
        self, account_id: str, selected: Sequence[HistorySelection], *,
        check: Callable[[], None] = lambda: None,
    ) -> int:
        self._history_account(account_id, check)
        selection = tuple(selected)
        seen: set[str] = set()
        for item in selection:
            text_value(item.history_id, maximum=128)
            integer(item.revision, 1)
            if item.history_id in seen:
                raise UserDataValidationError("육성 기록 삭제 집합에 중복된 식별 정보가 있습니다")
            seen.add(item.history_id)
        if not selection:
            return 0
        connection = self._db()
        deleted = 0
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                check()
                # Under the single write transaction no row can change between validation and delete.
                for offset in range(0, len(selection), 400):
                    chunk = selection[offset:offset + 400]
                    ids = tuple(item.history_id for item in chunk)
                    placeholders = ",".join("?" for _ in ids)
                    current = dict(connection.execute(
                        f"SELECT history_id, revision FROM cultivation_history WHERE history_id IN ({placeholders})",
                        ids,
                    ).fetchall())
                    if any(item.history_id in current and current[item.history_id] != item.revision for item in chunk):
                        raise HistoryConflict("선택한 기록이 업데이트되어 이번 삭제는 반영되지 않았습니다. 새로 고친 후 다시 확인하세요")
                    check()
                    cursor = connection.execute(
                        f"DELETE FROM cultivation_history WHERE history_id IN ({placeholders})", ids,
                    )
                    deleted += cursor.rowcount
                check()
            return deleted
        except sqlite3.Error as error:
            raise UserDataError("육성 기록 삭제에 실패해 이번 삭제를 롤백했습니다") from error
