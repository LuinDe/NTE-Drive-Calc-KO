# 原子保存账号完整物品原始快照，与装备库存及当前计算指针隔离。
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.domain.all_item_snapshot import encode_all_item_snapshot
from .protocols import UserDataDaoMixinHost
from .user_data_support import DEFAULT_SNAPSHOT_RETENTION_COUNT, UserDataError, UserDataValidationError, _utc_now


def read_latest_all_item_snapshot_archive(database_path: str | Path, *, account_id: str):
    """Read one account archive without schema migration or writes."""

    uri = f"{Path(database_path).expanduser().resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            profile = connection.execute(
                "SELECT account_id FROM database_profile WHERE singleton_id = 1"
            ).fetchone()
            if profile is None or profile["account_id"] != account_id:
                raise UserDataValidationError("네이티브 아이템 보관 기록이 현재 계정과 일치하지 않습니다.")
            row = connection.execute(
                "SELECT snapshot_id, source, raw_snapshot_json, content_sha256, saved_at_utc "
                "FROM all_item_snapshot ORDER BY snapshot_id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
    except sqlite3.Error as exc:
        raise UserDataError("네이티브 아이템 아카이브 읽기 실패") from exc


class AllItemSnapshotDaoMixin(UserDataDaoMixinHost):
    def save_all_item_snapshot(self, snapshot, *, account_id, check):
        check()
        if not account_id or self.profile()["account_id"] != account_id:
            raise UserDataValidationError("전체 아이템 스냅샷의 계정 식별이 일치하지 않습니다")
        try:
            raw = encode_all_item_snapshot(snapshot)
        except (ValueError, TypeError, OverflowError) as error:
            raise UserDataValidationError("전체 아이템 스냅샷이 완전성 검증을 통과하지 못했습니다") from error
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        identity = snapshot["providerId"], snapshot["domainKey"], snapshot["snapshotId"]
        connection = self._db()
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            check()
            prior = connection.execute(
                "SELECT snapshot_id, content_sha256 FROM all_item_snapshot "
                "WHERE provider_id=? AND domain_key=? AND native_snapshot_id=?", identity,
            ).fetchone()
            if prior is not None:
                if prior["content_sha256"] != digest:
                    raise UserDataValidationError("같은 아이템 스냅샷 식별 정보에 대응하는 내용이 서로 충돌합니다")
                check()
                return int(prior["snapshot_id"])
            cursor = connection.execute(
                "INSERT INTO all_item_snapshot(source, provider_id, domain_key, native_snapshot_id, "
                "revision, record_count, collection_scope, raw_snapshot_json, content_sha256, saved_at_utc) "
                "VALUES ('nte_core', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*identity, snapshot["revision"], snapshot["recordCount"], snapshot["collectionScope"], raw, digest, _utc_now()),
            )
            saved_id = int(cursor.lastrowid)
            connection.execute(
                "DELETE FROM all_item_snapshot WHERE snapshot_id NOT IN "
                "(SELECT snapshot_id FROM all_item_snapshot ORDER BY snapshot_id DESC LIMIT ?)",
                (DEFAULT_SNAPSHOT_RETENTION_COUNT,),
            )
            check()
        return saved_id

    def latest_all_item_snapshot(self):
        row = self._one("SELECT raw_snapshot_json FROM all_item_snapshot ORDER BY snapshot_id DESC LIMIT 1")
        return json.loads(row["raw_snapshot_json"]) if row else None
