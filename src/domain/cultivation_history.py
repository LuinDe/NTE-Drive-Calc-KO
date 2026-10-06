# 定义养成历史的有界显式数据格式和不可变持久化投影。
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.domain.progression_stamina import project_identification_level

PAYLOAD_VERSION = 1
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_COLLECTION_SIZE = 100_000


class HistoryConflict(ValueError):
    """选定记录已改变或删除，调用方必须刷新而不是补插。"""


class HistoryContextExpired(RuntimeError):
    """冻结账号、资料或草稿已经失效。"""


def object_fields(value: object, fields: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(fields.split()):
        raise ValueError("육성 기록 객체의 필드 또는 버전이 일치하지 않습니다")
    return dict(value)


def text_value(value: object, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError("육성 기록 텍스트 또는 식별 정보가 잘못되었습니다")
    if any(ord(char) < 32 for char in value):
        raise ValueError("육성 기록 텍스트에 제어 문자가 포함되어 있습니다")
    return value


def integer(value: object, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("육성 기록 수치의 유형 또는 범위가 잘못되었습니다")
    return value


def boolean(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("육성 기록 스위치는 불리언 값이어야 합니다")
    return value


def rows(value: object) -> list[Any]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_COLLECTION_SIZE:
        raise ValueError("육성 기록 컬렉션의 형식 또는 규모가 잘못되었습니다")
    return list(value)


def utc_time(value: object) -> str | None:
    if value is None:
        return None
    raw = text_value(value, maximum=64)
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("육성 기록 관측 시각이 잘못되었습니다") from error
    if moment.utcoffset() is None or moment.utcoffset().total_seconds() != 0:
        raise ValueError("육성 기록 관측 시각은 UTC여야 합니다")
    return raw


def unique(values: list[dict[str, Any]], field: str) -> None:
    if len({row[field] for row in values}) != len(values):
        raise ValueError(f"육성 기록에 중복된 {field} 항목이 있습니다")


def _state(value: dict[str, Any]) -> None:
    for prefix in ("current", "target"):
        level = integer(value[f"{prefix}_level"], 1, 80)
        stage = integer(value[f"{prefix}_stage"], 0, 6)
        minimum = 1 if stage == 0 else (stage + 1) * 10
        if not minimum <= level <= (stage + 2) * 10:
            raise ValueError("육성 기록의 레벨과 돌파 단계가 일치하지 않습니다")
    # Disabled modules retain their editor state; interval feasibility belongs to the solver.


def normalize_configuration(value: object) -> dict[str, Any]:
    data = object_fields(value, "version mode targets hunter_level identification_level material_scope owned_materials")
    if integer(data["version"]) != PAYLOAD_VERSION or text_value(data["mode"]) not in {"single", "batch"}:
        raise ValueError("육성 기록 구성의 버전 또는 모드가 잘못되었습니다")
    if text_value(data["material_scope"]) not in {"all", "stamina"}:
        raise ValueError("육성 기록 재료 범위가 잘못되었습니다")
    integer(data["hunter_level"], 1, 60)
    identification = data["identification_level"]
    if identification is not None:
        integer(identification, 0, 7)
    targets = []
    for raw in rows(data["targets"]):
        target = object_fields(raw, "line_id character_id name current_level current_stage target_level target_stage character_enabled skills_enabled skills fork")
        text_value(target["line_id"], maximum=256)
        integer(target["character_id"], 1)
        text_value(target["name"])
        _state(target)
        boolean(target["character_enabled"])
        boolean(target["skills_enabled"])
        skills = []
        for raw_skill in rows(target["skills"]):
            skill = object_fields(raw_skill, "skill_id current_level target_level")
            text_value(skill["skill_id"], maximum=256)
            integer(skill["current_level"], 1, 10)
            integer(skill["target_level"], 1, 10)
            skills.append(skill)
        unique(skills, "skill_id")
        target["skills"] = skills
        if target["fork"] is not None:
            fork = object_fields(target["fork"], "fork_id name enabled current_level current_stage target_level target_stage")
            text_value(fork["fork_id"], maximum=256)
            text_value(fork["name"])
            boolean(fork["enabled"])
            _state(fork)
            target["fork"] = fork
        targets.append(target)
    if not targets or (data["mode"] == "single" and len(targets) != 1):
        raise ValueError("육성 기록의 캐릭터 집합과 모드가 일치하지 않습니다")
    unique(targets, "line_id")
    unique(targets, "character_id")
    data["targets"] = targets
    data["owned_materials"] = normalize_owned_materials(data["owned_materials"])
    return data


def normalize_owned_materials(value: object) -> list[dict[str, Any]]:
    owned = []
    for raw in rows(value):
        item = object_fields(raw, "item_id quantity manual_override source observed_at_utc")
        text_value(item["item_id"], maximum=256)
        integer(item["quantity"], 0, 99_999_999)
        boolean(item["manual_override"])
        if text_value(item["source"]) not in {"manual", "native", "packet"}:
            raise ValueError("육성 기록 재료 출처가 잘못되었습니다")
        utc_time(item["observed_at_utc"])
        if item["manual_override"] and item["source"] != "manual":
            raise ValueError("육성 기록의 수동 덮어쓰기와 출처가 일치하지 않습니다")
        owned.append(item)
    unique(owned, "item_id")
    return sorted(owned, key=lambda item: item["item_id"])


def _bounded_tree(value: object, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("육성 기록의 중첩이 너무 깊습니다")
    if isinstance(value, Mapping):
        if len(value) > MAX_COLLECTION_SIZE:
            raise ValueError("육성 기록 객체 규모가 한도를 초과했습니다")
        for key, child in value.items():
            text_value(key, maximum=128)
            _bounded_tree(child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in rows(value):
            _bounded_tree(child, depth + 1)
    elif value is not None and type(value) not in {str, int, bool}:
        raise ValueError("육성 기록에 지원하지 않는 수치 또는 객체가 포함되어 있습니다")


def _encoded(value: object) -> str:
    _bounded_tree(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("육성 기록이 1건당 크기 한도를 초과했습니다. 계산 결과는 유지됩니다")
    return raw


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("육성 기록 JSON에 중복 필드가 있습니다")
        result[key] = value
    return result


def decode_summary(raw: str) -> dict[str, Any]:
    """列表只解码有界摘要；损坏行不影响其正式身份和删除能力。"""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("육성 기록 요약의 형식 또는 크기가 잘못되었습니다")
    try:
        value = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
        _bounded_tree(value)
        data = object_fields(value, "version characters status known_stamina total_stamina gap_count")
        if integer(data["version"]) != PAYLOAD_VERSION:
            raise ValueError("육성 기록 요약 버전은 아직 지원하지 않습니다")
        characters = []
        for row in rows(data["characters"]):
            character = object_fields(row, "character_id name target_label")
            integer(character["character_id"], 1)
            text_value(character["name"])
            text_value(character["target_label"])
            characters.append(character)
        if not characters:
            raise ValueError("육성 기록 요약에 캐릭터가 없습니다")
        unique(characters, "character_id")
        data["characters"] = characters
        if text_value(data["status"]) not in {"complete", "partial", "unavailable"}:
            raise ValueError("육성 기록 요약 상태가 잘못되었습니다")
        integer(data["known_stamina"])
        integer(data["gap_count"], 0, MAX_COLLECTION_SIZE)
        total = data["total_stamina"]
        if total is not None:
            integer(total)
        if ((data["status"] == "complete") != (total is not None)
                or total is not None and total != data["known_stamina"]):
            raise ValueError("육성 기록 요약의 스태미나 상태가 일치하지 않습니다")
        return data
    except (RecursionError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("육성 기록 요약 분석 실패") from error


def freeze_configuration(value: object) -> str:
    """在提交计算之前复制并校验完整草稿，之后不再读取活控件。"""
    _encoded(value)
    configuration = normalize_configuration(value)
    project_identification_level(configuration["hunter_level"], effective_level=configuration["identification_level"])
    return _encoded(configuration)


def _configuration_projection(raw: str) -> dict[str, Any]:
    """有界读取配置供只读名称投影使用，不消费当前角色资料。"""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("기록 구성의 크기 또는 형식이 잘못되었습니다")
    try:
        value = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
        _bounded_tree(value)
        return normalize_configuration(value)
    except (RecursionError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("기록 구성 이름 분석 실패") from error


def configuration_character_labels(raw: str) -> tuple[str, ...]:
    """从本页历史的原配置投影名称，不改持久化摘要或消费当前资料。"""
    config = _configuration_projection(raw)
    return tuple(target["name"] + (f"（{target['fork']['name']}）" if target["fork"] else "")
                 for target in config["targets"])


def configuration_search_names(raw: str) -> tuple[str, ...]:
    """角色与选定弧盘分别匹配，未参与养成的弧盘也保留其名称。"""
    config = _configuration_projection(raw)
    names = []
    for target in config["targets"]:
        names.append(target["name"])
        if target["fork"] is not None:
            names.append(target["fork"]["name"])
    return tuple(names)


@dataclass(frozen=True, slots=True)
class HistoryCalculationEnvelope:
    account_id: str
    context_identity: object
    session_id: str
    request_revision: int
    write_epoch: int
    configuration_json: str
    explicit_calculation: bool


def _target_label(target: dict[str, Any]) -> str:
    parts = []
    if target["character_enabled"]:
        parts.append(f"캐릭터 {target['current_level']}/{target['current_stage']}→{target['target_level']}/{target['target_stage']}")
    if target["skills_enabled"] and target["skills"]:
        levels = [skill["target_level"] for skill in target["skills"]]
        parts.append(f"스킬 {len(levels)}개, 목표 {min(levels)}–{max(levels)}레벨")
    fork = target["fork"]
    if fork is not None and fork["enabled"]:
        parts.append(f"아크 {fork['current_level']}/{fork['current_stage']}→{fork['target_level']}/{fork['target_stage']}")
    return "; ".join(parts) or "활성화된 육성 모듈 없음"


@dataclass(frozen=True, slots=True)
class HistoryPayload:
    """持久化边界只传不可变字符串，不携带控件、路径或活对象。"""

    mode: str
    configuration_json: str
    result_snapshot_json: str
    summary_json: str
    content_sha256: str

    @classmethod
    def create(cls, configuration: object, result: object) -> HistoryPayload:
        from src.domain.cultivation_history_snapshot import normalize_snapshot

        # Check resources before traversing or normalizing caller-provided structures.
        _encoded(configuration)
        _encoded(result)
        config = normalize_configuration(configuration)
        snapshot = normalize_snapshot(result, config)
        config_raw, snapshot_raw = _encoded(config), _encoded(snapshot)
        if len(config_raw.encode("utf-8")) + len(snapshot_raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("육성 기록이 1건당 크기 한도를 초과했습니다. 계산 결과는 유지됩니다")
        summary = {
            "version": PAYLOAD_VERSION,
            "characters": [{"character_id": target["character_id"], "name": target["name"],
                            "target_label": _target_label(target)}
                           for target in config["targets"]],
            "status": snapshot["stamina"]["status"],
            "known_stamina": snapshot["stamina"]["known_stamina"],
            "total_stamina": snapshot["stamina"]["total_stamina"],
            "gap_count": len(snapshot["gaps"]),
        }
        fingerprint = hashlib.sha256((config_raw + "\n" + snapshot_raw).encode("utf-8")).hexdigest()
        return cls(config["mode"], config_raw, snapshot_raw, _encoded(summary), fingerprint)

    @classmethod
    def decode(cls, configuration_json: str, result_snapshot_json: str) -> HistoryPayload:
        if (not isinstance(configuration_json, str) or not isinstance(result_snapshot_json, str)
                or len(configuration_json.encode("utf-8")) + len(result_snapshot_json.encode("utf-8")) > MAX_PAYLOAD_BYTES):
            raise ValueError("육성 기록 본문의 형식 또는 크기가 잘못되었습니다")
        try:
            config = json.loads(configuration_json, object_pairs_hook=_no_duplicate_keys)
            snapshot = json.loads(result_snapshot_json, object_pairs_hook=_no_duplicate_keys)
            return cls.create(config, snapshot)
        except (RecursionError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("육성 기록 본문 분석 실패") from error


@dataclass(frozen=True, slots=True)
class HistorySelection:
    history_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class HistorySummary:
    history_id: str
    mode: str
    revision: int
    first_calculated_at_utc: str
    last_calculated_at_utc: str
    summary_json: str
    # Page-local read projection; never serialized or used for identity/deletion.
    character_labels: tuple[str, ...] = field(default=(), compare=False)


@dataclass(frozen=True, slots=True)
class HistoryRecord:
    summary: HistorySummary
    payload: HistoryPayload


@dataclass(frozen=True, slots=True)
class HistoryPage:
    items: tuple[HistorySummary, ...]
    total: int
    page: int
    page_size: int
