# 展示静态规则推算的 Buff 区间及其逐击覆盖，不冒充运行时实测。
"""Presentation-only summary for inferred battle Buff intervals."""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_report import BattleAnalysisSnapshot
from src.features.battle_report.analysis_components import analysis_table
from src.features.battle_report.marginal_quantification_view import damage_coverage_text
from src.services.battle_buff_attribute_projection_service import (
    normalize_battle_buff_property_id,
)
from src.services.battle_buff_counterfactual_plan_service import (
    battle_buff_counterfactual_key,
)
from src.services.battle_hit_buff_explanation_service import (
    battle_buff_property_label,
    format_battle_buff_value,
)


_SCOPE_LABELS = {
    "self": "자신",
    "team": "팀 전체",
    "team_others": "다른 팀원",
    "target": "적 대상",
    "unknown": "적용 대상 확인 필요",
}


def _trigger_label(value: str) -> str:
    raw = str(value or "")
    normalized = raw.casefold()
    rules = (
        (("whole_battle", "battle_condition", "equipped"), "전투 전체 상시"),
        (("change_role_in", "role_in", "appear"), "교체 투입/등장"),
        (("change_role_out", "role_out"), "교체 이탈/퇴장"),
        (("qte",), "QTE"),
        (("ultra", "q_begin", "q_action"), "Q 시작"),
        (("skill", "e_begin", "e_action"), "E 시작"),
        (("treatment", "cure", "heal"), "치료 발동"),
        (("target_toppled", "unbalance"), "대상 브레이크"),
        (("dark_star", "nova"), "노바 정산"),
        (("damage_after_hit", "after_hit"), "피해를 준 후"),
        (("normalattack", "melee"), "일반 공격"),
    )
    return next(
        (label for needles, label in rules if any(item in normalized for item in needles)),
        raw or "발동 확인 필요",
    )


def _modifier_cells(intervals: list) -> tuple[str, str]:
    first = intervals[0]
    max_stacks = max(
        (max(1, int(getattr(row, "stacks", 1))) for row in intervals),
        default=1,
    )
    labels: list[str] = []
    values: list[str] = []
    for modifier in getattr(first, "modifiers", ()):
        property_id = normalize_battle_buff_property_id(modifier.property_id)
        labels.append(battle_buff_property_label(property_id))
        if modifier.magnitude_value is not None:
            per_stack = float(modifier.magnitude_value)
            if max_stacks > 1 or int(getattr(first, "stack_limit_count", 1)) > 1:
                values.append(
                    f"중첩당 {format_battle_buff_value(property_id, per_stack)}"
                    f" × {max_stacks}중첩"
                )
            else:
                values.append(format_battle_buff_value(property_id, per_stack))
        else:
            values.append("공식 미분석")
    if not labels:
        return "속성 미분석", "—"
    return "\n".join(labels), "\n".join(values)


def _gain_percent(counterfactual) -> str:
    if counterfactual is None:
        return "—"
    status = counterfactual.quantification.status
    if status == "not_applicable":
        return "+0.00%"
    if status == "complete":
        value = counterfactual.gain_percent
        return "—" if value is None else f"{value:+.2f}%"
    if status == "partial":
        value = counterfactual.quantified_gain_percent
        return "—" if value is None else f"{value:+.2f}% (일부)"
    return "—"


def _optional_number(value: float | None, format_spec: str) -> str:
    return "—" if value is None else format(value, format_spec)


def _counterfactual_detail(counterfactual) -> str:
    if counterfactual is None:
        return "현재 분석 결과에는 아직 버프별 제거 반사실이 생성되지 않았습니다."
    gaps = "\n".join(
        f"- {gap.explanation}" for gap in counterfactual.quantification.gaps
    )
    partial_note = (
        "\n일부 수치는 전체 버프 이득이나 이득 하한을 뜻하지 않습니다."
        if counterfactual.quantification.status == "partial" else ""
    )
    return (
        f"현재 구간 유효 피해: {counterfactual.baseline_damage:,.2f}\n"
        f"피해 커버리지: {damage_coverage_text(counterfactual.damage_coverage)}\n"
        f"적용 히트: {counterfactual.affected_hits:,}\n"
        f"계산 가능 히트: {counterfactual.quantified_hits:,}\n"
        f"정량화 상태: {counterfactual.quantification.status}\n"
        f"제거 후 피해: {_optional_number(counterfactual.without_buff_damage, ',.2f')}\n"
        f"전체 피해 증가량: {_optional_number(counterfactual.damage_gain, '+,.2f')}\n"
        f"일부 피해 증가량: {_optional_number(counterfactual.quantified_damage_gain, '+,.2f')}\n"
        f"방법: {counterfactual.method}\n"
        f"신뢰도: {counterfactual.confidence}\n"
        f"설명: {counterfactual.explanation}"
        + partial_note
        + (f"\n미정량화 누락 항목:\n{gaps}" if gaps else "")
    )


