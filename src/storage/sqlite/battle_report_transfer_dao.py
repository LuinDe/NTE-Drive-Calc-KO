# 精确导出战报行图并事务式导入战报包。

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .protocols import UserDataDaoMixinHost
from .user_data_support import (
    BATTLE_REPORT_MAX_MANUAL_RECORDS,
    BATTLE_REPORT_MAX_RECORDS,
    UserDataError,
    UserDataValidationError,
)


_RECORD_TABLES = (
    "battle_record",
    "battle_record_retention",
    "battle_report_import_origin",
    "battle_axis_capture",
    "battle_hit_evidence",
    "battle_time_stop_interval",
    "battle_build_snapshot",
    "battle_character_build_snapshot",
    "battle_character_import_equipment_lock",
    "battle_character_skill_snapshot",
    "battle_equipment_snapshot",
    "battle_equipment_stat_snapshot",
    "battle_character_stat_snapshot",
    "battle_build_edit",
    "battle_character_build_edit",
    "battle_character_skill_edit",
    "battle_character_awaken_edit",
    "battle_target_condition",
)

_INSERT_ORDER = _RECORD_TABLES


class BattleReportTransferDaoMixin(UserDataDaoMixinHost):
    """Own the SQLite-specific portable row graph for one battle record."""

    def load_battle_report_transfer_rows(
        self,
        battle_record_id: int,
    ) -> dict[str, Any] | None:
        if isinstance(battle_record_id, bool) or not isinstance(battle_record_id, int):
            raise UserDataValidationError("battle_record_id는 정수여야 합니다")
        if battle_record_id < 1:
            raise UserDataValidationError("battle_record_id는 1보다 작을 수 없습니다")
        connection = self._db()
        record = connection.execute(
            "SELECT * FROM battle_record WHERE battle_record_id = ?",
            (battle_record_id,),
        ).fetchone()
        if record is None:
            return None
        capture = connection.execute(
            "SELECT capture_id FROM battle_axis_capture WHERE battle_record_id = ?",
            (battle_record_id,),
        ).fetchone()
        capture_id = None if capture is None else int(capture["capture_id"])
        tables: dict[str, list[dict[str, Any]]] = {}
        for table in _RECORD_TABLES:
            if table in {"battle_hit_evidence", "battle_time_stop_interval"}:
                rows = [] if capture_id is None else connection.execute(
                    f"SELECT * FROM {table} WHERE capture_id = ? "
                    + self._portable_order_by(table),
                    (capture_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE battle_record_id = ? "
                    + self._portable_order_by(table),
                    (battle_record_id,),
                ).fetchall()
            tables[table] = [dict(row) for row in rows]
        state = connection.execute(
            """
            SELECT last_detail_scope, analysis_start_us, analysis_end_us,
                   analysis_character_id, updated_at_utc
            FROM battle_report_page_state
            WHERE singleton_id = 1 AND last_battle_record_id = ?
            """,
            (battle_record_id,),
        ).fetchone()
        return {
            "source_battle_record_id": battle_record_id,
            "tables": tables,
            "saved_page_state": None if state is None else dict(state),
        }

    def battle_report_transfer_statuses(self) -> dict[int, dict[str, Any]]:
        rows = self._db().execute(
            """
            SELECT battle_record_id, axis_complete, first_available_cursor,
                   next_cursor, first_sequence, total_hits, retained_hits,
                   stored_hits
            FROM battle_axis_capture
            WHERE capture_state = 'finalized' AND battle_record_id IS NOT NULL
            ORDER BY battle_record_id
            """
        ).fetchall()
        return {int(row["battle_record_id"]): dict(row) for row in rows}

    def import_battle_report_transfer_rows(
        self,
        reports: Sequence[Mapping[str, Any]],
        *,
        before_commit=None,
    ) -> dict[str, Any]:
        if not reports:
            raise UserDataValidationError("전투 리포트 번들에 가져올 수 있는 기록이 없습니다")
        normalized = [self._normalize_transfer_report(item) for item in reports]
        operation_ids = [item["capture_operation_id"] for item in normalized]
        if len(set(operation_ids)) != len(operation_ids):
            raise UserDataValidationError("전투 리포트 패키지에 중복 capture_operation_id가 있습니다")

        connection = self._db()
        imported_ids: list[int] = []
        skipped = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            pending: list[dict[str, Any]] = []
            for item in normalized:
                existing = connection.execute(
                    """
                    SELECT battle_record_id, raw_summary_sha256
                    FROM battle_record WHERE capture_operation_id = ?
                    """,
                    (item["capture_operation_id"],),
                ).fetchone()
                if existing is None:
                    pending.append(item)
                    continue
                if str(existing["raw_summary_sha256"]) != item["raw_summary_sha256"]:
                    raise UserDataValidationError(
                        "로컬에 같은 capture_operation_id의 다른 전투 리포트가 이미 있어 덮어쓰기를 거부했습니다"
                    )
                skipped += 1
            self._validate_retention_capacity(connection, pending)
            for item in pending:
                imported_ids.append(self._insert_transfer_report(connection, item))
            if before_commit is not None:
                before_commit()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return {
            "imported_battle_record_ids": tuple(imported_ids),
            "skipped_existing_count": skipped,
        }

    @staticmethod
    def _portable_order_by(table: str) -> str:
        return {
            "battle_hit_evidence": "ORDER BY sequence_order",
            "battle_time_stop_interval": "ORDER BY ordinal",
            "battle_character_build_snapshot": "ORDER BY ordinal, character_id",
            "battle_character_import_equipment_lock": "ORDER BY character_id",
            "battle_character_skill_snapshot": "ORDER BY character_id, skill_id",
            "battle_equipment_snapshot": "ORDER BY character_id, kind, uid_slot, uid_serial",
            "battle_equipment_stat_snapshot": "ORDER BY uid_slot, uid_serial, stat_group, ordinal",
            "battle_character_stat_snapshot": "ORDER BY character_id, source_group, ordinal, property_id",
            "battle_character_build_edit": "ORDER BY ordinal, character_id",
            "battle_character_skill_edit": "ORDER BY character_id, skill_id",
            "battle_character_awaken_edit": "ORDER BY character_id, ordinal, effect_id",
        }.get(table, "")

    @classmethod
    def _normalize_transfer_report(cls, value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise UserDataValidationError("전투 리포트 패키지 기록은 객체여야 합니다")
        raw_tables = value.get("tables")
        if not isinstance(raw_tables, Mapping):
            raise UserDataValidationError("전투 리포트 패키지 기록에 데이터베이스 행이 없습니다")
        unknown = set(raw_tables) - set(_RECORD_TABLES)
        if unknown:
            raise UserDataValidationError("전투 리포트 패키지에 지원하지 않는 데이터 테이블이 있습니다")
        tables: dict[str, list[dict[str, Any]]] = {}
        for table in _RECORD_TABLES:
            raw_rows = raw_tables.get(table, [])
            if isinstance(raw_rows, (str, bytes)) or not isinstance(raw_rows, Sequence):
                raise UserDataValidationError(f"{table}은(는) 배열이어야 합니다")
            rows = []
            for raw_row in raw_rows:
                if not isinstance(raw_row, Mapping):
                    raise UserDataValidationError(f"{table}에 잘못된 데이터 행이 있습니다")
                rows.append(dict(raw_row))
            tables[table] = rows
        if len(tables["battle_record"]) != 1:
            raise UserDataValidationError("내보내는 전투 리포트마다 battle_record가 하나 있어야 합니다")
        if len(tables["battle_record_retention"]) != 1:
            raise UserDataValidationError("내보내는 전투 리포트마다 보존 상태가 하나 있어야 합니다")
        record = tables["battle_record"][0]
        operation_id = str(record.get("capture_operation_id") or "").strip()
        raw_summary = record.get("raw_summary_json")
        raw_sha256 = str(record.get("raw_summary_sha256") or "").strip().lower()
        if not operation_id or not isinstance(raw_summary, str):
            raise UserDataValidationError("전투 리포트 패키지에 원본 요약 식별 정보가 없습니다")
        try:
            summary_payload = json.loads(raw_summary)
        except json.JSONDecodeError as error:
            raise UserDataValidationError("전투 리포트 패키지의 원본 요약 JSON이 잘못되었습니다") from error
        if not isinstance(summary_payload, dict):
            raise UserDataValidationError("전투 리포트 패키지의 원본 요약은 객체여야 합니다")
        expected = hashlib.sha256(raw_summary.encode("utf-8")).hexdigest()
        if raw_sha256 != expected:
            raise UserDataValidationError("전투 리포트 패키지의 원본 요약 SHA-256이 일치하지 않습니다")
        cls._validate_record_graph(tables)
        return {
            "capture_operation_id": operation_id,
            "raw_summary_sha256": raw_sha256,
            "tables": tables,
        }

    @staticmethod
    def _validate_record_graph(tables: Mapping[str, list[dict[str, Any]]]) -> None:
        record_id = tables["battle_record"][0].get("battle_record_id")
        if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 1:
            raise UserDataValidationError("출처 battle_record_id가 잘못되었습니다")
        for table, rows in tables.items():
            if table in {"battle_hit_evidence", "battle_time_stop_interval"}:
                continue
            for row in rows:
                if row.get("battle_record_id") != record_id:
                    raise UserDataValidationError(f"{table}의 전투 리포트 외래 키가 일치하지 않습니다")
        captures = tables["battle_axis_capture"]
        if len(captures) > 1:
            raise UserDataValidationError("전투 리포트 한 건에는 axis capture가 여러 개 있을 수 없습니다")
        if not captures:
            if tables["battle_hit_evidence"] or tables["battle_time_stop_interval"]:
                raise UserDataValidationError("히트별 또는 시간 정지에 axis capture가 없습니다")
            return
        capture_id = captures[0].get("capture_id")
        if isinstance(capture_id, bool) or not isinstance(capture_id, int):
            raise UserDataValidationError("출처 capture_id가 잘못되었습니다")
        if (
            str(captures[0].get("capture_operation_id") or "")
            != str(tables["battle_record"][0].get("capture_operation_id") or "")
            or captures[0].get("capture_state") != "finalized"
        ):
            raise UserDataValidationError("히트별 축이 전투 리포트 식별 정보 또는 완료 상태와 일치하지 않습니다")
        for table in ("battle_hit_evidence", "battle_time_stop_interval"):
            if any(row.get("capture_id") != capture_id for row in tables[table]):
                raise UserDataValidationError(f"{table}의 capture 외래 키가 일치하지 않습니다")
        raw_record = captures[0].get("raw_record_json")
        raw_record_sha256 = captures[0].get("raw_record_sha256")
        if raw_record not in (None, ""):
            if not isinstance(raw_record, str):
                raise UserDataValidationError("nte-core record 원본 JSON이 잘못되었습니다")
            try:
                decoded_record = json.loads(raw_record)
            except json.JSONDecodeError as error:
                raise UserDataValidationError("nte-core record 원본 JSON이 잘못되었습니다") from error
            if not isinstance(decoded_record, dict):
                raise UserDataValidationError("nte-core record 원본 JSON은 객체여야 합니다")
            expected_record_sha = hashlib.sha256(raw_record.encode("utf-8")).hexdigest()
            if str(raw_record_sha256 or "").lower() != expected_record_sha:
                raise UserDataValidationError("nte-core record SHA-256이 일치하지 않습니다")
        for row in tables["battle_hit_evidence"]:
            cls_payload = row.get("raw_hit_json")
            try:
                decoded_hit = json.loads(str(cls_payload))
            except json.JSONDecodeError as error:
                raise UserDataValidationError("히트별 원본 JSON이 잘못되었습니다") from error
            if not isinstance(decoded_hit, dict):
                raise UserDataValidationError("히트별 원본 JSON은 객체여야 합니다")
        for row in tables["battle_time_stop_interval"]:
            try:
                decoded_interval = json.loads(str(row.get("raw_interval_json")))
            except json.JSONDecodeError as error:
                raise UserDataValidationError("시간 정지 원본 JSON이 잘못되었습니다") from error
            if not isinstance(decoded_interval, dict):
                raise UserDataValidationError("시간 정지 원본 JSON은 객체여야 합니다")

    @staticmethod
    def _validate_retention_capacity(
        connection: sqlite3.Connection,
        pending: Sequence[Mapping[str, Any]],
    ) -> None:
        current_total = int(connection.execute(
            "SELECT COUNT(*) FROM battle_record"
        ).fetchone()[0])
        current_manual = int(connection.execute(
            "SELECT COUNT(*) FROM battle_record_retention WHERE retention_kind = 'manual'"
        ).fetchone()[0])
        pending_manual = sum(
            str(item["tables"]["battle_record_retention"][0].get("retention_kind"))
            == "manual"
            for item in pending
        )
        if current_total + len(pending) > BATTLE_REPORT_MAX_RECORDS:
            raise UserDataValidationError(
                f"가져오면 전투 리포트가 {BATTLE_REPORT_MAX_RECORDS}건을 넘게 됩니다. 먼저 필요 없는 기록을 삭제하세요"
            )
        if current_manual + pending_manual > BATTLE_REPORT_MAX_MANUAL_RECORDS:
            raise UserDataValidationError(
                f"가져오면 수동 저장 전투 리포트가 {BATTLE_REPORT_MAX_MANUAL_RECORDS}건을 넘게 됩니다. 먼저 일부 저장을 취소하세요"
            )

    def _insert_transfer_report(
        self,
        connection: sqlite3.Connection,
        item: Mapping[str, Any],
    ) -> int:
        tables = item["tables"]
        source_record_id = int(tables["battle_record"][0]["battle_record_id"])
        record_row = dict(tables["battle_record"][0])
        record_row.pop("battle_record_id", None)
        record_id = self._insert_row(connection, "battle_record", record_row)
        if record_id is None:
            raise UserDataError("전투 리포트를 가져온 후 battle_record_id가 반환되지 않았습니다")

        capture_id_map: dict[int, int] = {}
        for table in _INSERT_ORDER[1:]:
            for source in tables[table]:
                row = dict(source)
                if "battle_record_id" in row:
                    if int(row["battle_record_id"]) != source_record_id:
                        raise UserDataValidationError(f"{table}의 전투 리포트 외래 키가 일치하지 않습니다")
                    row["battle_record_id"] = int(record_id)
                if table == "battle_axis_capture":
                    source_capture_id = int(row.pop("capture_id"))
                    row["source_inventory_snapshot_id"] = None
                    new_capture_id = self._insert_row(connection, table, row)
                    if new_capture_id is None:
                        raise UserDataError("히트별 축을 가져온 후 capture_id가 반환되지 않았습니다")
                    capture_id_map[source_capture_id] = int(new_capture_id)
                    continue
                if table in {"battle_hit_evidence", "battle_time_stop_interval"}:
                    source_capture_id = int(row["capture_id"])
                    if source_capture_id not in capture_id_map:
                        raise UserDataValidationError(f"{table}에 출처 capture가 없습니다")
                    row["capture_id"] = capture_id_map[source_capture_id]
                if table == "battle_build_snapshot":
                    row["source_inventory_snapshot_id"] = None
                self._insert_row(connection, table, row)
        return int(record_id)

    @staticmethod
    def _insert_row(
        connection: sqlite3.Connection,
        table: str,
        row: Mapping[str, Any],
    ) -> int | None:
        if table not in _RECORD_TABLES:
            raise UserDataValidationError("지원하지 않는 전투 리포트 데이터 테이블")
        local_columns = {
            str(column[1])
            for column in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        unknown = set(row) - local_columns
        if unknown:
            raise UserDataValidationError(f"{table}에 현재 버전에서 지원하지 않는 필드가 있습니다")
        if not row:
            raise UserDataValidationError(f"{table} 데이터 행은 비워 둘 수 없습니다")
        columns = tuple(row)
        placeholders = ",".join("?" for _ in columns)
        names = ",".join(f'"{name}"' for name in columns)
        cursor = connection.execute(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
            tuple(row[name] for name in columns),
        )
        return None if cursor.lastrowid is None else int(cursor.lastrowid)
