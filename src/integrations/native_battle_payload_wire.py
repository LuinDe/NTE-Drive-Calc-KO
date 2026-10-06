# 无损还原原生逐击文本分片，并限制展开后的总数据量。
from __future__ import annotations

from src.integrations.nte_analysis_core import NativeAnalysisError

MAX_EXPANDED_PAYLOAD_BYTES = 128 * 1024 * 1024


def restore_payloads(page: dict) -> dict:
    table = page.get("native_payload_fragments")
    if table is None:
        return page
    if not isinstance(table, list) or any(not isinstance(part, str) for part in table):
        raise NativeAnalysisError("분석 코어 네이티브 증거 조각 테이블이 유효하지 않음")
    sizes = [len(part.encode("utf-8")) for part in table]
    used = 0
    restored = dict(page)
    for section in ("analysis", "candidate_display_analysis"):
        snapshot = page.get(section)
        if not isinstance(snapshot, dict):
            continue
        snapshot = dict(snapshot)
        for field in ("hits", "timeline_hits"):
            rows = []
            for hit in snapshot.get(field, ()):
                evidence = hit.get("native_evidence") if isinstance(hit, dict) else None
                if isinstance(evidence, dict) and "payload_fragments" in evidence:
                    indices = evidence["payload_fragments"]
                    if ("payload_json" in evidence or "reference_event_id" in evidence
                            or not isinstance(indices, list)
                            or any(type(i) is not int or not 0 <= i < len(table) for i in indices)):
                        raise NativeAnalysisError("분석 코어 네이티브 증거 조각 참조가 유효하지 않음")
                    used += sum(sizes[i] for i in indices)
                    if used > MAX_EXPANDED_PAYLOAD_BYTES:
                        raise NativeAnalysisError("분석 코어 네이티브 증거 확장이 크기 제한을 초과했습니다")
                    evidence = dict(evidence)
                    del evidence["payload_fragments"]
                    evidence["payload_json"] = "".join(table[i] for i in indices)
                    hit = {**hit, "native_evidence": evidence}
                rows.append(hit)
            snapshot[field] = rows
        restored[section] = snapshot
    return restored
