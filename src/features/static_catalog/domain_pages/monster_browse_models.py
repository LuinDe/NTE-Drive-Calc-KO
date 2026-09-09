# 怪物与玩法页面的不可变浏览状态和正式记录键解析。
"""Qt-free browse models and typed-key helpers for the monster page."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import unquote

from src.services.static_catalog_monster_service import CatalogEntry


PLAY_LABELS = {
    "official_illustrated": "오픈 월드 도감",
    "feast": "争锋赏宴",
    "outer_realm": "轨外之境",
    "clone": "재료·육성 던전",
    "world_boss": "异象追猎",
    "high_risk": "정식 몬스터 풀이 있는 고위험 의뢰",
}
PLAY_COPY = {
    "official_illustrated": "지역별로 오픈 월드 적을 둘러보고 약점과 전투 속성을 확인합니다.",
    "feast": "먼저 현재 또는 지난 이벤트를 선택한 뒤 해당 기의 도전, 난이도, 적 프로필을 둘러봅니다.",
    "outer_realm": "전체 정식 구성을 둘러봅니다. 일정이 없는 구성도 층과 하프 기준으로 적을 표시합니다.",
    "clone": "재료, 육성, 이벤트 던전으로, 난이도별 등장 적을 확인할 수 있습니다.",
    "world_boss": "이상 현상 사냥 대상과 레벨별 전투 속성을 둘러봅니다.",
    "high_risk": "난이도별 등장 적이 명확한 고위험 의뢰만 수록합니다.",
}


@dataclass(frozen=True, slots=True)
class BrowseCard:
    title: str
    subtitle: str
    badge: str
    icon: Path | None
    action: Callable[[], None] | None
    formal_id: str = ""
    unavailable: bool = False
    category: str = ""
    difficulty: str = ""
    region: str = ""
    period: str = ""


@dataclass(frozen=True, slots=True)
class BrowseSection:
    title: str
    note: str
    cards: tuple[BrowseCard, ...]
    initial_limit: int = 0


@dataclass(frozen=True, slots=True)
class BrowseState:
    title: str
    subtitle: str
    sections: tuple[BrowseSection, ...]


def group_entries(
    entries: Iterable[CatalogEntry], key: Callable[[CatalogEntry], object],
) -> dict[str, tuple[CatalogEntry, ...]]:
    rows: dict[str, list[CatalogEntry]] = defaultdict(list)
    for entry in entries:
        rows[str(key(entry))].append(entry)
    return {name: tuple(values) for name, values in rows.items()}


def key_parts(key: str) -> tuple[str, ...]:
    return tuple(unquote(part) for part in key.split("|"))


def profile_parts(key: str) -> tuple[str, str] | None:
    parts = key_parts(key)
    return (
        (parts[1], parts[2])
        if len(parts) == 3 and parts[0] == "profile_monster"
        else None
    )


def object_name(path: str) -> str:
    name = str(path).rsplit(".", 1)[-1]
    return name[:-2] if name.endswith("_C") else name


def period_label(config_id: str) -> str:
    ordinal = str(config_id).rsplit("_", 1)[-1]
    return f"{ordinal}기" if ordinal.isdigit() else "정식 기수"


def home_badge(mode: str, entries: Iterable[CatalogEntry]) -> str:
    """Summarize one home category without duplicating records in the view."""

    if mode == "outer_realm":
        count = len({entry.primary_id for entry in entries})
        return f"정식 구성 {count}기"
    labels = {
        "official_illustrated": "종의 오픈 월드 적",
        "feast": "개의 도전 대상",
        "clone": "개의 던전",
        "world_boss": "개의 사냥 대상",
        "high_risk": "건의 고위험 의뢰",
    }
    return f"{len({entry.primary_id for entry in entries})} {labels[mode]}"
