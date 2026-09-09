# 持久化 nte-core 逐击证据，并在战后物化游戏当前角色配装。
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .battle_build_stage_support import normalize_frozen_advancement
from .protocols import UserDataDaoMixinHost
from .user_data_support import (
    UserDataError,
    UserDataValidationError,
    _decoded,
    _integer,
    _json,
    _json_object,
    _microseconds,
    _utc_now,
)

def _required_text(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise UserDataValidationError(f"{label}은(는) 비워 둘 수 없습니다")
    return normalized

def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_integer(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _integer(value, label, minimum=0)


class BattleAxisDaoMixin(UserDataDaoMixinHost):
    """Own staging, idempotent hit ingestion and final build materialization."""

    def battle_axis_capture_state(
        self,
        capture_operation_id: str,
    ) -> dict[str, Any] | None:
        operation_id = _required_text(capture_operation_id, "capture_operation_id")
        return self._one(
            """
            SELECT capture_id, capture_state, battle_record_id,
                   source_inventory_snapshot_id, stored_hits
            FROM battle_axis_capture WHERE capture_operation_id = ?
            """,
            (operation_id,),
        )

    def begin_battle_axis_capture(
        self,
        *,
        capture_operation_id: str,
        captured_at_utc: str,
        account_generation: int,
    ) -> dict[str, Any]:
        operation_id = _required_text(capture_operation_id, "capture_operation_id")
        generation = _integer(account_generation, "account_generation", minimum=0)
        now = _utc_now()
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT capture_id, capture_state, account_generation
                FROM battle_axis_capture WHERE capture_operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["capture_state"]) != "capturing"
                    or int(existing["account_generation"]) != generation
                ):
                    raise UserDataValidationError("전투 수집 작업 ID를 이미 다른 기록이 사용 중입니다")
                connection.rollback()
                return dict(existing)
            cursor = connection.execute(
                """
                INSERT INTO battle_axis_capture(
                    capture_operation_id, battle_record_id, capture_state,
                    source_inventory_snapshot_id, account_generation,
                    static_dataset_id, static_schema_version,
                    captured_at_utc, created_at_utc, updated_at_utc
                ) VALUES (?, NULL, 'capturing', NULL, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    operation_id,
                    generation,
                    _required_text(captured_at_utc, "captured_at_utc"),
                    now,
                    now,
                ),
            )
            connection.commit()
            return {
                "capture_id": int(cursor.lastrowid or 0),
                "capture_state": "capturing",
                "source_inventory_snapshot_id": None,
            }
        except (sqlite3.Error, UserDataError, UserDataValidationError):
            connection.rollback()
            raise

    def append_battle_axis_page(
        self,
        *,
        capture_operation_id: str,
        page: Mapping[str, Any],
    ) -> dict[str, Any]:
        operation_id = _required_text(capture_operation_id, "capture_operation_id")
        rows = page.get("rows")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
            raise UserDataValidationError("히트별 페이지 rows는 배열이어야 합니다")
        connection = self._db()
        now = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            capture = connection.execute(
                """
                SELECT capture_id, capture_state, source_battle_record_id
                FROM battle_axis_capture WHERE capture_operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if capture is None or str(capture["capture_state"]) != "capturing":
                raise UserDataValidationError("히트별 대상 수집이 존재하지 않거나 이미 종료되었습니다")
            capture_id = int(capture["capture_id"])
            source_record_id = _required_text(
                page.get("battle_record_id"),
                "page.battle_record_id",
            )
            previous_record_id = _optional_text(capture["source_battle_record_id"])
            if previous_record_id is not None and previous_record_id != source_record_id:
                raise UserDataValidationError("같은 수집에서 서로 다른 업스트림 전투 기록이 나타났습니다")
            inserted = 0
            for index, value in enumerate(rows):
                if not isinstance(value, Mapping):
                    raise UserDataValidationError(f"히트별 rows[{index}]가 객체가 아닙니다")
                inserted += self._insert_hit(connection, capture_id, value)
            stored_hits = int(
                connection.execute(
                    "SELECT COUNT(*) FROM battle_hit_evidence WHERE capture_id = ?",
                    (capture_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                UPDATE battle_axis_capture
                SET source_battle_record_id = ?, contract_version = ?,
                    source_generation = ?, axis_complete = ?,
                    first_available_cursor = ?, next_cursor = ?,
                    total_hits = ?, retained_hits = ?, stored_hits = ?,
                    updated_at_utc = ?
                WHERE capture_id = ?
                """,
                (
                    source_record_id,
                    _optional_integer(page.get("contract_version"), "contract_version"),
                    _optional_text(page.get("generation")),
                    int(bool(page.get("complete", True))),
                    _optional_text(page.get("first_available_cursor")),
                    _optional_text(page.get("next_cursor")),
                    _optional_text(page.get("total_hits")),
                    _optional_integer(page.get("retained_hits"), "retained_hits"),
                    stored_hits,
                    now,
                    capture_id,
                ),
            )
            connection.commit()
            return {
                "inserted_hits": inserted,
                "stored_hits": stored_hits,
                "next_cursor": _optional_text(page.get("next_cursor")),
                "complete": bool(page.get("complete", True)),
            }
        except (sqlite3.Error, UserDataError, UserDataValidationError):
            connection.rollback()
            raise

    @staticmethod
    def _insert_hit(
        connection: sqlite3.Connection,
        capture_id: int,
        hit: Mapping[str, Any],
    ) -> int:
        sequence_text = _required_text(hit.get("sequence"), "hit.sequence")
        try:
            sequence_order = int(sequence_text, 10)
        except ValueError as error:
            raise UserDataValidationError("hit.sequence는 10진수 문자열이어야 합니다") from error
        if sequence_order < 0:
            raise UserDataValidationError("hit.sequence는 음수일 수 없습니다")
        character_known = bool(hit.get("character_known", False))
        raw_character_id = _optional_integer(
            hit.get("character_id"),
            "hit.character_id",
        )
        if character_known and not raw_character_id:
            raise UserDataValidationError("귀속된 히트에는 양수 캐릭터 ID를 제공해야 합니다")
        character_id = raw_character_id if character_known and raw_character_id else None
        labels = hit.get("follow_up_labels") or []
        if not isinstance(labels, Sequence) or isinstance(
            labels, (str, bytes, bytearray)
        ):
            raise UserDataValidationError("hit.follow_up_labels는 배열이어야 합니다")
        target_context = hit.get("target_context") or []
        if isinstance(target_context, str):
            target_context = [target_context] if target_context.strip() else []
        if not isinstance(target_context, Sequence) or isinstance(
            target_context, (bytes, bytearray)
        ):
            raise UserDataValidationError("hit.target_context는 배열이어야 합니다")
        normalized_context = [
            str(value) for value in target_context if str(value).strip()
        ]
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO battle_hit_evidence(
                capture_id, sequence_text, sequence_order, timestamp_unix_us,
                relative_time_us, abyss_half, character_id, character_name,
                character_known, character_source, attribution_status,
                attribution_source, attribution_unknown_reason, team_snapshot_id,
                direction, damage, follow_up_damage, total_damage,
                follow_up_timestamp_unix_us, target_id, target_name,
                target_name_en, target_name_ja, target_monster_id, target_context,
                target_hp_before, target_hp_after, target_max_hp,
                target_hp_percent, gameplay_effect_index, gameplay_effect_name,
                ability_name, damage_name, damage_component, attack_type,
                damage_attribute, follow_up_labels_json, raw_hit_json,
                target_context_json, follow_up_damage_name,
                follow_up_damage_component, follow_up_attack_type,
                follow_up_damage_attribute, max_hp_reduction
            ) VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                capture_id,
                sequence_text,
                sequence_order,
                _microseconds(hit.get("timestamp_unix"), "hit.timestamp_unix"),
                _microseconds(
                    hit.get("relative_time_seconds"),
                    "hit.relative_time_seconds",
                ),
                _optional_text(hit.get("abyss_half")),
                character_id,
                _optional_text(hit.get("character_name")),
                int(character_known),
                _optional_text(hit.get("character_source")),
                _optional_text(hit.get("attribution_status")),
                _optional_text(hit.get("attribution_source")),
                _optional_text(hit.get("attribution_unknown_reason")),
                _optional_text(hit.get("team_snapshot_id")),
                _required_text(hit.get("direction"), "hit.direction"),
                float(hit.get("damage") or 0.0),
                float(hit.get("follow_up_damage") or 0.0),
                float(hit.get("total_damage") or 0.0),
                _microseconds(
                    hit.get("follow_up_timestamp_unix"),
                    "hit.follow_up_timestamp_unix",
                ),
                _optional_text(hit.get("target_id")),
                _optional_text(hit.get("target_name")),
                _optional_text(hit.get("target_name_en")),
                _optional_text(hit.get("target_name_ja")),
                _optional_text(hit.get("target_monster_id")),
                " · ".join(normalized_context) or None,
                hit.get("target_hp_before"),
                hit.get("target_hp_after"),
                hit.get("target_max_hp"),
                hit.get("target_hp_percent"),
                _optional_integer(
                    hit.get("gameplay_effect_index"),
                    "hit.gameplay_effect_index",
                ),
                _optional_text(hit.get("gameplay_effect_name")),
                _optional_text(hit.get("ability_name")),
                _optional_text(hit.get("damage_name")),
                _optional_text(hit.get("damage_component")),
                _optional_text(hit.get("attack_type")),
                _optional_text(hit.get("damage_attribute")),
                _json(list(labels)),
                _json_object(hit, "hit"),
                _json(normalized_context),
                _optional_text(hit.get("follow_up_damage_name")),
                _optional_text(hit.get("follow_up_damage_component")),
                _optional_text(hit.get("follow_up_attack_type")),
                _optional_text(hit.get("follow_up_damage_attribute")),
                hit.get("max_hp_reduction"),
            ),
        )
        return max(0, int(cursor.rowcount))

    def finalize_battle_axis_capture(
        self,
        *,
        capture_operation_id: str,
        battle_record_id: int,
        record: Mapping[str, Any] | None,
        observed_characters: Mapping[int, str],
        source_inventory_snapshot_id: int | None,
        static_dataset_id: str | None,
        static_schema_version: int,
        character_profiles: Mapping[int, Mapping[str, Any]],
        character_stat_snapshots: Mapping[int, Sequence[Mapping[str, Any]]] | None = None,
        formula_model_version: str = "battle-counterfactual-v3",
        name_mapping_version: str = "gameplay-effect-semantics-v1",
        finalized_at_utc: str,
    ) -> dict[str, Any]:
        operation_id = _required_text(capture_operation_id, "capture_operation_id")
        record_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        record_payload = dict(record or {})
        finalized_at = _required_text(finalized_at_utc, "finalized_at_utc")
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            capture = connection.execute(
                "SELECT * FROM battle_axis_capture WHERE capture_operation_id = ?",
                (operation_id,),
            ).fetchone()
            if capture is None:
                raise UserDataValidationError("히트별 수집 기록이 존재하지 않습니다")
            if str(capture["capture_state"]) == "finalized":
                if int(capture["battle_record_id"] or 0) != record_id:
                    raise UserDataValidationError("히트별 수집이 이미 다른 전투 리포트에 연결되어 있습니다")
                connection.rollback()
                return {
                    "battle_record_id": record_id,
                    "stored_hits": int(capture["stored_hits"]),
                    "materialized_character_count": int(
                        connection.execute(
                            """
                            SELECT COUNT(*) FROM battle_character_build_snapshot
                            WHERE battle_record_id = ?
                            """,
                            (record_id,),
                        ).fetchone()[0]
                    ),
                }
            capture_id = int(capture["capture_id"])
            snapshot_id = (
                None
                if source_inventory_snapshot_id is None
                else _integer(
                    source_inventory_snapshot_id,
                    "source_inventory_snapshot_id",
                    minimum=1,
                )
            )
            if snapshot_id is not None:
                snapshot = connection.execute(
                    """
                    SELECT source, complete
                    FROM inventory_snapshot WHERE snapshot_id = ?
                    """,
                    (snapshot_id,),
                ).fetchone()
                if snapshot is None:
                    raise UserDataValidationError("전투 후 게임의 현재 가방 스냅샷이 존재하지 않습니다")
                if str(snapshot["source"]) != "nte_core" or not bool(snapshot["complete"]):
                    raise UserDataValidationError("전투 리포트에는 완전한 게임 원본 가방 스냅샷만 저장할 수 있습니다")
            observed = {
                int(character_id): str(name or "")
                for character_id, name in observed_characters.items()
            }
            for row in connection.execute(
                """
                SELECT character_id, MAX(COALESCE(character_name, '')) AS name
                FROM battle_hit_evidence
                WHERE capture_id = ? AND character_id > 0 AND character_known = 1
                GROUP BY character_id
                """,
                (capture_id,),
            ):
                observed.setdefault(int(row["character_id"]), str(row["name"] or ""))
            selected_profiles: list[tuple[int, str, dict[str, Any]]] = []
            for character_id, observed_name in sorted(observed.items()):
                raw_character_id = _integer(
                    character_id,
                    "observed_character_id",
                    minimum=1,
                )
                profile = character_profiles.get(raw_character_id)
                if isinstance(profile, Mapping):
                    selected_profiles.append(
                        (raw_character_id, str(observed_name or ""), dict(profile))
                    )
            connection.execute(
                """
                INSERT OR REPLACE INTO battle_build_snapshot(
                    battle_record_id, source_inventory_snapshot_id,
                    account_generation, static_dataset_id, static_schema_version,
                    profile_schema_version, observed_character_count,
                    materialized_at_utc, formula_model_version,
                    name_mapping_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    snapshot_id,
                    int(capture["account_generation"]),
                    _optional_text(static_dataset_id),
                    _integer(
                        static_schema_version,
                        "static_schema_version",
                        minimum=1,
                    ),
                    1,
                    len(selected_profiles),
                    finalized_at,
                    _required_text(formula_model_version, "formula_model_version"),
                    _required_text(name_mapping_version, "name_mapping_version"),
                ),
            )
            self._materialize_character_builds(
                connection,
                record_id=record_id,
                snapshot_id=snapshot_id,
                profiles=selected_profiles,
            )
            self._materialize_character_stats(
                connection,
                record_id=record_id,
                character_ids={row[0] for row in selected_profiles},
                snapshots=character_stat_snapshots or {},
            )
            self._replace_time_stop_intervals(
                connection,
                capture_id=capture_id,
                intervals=record_payload.get("time_stop_intervals"),
            )
            raw_record_json = (
                _json_object(record_payload, "battle record")
                if record_payload
                else None
            )
            raw_record_sha256 = (
                hashlib.sha256(raw_record_json.encode("utf-8")).hexdigest()
                if raw_record_json is not None
                else None
            )
            stored_hits = int(capture["stored_hits"])
            axis_complete = bool(
                record_payload.get("axis_complete", capture["axis_complete"])
            )
            source_record_id = _optional_text(
                record_payload.get("battle_record_id")
                or capture["source_battle_record_id"]
            )
            contract_version = _optional_integer(
                record_payload.get("contract_version") or capture["contract_version"],
                "record.contract_version",
            )
            first_sequence = _optional_text(
                record_payload.get("axis_first_sequence")
            )
            total_hits = _optional_text(record_payload.get("axis_total_hits"))
            connection.execute(
                """
                UPDATE battle_record
                SET evidence_source_kind = 'nte_core_combat',
                    evidence_capability_level = ?, nte_core_record_id = ?,
                    nte_core_contract_version = ?, axis_complete = ?,
                    axis_first_sequence = ?, axis_total_hits = ?,
                    axis_stored_hits = ?
                WHERE battle_record_id = ?
                """,
                (
                    "hit_axis" if stored_hits > 0 else "summary_only",
                    source_record_id,
                    contract_version,
                    int(axis_complete),
                    first_sequence,
                    total_hits,
                    stored_hits,
                    record_id,
                ),
            )
            connection.execute(
                """
                UPDATE battle_axis_capture
                SET battle_record_id = ?, capture_state = 'finalized',
                    source_inventory_snapshot_id = ?, static_dataset_id = ?,
                    static_schema_version = ?,
                    source_battle_record_id = COALESCE(?, source_battle_record_id),
                    contract_version = COALESCE(?, contract_version),
                    source_generation = COALESCE(?, source_generation),
                    axis_complete = ?, first_sequence = ?, total_hits = ?,
                    retained_hits = COALESCE(?, retained_hits),
                    finalized_at_utc = ?, raw_record_json = ?,
                    raw_record_sha256 = ?, updated_at_utc = ?
                WHERE capture_id = ?
                """,
                (
                    record_id,
                    snapshot_id,
                    _optional_text(static_dataset_id),
                    _integer(
                        static_schema_version,
                        "static_schema_version",
                        minimum=1,
                    ),
                    source_record_id,
                    contract_version,
                    _optional_text(record_payload.get("generation")),
                    int(axis_complete),
                    first_sequence,
                    total_hits,
                    _optional_integer(
                        record_payload.get("axis_retained_hits"),
                        "record.axis_retained_hits",
                    ),
                    finalized_at,
                    raw_record_json,
                    raw_record_sha256,
                    _utc_now(),
                    capture_id,
                ),
            )
            connection.commit()
            return {
                "battle_record_id": record_id,
                "stored_hits": stored_hits,
                "materialized_character_count": len(selected_profiles),
            }
        except (sqlite3.Error, UserDataError, UserDataValidationError):
            connection.rollback()
            raise

    @staticmethod
    def _materialize_character_builds(
        connection: sqlite3.Connection,
        *,
        record_id: int,
        snapshot_id: int | None,
        profiles: Sequence[tuple[int, str, dict[str, Any]]],
    ) -> None:
        for character_id, observed_name, profile in profiles:
            skills = profile.get("skill_levels") or {}
            if not isinstance(skills, Mapping):
                raise UserDataError(f"캐릭터 {character_id}의 고정 스킬 구성이 손상되었습니다")
            (
                character_level,
                breakthrough_stage,
                fork_id,
                fork_level,
                fork_breakthrough_stage,
                frozen_profile,
            ) = normalize_frozen_advancement(profile, character_id)
            connection.execute(
                """
                INSERT INTO battle_character_build_snapshot(
                    battle_record_id, character_id, observed_name,
                    profile_source, character_level, breakthrough_stage,
                    awakening_level, fork_id, fork_level,
                    fork_breakthrough_stage,
                    fork_refinement_level, selected_skill_id, ordinal,
                    raw_profile_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record_id,
                    character_id,
                    observed_name or None,
                    str(profile.get("profile_source") or "unknown"),
                    character_level,
                    breakthrough_stage,
                    int(
                        6
                        if profile.get("awakening_level") is None
                        else profile["awakening_level"]
                    ),
                    fork_id,
                    fork_level,
                    fork_breakthrough_stage,
                    profile.get("fork_refinement_level"),
                    _optional_text(profile.get("selected_skill_id")),
                    int(profile.get("ordinal") or 0),
                    _json(frozen_profile),
                ),
            )
            connection.executemany(
                """
                INSERT INTO battle_character_skill_snapshot(
                    battle_record_id, character_id, skill_id, skill_level
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (record_id, character_id, str(skill_id), int(skill_level))
                    for skill_id, skill_level in sorted(skills.items())
                ],
            )
        if not profiles or snapshot_id is None:
            return
        character_ids = [row[0] for row in profiles]
        placeholders = ",".join("?" for _ in character_ids)
        items = connection.execute(
            f"""
            SELECT * FROM inventory_item
            WHERE snapshot_id = ? AND equipped = 1
              AND equipped_character_id IN ({placeholders})
            ORDER BY equipped_character_id, kind, uid_slot, uid_serial
            """,
            (snapshot_id, *character_ids),
        ).fetchall()
        selected_uids: list[tuple[int, int]] = []
        for item in items:
            raw_item = _decoded(str(item["raw_item_json"]), {})
            placement = (
                raw_item.get("equipped_placement")
                if isinstance(raw_item, Mapping)
                else None
            )
            placement = placement if isinstance(placement, Mapping) else {}
            connection.execute(
                """
                INSERT INTO battle_equipment_snapshot(
                    battle_record_id, character_id, uid_serial, uid_slot,
                    kind, item_id, suit_id, geometry, grid_count, quality,
                    level, max_level, locked, target_row, target_column,
                    names_json, suit_names_json, raw_item_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record_id,
                    int(item["equipped_character_id"]),
                    int(item["uid_serial"]),
                    int(item["uid_slot"]),
                    item["kind"],
                    item["item_id"],
                    item["suit_id"],
                    item["geometry"],
                    int(item["grid_count"] or 0),
                    item["quality"],
                    item["level"],
                    item["max_level"],
                    int(item["locked"]),
                    placement.get("row"),
                    placement.get("column"),
                    item["names_json"],
                    item["suit_names_json"],
                    item["raw_item_json"],
                ),
            )
            selected_uids.append((int(item["uid_serial"]), int(item["uid_slot"])))
        for uid_serial, uid_slot in selected_uids:
            stats = connection.execute(
                """
                SELECT stat_group, ordinal, property_id, value, is_percent,
                       names_json, raw_stat_json
                FROM inventory_item_stat
                WHERE snapshot_id = ? AND uid_serial = ? AND uid_slot = ?
                ORDER BY stat_group, ordinal
                """,
                (snapshot_id, uid_serial, uid_slot),
            ).fetchall()
            connection.executemany(
                """
                INSERT INTO battle_equipment_stat_snapshot(
                    battle_record_id, uid_serial, uid_slot, stat_group,
                    ordinal, property_id, value, is_percent, names_json,
                    raw_stat_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        record_id,
                        uid_serial,
                        uid_slot,
                        stat["stat_group"],
                        int(stat["ordinal"]),
                        stat["property_id"],
                        float(stat["value"]),
                        int(stat["is_percent"]),
                        stat["names_json"],
                        stat["raw_stat_json"],
                    )
                    for stat in stats
                ],
            )

    @staticmethod
    def _materialize_character_stats(
        connection: sqlite3.Connection,
        *,
        record_id: int,
        character_ids: set[int],
        snapshots: Mapping[int, Sequence[Mapping[str, Any]]],
    ) -> None:
        for character_id, rows in snapshots.items():
            normalized_character_id = int(character_id)
            if normalized_character_id not in character_ids:
                continue
            seen: set[tuple[str, str]] = set()
            values = []
            for ordinal, row in enumerate(rows):
                source_group = str(row.get("source_group") or "resolved")
                if source_group not in {
                    "character",
                    "fork",
                    "likeability",
                    "world_bonus",
                    "equipment",
                    "resolved",
                }:
                    raise UserDataValidationError("전투 리포트 속성 스냅샷 출처가 잘못되었습니다")
                property_id = _required_text(row.get("property_id"), "property_id")
                key = (source_group, property_id)
                if key in seen:
                    raise UserDataValidationError("전투 리포트 속성 스냅샷에 중복 속성이 있습니다")
                seen.add(key)
                values.append(
                    (
                        record_id,
                        normalized_character_id,
                        source_group,
                        property_id,
                        _required_text(
                            row.get("display_name") or property_id,
                            "display_name",
                        ),
                        float(row.get("value") or 0.0),
                        int(bool(row.get("is_percent", False))),
                        int(row.get("ordinal", ordinal)),
                    )
                )
            connection.executemany(
                """
                INSERT INTO battle_character_stat_snapshot(
                    battle_record_id, character_id, source_group, property_id,
                    display_name, value, is_percent, ordinal
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                values,
            )

    @staticmethod
    def _replace_time_stop_intervals(
        connection: sqlite3.Connection,
        *,
        capture_id: int,
        intervals: Any,
    ) -> None:
        connection.execute(
            "DELETE FROM battle_time_stop_interval WHERE capture_id = ?",
            (capture_id,),
        )
        if intervals is None:
            return
        if not isinstance(intervals, Sequence) or isinstance(
            intervals, (str, bytes, bytearray)
        ):
            raise UserDataValidationError("time_stop_intervals는 배열이어야 합니다")
        for ordinal, value in enumerate(intervals):
            if not isinstance(value, Mapping):
                raise UserDataValidationError("time_stop_interval은 객체여야 합니다")
            start = value.get("start_unix", value.get("started_at_unix"))
            end = value.get("end_unix", value.get("ended_at_unix"))
            duration = value.get("duration_seconds")
            start_offset = value.get("start_offset_seconds")
            end_offset = value.get("end_offset_seconds")
            pause_type_mask = _optional_integer(value.get("pause_type_mask"), "time_stop.pause_type_mask")
            if pause_type_mask is not None and not 0 < pause_type_mask <= 0xFFFF_FFFF:
                raise UserDataValidationError("time_stop.pause_type_mask는 0이 아닌 u32 정수 또는 null이어야 합니다")
            if duration is None and isinstance(start, (int, float)) and isinstance(
                end, (int, float)
            ):
                duration = max(0.0, float(end) - float(start))
            if duration is None and isinstance(
                start_offset, (int, float)
            ) and isinstance(end_offset, (int, float)):
                duration = max(0.0, float(end_offset) - float(start_offset))
            connection.execute(
                """
                INSERT INTO battle_time_stop_interval(
                    capture_id, ordinal, start_unix_us, end_unix_us,
                    duration_us, raw_interval_json, pause_type_mask
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    capture_id,
                    ordinal,
                    _microseconds(start, "time_stop.start"),
                    _microseconds(end, "time_stop.end"),
                    _microseconds(duration, "time_stop.duration"),
                    _json_object(value, "time_stop_interval"),
                    pause_type_mask,
                ),
            )
