# 构建独立觉醒等级、普通效果选择与共鸣显示控件。
from __future__ import annotations

import re
from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel, QVBoxLayout
from src.services.official_role_awakening_service import render_awaken_effect_description
from src.ui.widgets import NoWheelSpinBox
from .role_calculation import _mark_dirty, _refresh_role_calculations


def _plain_effect_text(value: object) -> str:
    return re.sub(r"<[^>]*>", "", str(value or "")).strip()


def _resonance_threshold(effect: dict) -> int | None:
    match = re.search(r"(?:^|_)(\d+)$", str(effect.get("effect_id") or ""))
    return int(match.group(1)) if match is not None else None


def _build_awakening_group(
    window,
    character_id: int,
    detail: dict,
    editor: dict,
) -> QGroupBox:
    group = QGroupBox("캐릭터 각성")
    group.setObjectName("officialRoleAwakeningGroup")
    layout = QVBoxLayout(group)
    layout.setSpacing(8)
    selected_ids = {
        str(effect_id)
        for effect_id in detail["profile"].get("selected_awaken_effect_ids") or ()
    }
    awakening_level = NoWheelSpinBox()
    awakening_level.setObjectName("officialRoleAwakeningLevel")
    awakening_level.setRange(0, 6)
    awakening_level.setPrefix("각성 레벨:")
    awakening_level.setToolTip(
        "해금된 각성 슬롯 수입니다. 비워 둘 수 있습니다; 3각성과 6각성 공명은 이 레벨 기준으로 활성화됩니다. 휠은 페이지 스크롤에만 사용됩니다."
    )
    awakening_level.setValue(int(detail["profile"].get("awakening_level") or 0))
    layout.addWidget(awakening_level)
    explanation = QLabel(
        "레벨 = 해금된 슬롯; 아래 체크 = 실제 활성화된 일반 효과. 빈 슬롯은 남겨 둘 수 있으며, 3/6각성 공명은 레벨에 따라 활성화됩니다."
    )
    explanation.setWordWrap(True)
    explanation.setStyleSheet("color:#8b949e;")
    layout.addWidget(explanation)
    editor["awakening_level"] = awakening_level
    checks: dict[str, QCheckBox] = {}
    description_labels: list[tuple[dict, QLabel]] = []
    normal_effects = [
        effect
        for effect in detail.get("awakenings") or ()
        if str(effect.get("awaken_type") or "") == "Awaken_Effect"
    ]
    resonance_effects = [
        effect
        for effect in detail.get("awakenings") or ()
        if str(effect.get("awaken_type") or "") == "Awaken_Resonance"
    ]
    for index, effect in enumerate(normal_effects, start=1):
        effect_id = str(effect.get("effect_id") or "")
        title = str(effect.get("title_zh") or f"각성 {index}")
        check = QCheckBox(f"{index}. {title}")
        check.setChecked(effect_id in selected_ids)
        layout.addWidget(check)
        description = QLabel(_plain_effect_text(effect.get("description_zh")) or "효과 설명 없음")
        description.setWordWrap(True)
        description.setContentsMargins(24, 0, 8, 2)
        description.setStyleSheet("color:#8b949e;")
        layout.addWidget(description)
        description_labels.append((effect, description))
        checks[effect_id] = check

    resonance_labels: list[tuple[dict, int, QLabel]] = []
    if resonance_effects:
        resonance_title = QLabel("각성 공명")
        resonance_title.setStyleSheet("font-weight:bold;color:#58a6ff;margin-top:4px;")
        layout.addWidget(resonance_title)
    for effect in resonance_effects:
        threshold = _resonance_threshold(effect)
        if threshold is None:
            continue
        title = str(effect.get("title_zh") or f"{threshold}각성 효과")
        label = QLabel()
        label.setWordWrap(True)
        label.setContentsMargins(8, 0, 8, 0)
        label.setProperty("resonance_title", title)
        resonance_labels.append((effect, threshold, label))
        layout.addWidget(label)

    editor["awakening_checks"] = checks

    def current_profile() -> dict:
        return {
            **detail["profile"],
            "awakening_level": awakening_level.value(),
            "skill_levels": dict(
                editor.get("skill_levels")
                or detail["profile"].get("skill_levels")
                or {}
            ),
            "selected_awaken_effect_ids": [
                effect_id
                for effect_id, check in checks.items()
                if check.isChecked()
            ],
            "awakening_selection_initialized": True,
        }

    def rendered_description(effect: dict) -> str:
        rendered = render_awaken_effect_description(
            effect,
            current_profile(),
            detail.get("awakenings") or (),
        )
        return _plain_effect_text(rendered) or "효과 설명 없음"

    def refresh_descriptions() -> None:
        for effect, label in description_labels:
            label.setText(rendered_description(effect))

    def refresh_resonance() -> None:
        level = awakening_level.value()
        for effect, threshold, label in resonance_labels:
            active = level >= threshold
            state = "활성화됨" if active else f"미활성화({threshold}각 필요)"
            label.setText(
                f"{state}｜{label.property('resonance_title')}\n"
                f"{rendered_description(effect)}"
            )
            label.setStyleSheet(
                "color:#3fb950;font-weight:600;" if active else "color:#8b949e;"
            )

    def selection_changed(*_args) -> None:
        refresh_descriptions()
        refresh_resonance()
        _mark_dirty(window, character_id)
        for callback in tuple(editor.get("awakening_refreshers") or ()):
            callback()
        _refresh_role_calculations(editor)

    def level_changed(*_args) -> None:
        # Lowering capacity clears excess selections deterministically.
        selected = [check for check in checks.values() if check.isChecked()]
        for check in selected[awakening_level.value():]:
            check.blockSignals(True)
            check.setChecked(False)
            check.blockSignals(False)
        selection_changed()

    def effect_changed(*_args) -> None:
        selected_count = sum(check.isChecked() for check in checks.values())
        if selected_count > awakening_level.value():
            awakening_level.setValue(selected_count)
        else:
            selection_changed()

    awakening_level.valueChanged.connect(level_changed)
    for check in checks.values():
        check.toggled.connect(effect_changed)
    editor["refresh_awakening_descriptions"] = refresh_descriptions
    refresh_descriptions()
    refresh_resonance()
    return group
