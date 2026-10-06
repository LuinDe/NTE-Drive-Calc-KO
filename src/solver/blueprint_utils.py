# 提供图纸按具体形状数量去重的通用工具。
from __future__ import annotations

from collections import Counter
from typing import Iterable


def blueprint_piece_signature(blueprint: dict) -> tuple:
    """Keep set qualification distinct from identical total board geometry."""
    set_counts = Counter(str(piece) for piece in blueprint.get("set_pieces") or [])
    extra_counts = Counter(str(piece) for piece in blueprint.get("extra_pieces") or [])
    return (
        str(blueprint.get("set_effect_mode") or ""),
        tuple(sorted(set_counts.items())),
        tuple(sorted(extra_counts.items())),
    )


def dedupe_blueprints_by_piece_signature(blueprints: Iterable[dict]) -> list[dict]:
    seen = set()
    unique = []
    for blueprint in blueprints:
        key = blueprint_piece_signature(blueprint)
        if key in seen:
            continue
        seen.add(key)
        unique.append(blueprint)
    return unique
