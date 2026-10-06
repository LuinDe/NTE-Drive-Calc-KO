# 校验性能计数并在账号日志旁保存有界诊断记录，不保留业务正文。
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.integrations.native_snapshot_timing import STAGES
from src.observability import OperationContext, log_event


def performance_counters(status: dict) -> dict[str, dict[str, int]]:
    native = status.get("native_status")
    costs = native.get("runtimePerformance") if isinstance(native, dict) else None
    if not isinstance(costs, dict) or type(costs.get("version")) is not int or costs["version"] != 1:
        return {}
    candidates = {name: costs.get(name) for name in ("snapshot_pulse", "snapshot_read")}
    for field, prefix in (("snapshot_diagnostics", "snapshot."),
                          ("user_snapshot_diagnostics", "user.snapshot.")):
        detail = costs.get(field)
        if isinstance(detail, dict) and type(detail.get("version")) is int and detail["version"] == 1:
            stages = detail.get("stages")
            if isinstance(stages, dict):
                candidates.update({prefix + name: stages.get(name) for name in STAGES})
    result = {}
    for name, row in candidates.items():
        keys = ("calls", "total_us", "max_us")
        if not isinstance(row, dict) or not all(type(row.get(k)) is int and 0 <= row[k] < 2**63 for k in keys):
            continue
        if row["max_us"] > row["total_us"] or (row["calls"] == 0 and row["total_us"] != 0):
            continue
        result[name] = {k: row[k] for k in keys}
    return result


class PerformanceJournal:
    """Worker-owned, bounded trace. The original capture files remain untouched."""

    def __init__(self, log_dir: Path, *, byte_limit: int = 16 * 1024 * 1024):
        self.session = uuid4().hex
        directory = log_dir / "performance"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = directory / f"performance_{stamp}_{self.session[:8]}.jsonl"
        self._stream = self.path.open("x", encoding="utf-8")
        self._bytes, self._limit = 0, byte_limit
        self.context = OperationContext.create("performance")
        try:
            self.write({"kind": "session_start", "schema": "calc.performance/2", "session": self.session,
                        "source": "native.runtimePerformance/1", "metric_contract": "calc.performance.metrics/1",
                        "frame_source": "presentmon.present_interval", "cost_complete": False,
                        "raw_capture_directory": str(log_dir / "nte_core" / "raw_capture")})
        except Exception:
            self._stream.close()
            raise
        log_event("INFO", "performance.started", "성능 문제 해결 기록을 시작했습니다", self.context,
                  session=self.session, path=str(self.path))

    def write(self, row: dict):
        text = json.dumps({**row, "utc": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n"
        size = len(text.encode("utf-8"))
        if self._bytes + size > self._limit:
            raise OSError("performance_budget_reached")
        self._stream.write(text)
        self._stream.flush()
        self._bytes += size

    def close(self):
        try:
            self.write({"kind": "session_end", "session": self.session})
        finally:
            self._stream.close()
            log_event("INFO", "performance.stopped", "성능 문제 해결 기록을 종료했습니다", self.context,
                      session=self.session, path=str(self.path))
