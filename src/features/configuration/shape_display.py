# 构造基础权重页的官方额外形状只读展示行。
"""Read-only release-static shape widgets for the basic-weight page."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel


def official_shape_display_rows(window, role_data: dict) -> tuple[tuple[str, QLabel], ...]:
    tooltip = "공식 캐릭터의 추가 형태는 배포 정적 리소스 라이브러리에서 제공되며 기본 가중치 페이지에서 편집할 수 없습니다."
    shape_label = QLabel(str(role_data.get("extra_shape_label") or "정적 리소스 라이브러리에서 제공하지 않음"))
    shape_label.setToolTip(tooltip)

    property_labels = getattr(window, "_config_weight_property_labels", {}) or {}
    rows = []
    for property_id, raw_value in (role_data.get("extra_shape_buffs") or {}).items():
        label = str(property_labels.get(property_id) or property_id)
        rows.append(f"{label}: {float(raw_value):g}")
    bonus = QLabel("、".join(rows) if rows else "정적 리소스 라이브러리에서 제공하지 않음")
    bonus.setToolTip(tooltip)
    return (("추가 형태 태그", shape_label), ("추가 형태 보너스", bonus))