def _interval_detail(rows: list, counterfactual) -> str:
    first = rows[0]
    interval_lines = "\n".join(
        f"- {row.start_us / 1_000_000:.3f}s—{row.end_us / 1_000_000:.3f}s;"
        f"{getattr(row, 'stacks', 1)}중첩; 상태 {row.state_confidence} / 수치 {row.value_confidence}"
        for row in rows
    )
    modifier_lines = "\n".join(
        f"- {modifier.property_id}; 값 {modifier.magnitude_value!r};"
        f"Calculation {modifier.calculation_asset_path or '无'}"
        for modifier in getattr(first, "modifiers", ())
    ) or "- 추출된 속성 보정 없음"
    return (
        f"버프 패키지: {first.buff_name}\n"
        f"출처 정의: {getattr(first, 'source_effect_definition_id', '未知')}\n"
        f"에셋: {getattr(first, 'buff_asset_path', '未知')}\n"
        f"출처 유형: {getattr(first, 'source_kind', '未知')}\n"
        f"원본 트리거: {first.trigger_event_type}\n"
        f"지속 정책: {getattr(first, 'duration_policy', '未知')}\n"
        f"추론 근거: {first.inference_basis}\n\n"
        f"구간:\n{interval_lines}\n\n속성 근거:\n{modifier_lines}\n\n"
        f"반사실:\n{_counterfactual_detail(counterfactual)}"
    )


class _BuffDetailDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("버프 상세")
        root = QVBoxLayout(self)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        root.addWidget(self.detail)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.hide)
        root.addWidget(buttons)
        fit_dialog_to_available_screen(self, QSize(920, 720))

    def show_detail(self, text: str) -> None:
        self.detail.setPlainText(text)
        self.detail.moveCursor(QTextCursor.MoveOperation.Start)
        self.show()
        self.raise_()


class BattleBuffEvidencePanel(QWidget):
    """Render one decision-oriented row per stable Buff package."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary_label = QLabel()
        self.summary_label.hide()
        self.table = analysis_table(
            (
                "출처", "적용 대상", "Buff", "값", "발동", "시간 커버리지",
                "적용 히트", "피해 커버리지", "이득률", "상세",
            ),
            260,
            default_widths=(130, 105, 190, 190, 135, 190, 90, 145, 115, 70),
        )
        self.table.cellClicked.connect(self._cell_clicked)
        layout.addWidget(self.table)
        self._detail_dialog: _BuffDetailDialog | None = None

    def clear(self) -> None:
        self.summary_label.setText("현재 사용할 수 있는 버프 추정이 없습니다.")
        self.table.setRowCount(0)

    def render(self, analysis: BattleAnalysisSnapshot) -> None:
        intervals = analysis.buff_intervals
        if not intervals:
            self.clear()
            return
        groups: dict[str, list] = defaultdict(list)
        for interval in intervals:
            groups[battle_buff_counterfactual_key(interval)].append(interval)
        counterfactuals = {
            row.buff_key: row
            for row in getattr(analysis, "buff_counterfactuals", ())
        }
        ordered = sorted(
            groups.items(),
            key=lambda item: (item[1][0].source_character_name, item[1][0].buff_name),
        )
        self.table.setRowCount(len(ordered))
        total_duration = max(
            0.0,
            (analysis.range_end_us - analysis.range_start_us) / 1_000_000,
        )
        for row_index, (key, rows) in enumerate(ordered):
            first = rows[0]
            counterfactual = counterfactuals.get(key)
            duration = (
                counterfactual.coverage_seconds
                if counterfactual is not None
                else sum(
                    max(
                        0,
                        min(row.end_us, analysis.range_end_us)
                        - max(row.start_us, analysis.range_start_us),
                    )
                    for row in rows
                ) / 1_000_000
            )
            property_text, value_text = _modifier_cells(rows)
            coverage_percent = duration / total_duration * 100.0 if total_duration else 0.0
            detail = _interval_detail(rows, counterfactual)
            values = (
                first.source_character_name,
                _SCOPE_LABELS.get(first.target_scope, first.target_scope),
                property_text,
                value_text,
                " / ".join(dict.fromkeys(_trigger_label(row.trigger_event_type) for row in rows)),
                f"{duration:.3f}s / {total_duration:.3f}s = {coverage_percent:.1f}%",
                "—" if counterfactual is None else f"{counterfactual.affected_hits:,}",
                "—" if counterfactual is None else damage_coverage_text(counterfactual.damage_coverage),
                _gain_percent(counterfactual),
                "보기",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(detail)
                if column == 9:
                    item.setData(Qt.ItemDataRole.UserRole, detail)
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)
                self.table.setItem(row_index, column, item)
        self.table.resizeRowsToContents()

    def _cell_clicked(self, row: int, column: int) -> None:
        if column != 9:
            return
        item = self.table.item(row, column)
        detail = "" if item is None else str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not detail:
            return
        if self._detail_dialog is None:
            self._detail_dialog = _BuffDetailDialog(self)
        self._detail_dialog.show_detail(detail)
