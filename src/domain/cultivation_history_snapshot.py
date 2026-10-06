# 校验养成历史的轻量结果快照与输入角色身份一致性。
from __future__ import annotations

from typing import Any

from src.domain.cultivation_history import (
    PAYLOAD_VERSION, boolean, integer, object_fields, rows, text_value, unique, utc_time,
)


def _stamina_values(value: dict[str, Any]) -> None:
    if text_value(value["status"]) not in {"complete", "partial", "unavailable"}:
        raise ValueError("육성 기록 결과 상태가 잘못되었습니다")
    known = integer(value["known_stamina"])
    total = value["total_stamina"]
    if total is not None:
        integer(total, known)


def normalize_snapshot(value: object, configuration: dict[str, Any]) -> dict[str, Any]:
    data = object_fields(value, "version dataset algorithm_version materials target_summaries stamina gaps")
    if integer(data["version"]) != PAYLOAD_VERSION:
        raise ValueError("육성 기록 결과 버전이 잘못되었습니다")
    dataset = object_fields(data["dataset"], "dataset_id schema_version importer_version built_at_utc")
    text_value(dataset["dataset_id"], maximum=256)
    integer(dataset["schema_version"], 1)
    # Dataset importers use a version label, independent of the schema integer.
    text_value(dataset["importer_version"], maximum=128)
    if utc_time(dataset["built_at_utc"]) is None:
        raise ValueError("육성 기록 자료에 생성 시각이 없습니다")
    data["dataset"] = dataset
    text_value(data["algorithm_version"], maximum=128)
    materials = []
    for raw in rows(data["materials"]):
        item = object_fields(raw, "item_id name required allocated_equivalent remaining stamina_eligible")
        text_value(item["item_id"], maximum=256)
        text_value(item["name"])
        required = integer(item["required"])
        allocated = integer(item["allocated_equivalent"], 0, required)
        if integer(item["remaining"], 0, required) + allocated != required:
            raise ValueError("육성 기록의 재료 합계가 일치하지 않습니다")
        boolean(item["stamina_eligible"])
        materials.append(item)
    unique(materials, "item_id")
    data["materials"] = materials
    targets = {target["line_id"]: target["character_id"] for target in configuration["targets"]}
    summaries = []
    for raw in rows(data["target_summaries"]):
        summary = object_fields(raw, "line_id character_id status known_stamina total_stamina")
        text_value(summary["line_id"], maximum=256)
        integer(summary["character_id"], 1)
        if targets.get(summary["line_id"]) != summary["character_id"]:
            raise ValueError("육성 기록의 입력과 결과 캐릭터가 일치하지 않습니다")
        _stamina_values(summary)
        summaries.append(summary)
    unique(summaries, "line_id")
    if len(summaries) != len(targets):
        raise ValueError("육성 기록 결과에 빠진 캐릭터가 있습니다")
    data["target_summaries"] = summaries
    stamina = object_fields(data["stamina"], "status known_stamina total_stamina runs")
    _stamina_values(stamina)
    if (stamina["status"] == "complete") != (stamina["total_stamina"] is not None):
        raise ValueError("육성 기록의 전체 스태미나와 상태가 일치하지 않습니다")
    runs = []
    for raw in rows(stamina["runs"]):
        run = object_fields(raw, "stage_id label runs stamina_per_run total_stamina source")
        text_value(run["stage_id"], maximum=256)
        text_value(run["label"])
        integer(run["runs"], 1)
        integer(run["stamina_per_run"], 1)
        if integer(run["total_stamina"]) != run["runs"] * run["stamina_per_run"]:
            raise ValueError("육성 기록의 던전 횟수와 스태미나가 일치하지 않습니다")
        text_value(run["source"], maximum=128)
        runs.append(run)
    unique(runs, "stage_id")
    if sum(run["total_stamina"] for run in runs) != stamina["known_stamina"]:
        raise ValueError("육성 기록의 던전 합계와 확인된 스태미나가 일치하지 않습니다")
    if stamina["total_stamina"] is not None and stamina["total_stamina"] != stamina["known_stamina"]:
        raise ValueError("육성 기록의 전체 스태미나와 확인된 스태미나가 일치하지 않습니다")
    stamina["runs"] = runs
    data["stamina"] = stamina
    gaps = []
    for raw in rows(data["gaps"]):
        gap = object_fields(raw, "line_id reason_code item_id")
        if gap["line_id"] is not None:
            text_value(gap["line_id"], maximum=128)
            if gap["line_id"] not in targets:
                raise ValueError("육성 기록의 누락 항목 캐릭터 식별 정보가 잘못되었습니다")
        text_value(gap["reason_code"])
        if gap["item_id"] is not None:
            text_value(gap["item_id"], maximum=256)
        gaps.append(gap)
    data["gaps"] = gaps
    return data
