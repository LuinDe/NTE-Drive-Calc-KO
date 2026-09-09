# 管理装备装配任务记录的 SQLite 访问方法。
from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .user_data_support import (
    UserDataError,
    UserDataValidationError,
    _decoded,
    _integer,
    _json,
    _plain_object,
    _utc_now,
)
from .protocols import UserDataDaoMixinHost

class EquipmentApplyJobDaoMixin(UserDataDaoMixinHost):
    @staticmethod
    def _character_uid(value: Mapping[str, Any], label: str = "character_uid") -> dict[str, int]:
        return {
            "slot": _integer(value.get("slot"), f"{label}.slot", minimum=1),
            "serial": _integer(value.get("serial"), f"{label}.serial", minimum=1),
        }

    def upsert_character_instance_mapping(
        self, character_id: int, character_uid: Mapping[str, Any], *, source: str = "manual"
    ) -> dict[str, Any]:
        if source not in {"snapshot", "manual"}:
            raise UserDataValidationError("캐릭터 인스턴스 매핑 source는 snapshot 또는 manual이어야 합니다")
        raw_character_id = _integer(character_id, "character_id", minimum=1)
        uid = self._character_uid(character_uid)
        now = _utc_now()
        try:
            self._db().execute(
                """
                INSERT INTO character_instance_mapping(
                    character_id, uid_slot, uid_serial, source,
                    first_seen_snapshot_id, last_seen_snapshot_id, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
                ON CONFLICT(character_id, uid_slot, uid_serial) DO UPDATE SET
                    source = excluded.source, updated_at_utc = excluded.updated_at_utc
                """,
                (raw_character_id, uid["slot"], uid["serial"], source, now, now),
            )
            self._db().commit()
        except sqlite3.Error as exc:
            self._db().rollback()
            raise UserDataError("캐릭터 인스턴스 매핑을 저장할 수 없습니다") from exc
        return self.list_character_instance_mappings(raw_character_id)[0]

    def list_character_instance_mappings(self, character_id: int | None = None) -> list[dict[str, Any]]:
        where = "" if character_id is None else "WHERE character_id = ?"
        parameters = () if character_id is None else (_integer(character_id, "character_id", minimum=1),)
        return self._rows(
            f"""SELECT character_id, uid_slot, uid_serial, source, first_seen_snapshot_id,
                       last_seen_snapshot_id, created_at_utc, updated_at_utc
                FROM character_instance_mapping {where}
                ORDER BY character_id, updated_at_utc DESC, uid_slot, uid_serial""",
            parameters,
        )

    def list_observed_character_ids(self) -> list[int]:
        """Return actual character IDs ordered by their latest account evidence."""

        rows = self._rows(
            """
            SELECT equipped_character_id AS character_id,
                   MAX(snapshot_id) AS last_seen_snapshot_id
            FROM inventory_item
            WHERE equipped = 1 AND equipped_character_id IS NOT NULL
            GROUP BY equipped_character_id
            ORDER BY last_seen_snapshot_id DESC, character_id
            """
        )
        result = [int(row["character_id"]) for row in rows]
        for row in self._rows(
            """
            SELECT character_id, MAX(updated_at_utc) AS last_seen_at
            FROM character_instance_mapping
            GROUP BY character_id
            ORDER BY last_seen_at DESC, character_id
            """
        ):
            character_id = int(row["character_id"])
            if character_id not in result:
                result.append(character_id)
        return result

    def create_equipment_apply_job(
        self, source_snapshot_id: int, prepared_roles: Sequence[Mapping[str, Any]]
    ) -> int:
        raw_snapshot_id = _integer(source_snapshot_id, "source_snapshot_id", minimum=1)
        if self.inventory_snapshot_summary(raw_snapshot_id) is None:
            raise UserDataValidationError("장착 작업이 참조하는 안정 가방 스냅샷이 없습니다")
        if not prepared_roles:
            raise UserDataValidationError("장착 작업에는 캐릭터가 최소 한 명 필요합니다")
        now = _utc_now()
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT INTO equipment_apply_job(source_snapshot_id, status, created_at_utc) VALUES (?, 'prepared', ?)",
                (raw_snapshot_id, now),
            )
            if cursor.lastrowid is None:
                raise UserDataError("장착 작업 생성 후 job_id가 반환되지 않았습니다")
            job_id = int(cursor.lastrowid)
            for ordinal, raw_role in enumerate(prepared_roles):
                role = _plain_object(raw_role, f"prepared_roles[{ordinal}]")
                uid = self._character_uid(_plain_object(role.get("character_uid"), "character_uid"))
                role_name = str(role.get("role_name") or "").strip()
                if not role_name:
                    raise UserDataValidationError("장착 작업 캐릭터 이름은 비워 둘 수 없습니다")
                plan_id = _integer(role.get("plan_id"), "plan_id", minimum=1)
                connection.execute(
                    """INSERT INTO equipment_apply_job_item(
                        job_id, ordinal, role_name, character_id, character_uid_json, plan_id, status
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                    (job_id, ordinal, role_name, _integer(role.get("character_id"), "character_id", minimum=1), _json(uid), plan_id),
                )
            connection.execute(
                "INSERT INTO equipment_apply_job_log(job_id, created_at_utc, level, message) VALUES (?, ?, 'info', ?)",
                (job_id, now, "작업이 생성되어 실행을 기다립니다"),
            )
            connection.commit()
            return job_id
        except (sqlite3.Error, UserDataValidationError) as exc:
            connection.rollback()
            if isinstance(exc, UserDataValidationError):
                raise
            raise UserDataError("원클릭 장착 작업을 만들 수 없습니다") from exc

    def get_equipment_apply_job(self, job_id: int) -> dict[str, Any] | None:
        raw_job_id = _integer(job_id, "job_id", minimum=1)
        job = self._one("SELECT * FROM equipment_apply_job WHERE job_id = ?", (raw_job_id,))
        if job is None:
            return None
        items = self._rows("SELECT * FROM equipment_apply_job_item WHERE job_id = ? ORDER BY ordinal", (raw_job_id,))
        for item in items:
            item["character_uid"] = _decoded(item.pop("character_uid_json"), {})
        job["items"] = items
        job["logs"] = self._rows("SELECT * FROM equipment_apply_job_log WHERE job_id = ? ORDER BY log_id", (raw_job_id,))
        return job

    def latest_resumable_equipment_apply_job(self) -> dict[str, Any] | None:
        row = self._one("SELECT job_id FROM equipment_apply_job WHERE status IN ('prepared', 'running', 'failed') ORDER BY job_id DESC LIMIT 1")
        return self.get_equipment_apply_job(int(row["job_id"])) if row else None

    def reset_failed_equipment_apply_job_items(self, job_id: int) -> None:
        raw_job_id = _integer(job_id, "job_id", minimum=1)
        now = _utc_now()
        self._db().execute("UPDATE equipment_apply_job_item SET status = 'pending', last_error = NULL WHERE job_id = ? AND status = 'failed'", (raw_job_id,))
        self._db().execute("UPDATE equipment_apply_job SET status = 'prepared', last_error = NULL WHERE job_id = ?", (raw_job_id,))
        self._db().execute("INSERT INTO equipment_apply_job_log(job_id, created_at_utc, level, message) VALUES (?, ?, 'info', ?)", (raw_job_id, now, "실패한 캐릭터를 초기화했으며 재시도를 기다립니다"))
        self._db().commit()

    def mark_equipment_apply_job_item(self, job_item_id: int, *, status: str, error: str | None = None, before_snapshot_id: int | None = None, after_snapshot_id: int | None = None, verified: bool = True) -> None:
        if status not in {"running", "succeeded", "failed"}:
            raise UserDataValidationError("장착 작업 항목 상태가 잘못되었습니다")
        raw_item_id = _integer(job_item_id, "job_item_id", minimum=1)
        item = self._one("SELECT job_id, role_name FROM equipment_apply_job_item WHERE job_item_id = ?", (raw_item_id,))
        if item is None:
            raise UserDataValidationError("장착 작업 항목이 없습니다")
        now = _utc_now()
        connection = self._db()
        connection.execute("BEGIN IMMEDIATE")
        if status == "running":
            connection.execute("UPDATE equipment_apply_job_item SET status = 'running', attempt_count = attempt_count + 1, started_at_utc = ?, last_error = NULL WHERE job_item_id = ?", (now, raw_item_id))
            connection.execute("UPDATE equipment_apply_job SET status = 'running', started_at_utc = COALESCE(started_at_utc, ?), last_error = NULL WHERE job_id = ?", (now, item["job_id"]))
            message, level = f"캐릭터 [{item['role_name']}] 처리 시작", "info"
        elif status == "succeeded":
            connection.execute("UPDATE equipment_apply_job_item SET status = 'succeeded', before_snapshot_id = ?, after_snapshot_id = ?, completed_at_utc = ?, last_error = NULL WHERE job_item_id = ?", (before_snapshot_id, after_snapshot_id, now, raw_item_id))
            message, level = (
                (f"캐릭터 [{item['role_name']}] 장착 확인됨", "info")
                if verified
                else (f"캐릭터 [{item['role_name']}] 장착 명령 전송됨, 다음 안정 가방 동기화 때 갱신", "info")
            )
        else:
            connection.execute("UPDATE equipment_apply_job_item SET status = 'failed', completed_at_utc = ?, last_error = ? WHERE job_item_id = ?", (now, str(error or "알 수 없는 오류"), raw_item_id))
            connection.execute("UPDATE equipment_apply_job SET status = 'failed', last_error = ? WHERE job_id = ?", (str(error or "알 수 없는 오류"), item["job_id"]))
            message, level = f"캐릭터 [{item['role_name']}] 실패: {error or '未知错误'}", "error"
        connection.execute("INSERT INTO equipment_apply_job_log(job_id, job_item_id, created_at_utc, level, message) VALUES (?, ?, ?, ?, ?)", (item["job_id"], raw_item_id, now, level, message))
        connection.commit()

    def complete_equipment_apply_job_if_done(self, job_id: int) -> bool:
        raw_job_id = _integer(job_id, "job_id", minimum=1)
        remaining = self._one("SELECT COUNT(*) AS count FROM equipment_apply_job_item WHERE job_id = ? AND status != 'succeeded'", (raw_job_id,))
        if int((remaining or {}).get("count", 0)) != 0:
            return False
        now = _utc_now()
        self._db().execute("UPDATE equipment_apply_job SET status = 'completed', completed_at_utc = ?, last_error = NULL WHERE job_id = ?", (now, raw_job_id))
        self._db().execute("INSERT INTO equipment_apply_job_log(job_id, created_at_utc, level, message) VALUES (?, ?, 'info', ?)", (raw_job_id, now, "전체 캐릭터 장착 작업이 완료되었습니다"))
        self._db().commit()
        return True
