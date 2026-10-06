# 将冻结的历史配置与轻量合计格式化为只读文本，不重新计算或读取当前账号状态。
from __future__ import annotations

from datetime import datetime

from src.domain.cultivation_history import HistorySummary, decode_summary


def summary_text(summary: HistorySummary) -> tuple[str, str]:
    try:
        value = decode_summary(summary.summary_json)
        names = summary.character_labels or tuple(item["name"] for item in value["characters"])
        name = "、".join(names)
        total = value["total_stamina"]
        stamina = f"{total:,}" if total is not None else f"확인된 {value['known_stamina']:,} · 불완전"
        return name, stamina
    except (ValueError, TypeError, KeyError, RecursionError):
        return "기록 요약 오류, 체크하여 삭제할 수 있음", "상세를 확인하거나 삭제하세요"


def local_history_time(raw: str) -> str:
    """只改变显示格式；存储和排序继续使用原 UTC 时间。"""
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if moment.utcoffset() is None:
            raise ValueError("기록 시간에 시간대 정보가 없습니다")
        return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, AttributeError, OverflowError, OSError):
        return "시간 형식 오류"
