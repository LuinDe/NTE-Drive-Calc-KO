# 传输冻结战报计算批次并校验原生结果身份，不包含业务公式。
from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.integrations.nte_analysis_core import NteAnalysisCoreClient


def compute_battle_batch(
    client: NteAnalysisCoreClient, operation: str, inputs: Sequence[dict[str, Any]],
    *, checkpoint: Callable[[], None] | None = None,
) -> tuple[dict[str, Any], ...]:
    from src.integrations.nte_analysis_core import (
        MAX_BYTES, MAX_JOBS, REQUEST_SCHEMA, RESPONSE_SCHEMA, NativeAnalysisError, _json_object,
    )

    if not client.supports_battle_compute:
        raise NativeAnalysisError("배포된 분석 구성 요소가 확장 전투 리포트 계산을 지원하지 않습니다")
    if not isinstance(operation, str) or not operation or len(operation) > 80:
        raise ValueError("전투 리포트 계산 작업이 잘못되었습니다")
    client._checkpoint(checkpoint)
    if not inputs:
        return ()
    if len(inputs) > MAX_JOBS:
        return tuple(row for start in range(0, len(inputs), MAX_JOBS)
                     for row in compute_battle_batch(client, operation, inputs[start:start + MAX_JOBS],
                                                    checkpoint=checkpoint))
    started = time.perf_counter()
    payload = json.dumps({
        "schema_version": REQUEST_SCHEMA, "dataset_version": client.dataset_version,
        "batch_kind": "battle_compute_v1", "operation": operation,
        "jobs": [{"job_id": str(index), "input": row} for index, row in enumerate(inputs)],
    }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_BYTES:
        del payload
        if len(inputs) <= 1:
            raise NativeAnalysisError("단일 전투 리포트 계산 입력이 크기 제한을 초과했습니다")
        midpoint = len(inputs) // 2
        return (compute_battle_batch(client, operation, inputs[:midpoint], checkpoint=checkpoint)
                + compute_battle_batch(client, operation, inputs[midpoint:], checkpoint=checkpoint))
    input_bytes = len(payload)
    raw = client._run(payload, checkpoint=checkpoint)
    response = _json_object(raw)
    if (response.get("schema_version") != RESPONSE_SCHEMA
            or response.get("engine_version") != client.engine_version
            or response.get("dataset_version") != client.dataset_version
            or response.get("batch_kind") != "battle_compute_v1"
            or response.get("operation") != operation or "error" in response):
        raise NativeAnalysisError("전투 리포트 계산 응답의 버전 또는 작업이 일치하지 않습니다")
    rows = response.get("results")
    if not isinstance(rows, list) or len(rows) != len(inputs):
        raise NativeAnalysisError("전투 리포트 계산 응답 수가 일치하지 않습니다")
    values = []
    for index, row in enumerate(rows):
        if (not isinstance(row, dict) or row.get("job_id") != str(index)
                or not isinstance(row.get("value"), dict)):
            raise NativeAnalysisError("전투 리포트 계산 응답의 식별 정보 또는 형식이 일치하지 않습니다")
        values.append(row["value"])
    kernel_ns = response.get("compute_elapsed_ns")
    if isinstance(kernel_ns, bool) or not isinstance(kernel_ns, int) or kernel_ns < 0:
        raise NativeAnalysisError("전투 리포트 계산 응답의 소요 시간이 잘못되었습니다")
    client._checkpoint(checkpoint)
    with client._lock:
        client._stats["compute_batch_calls"] += 1
        client._stats["compute_jobs"] += len(inputs)
        client._stats["compute_duration_seconds"] += time.perf_counter() - started
        client._stats["compute_kernel_seconds"] += kernel_ns / 1_000_000_000
        client._stats["compute_input_bytes"] += input_bytes
        client._stats["compute_output_bytes"] += len(raw)
    return tuple(values)
