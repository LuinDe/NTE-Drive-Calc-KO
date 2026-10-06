# 汇总扫描后管理动作结果。
"""Text projection for completed scan state-management results."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from src.app.window_geometry import fit_dialog_to_available_screen


def append_scan_post_action_summary(summary: str, stats: Mapping[str, Any]) -> str:
    """Render the existing management summary and append any skipped-row notice."""

    if not stats.get("post_actions_enabled"):
        return summary
    planned = "계획: " if stats.get("post_action_issue_count") else ""
    summary += (
        "\n스캔 후 관리:"
        f"계산 참여 {int(stats.get('post_action_candidate_count', 0) or 0)}개,"
        f"대상 변경 {int(stats.get('post_action_target_count', 0) or 0)}개,"
        f"처리 완료 {int(stats.get('post_action_applied_count', 0) or 0)}개."
        f"\n{planned}폐기 {int(stats.get('discard_set_count', 0) or 0)}개,"
        f"폐기 취소 {int(stats.get('discard_clear_count', 0) or 0)}개;"
        f"잠금 {int(stats.get('lock_set_count', 0) or 0)}개,"
        f"잠금 취소 {int(stats.get('lock_clear_count', 0) or 0)}개."
    )
    filtered_parts = []
    for key, label in (
        ("post_action_quality_filtered_count", "품질 범위 필터"),
        ("post_action_type_filtered_count", "처리 종류 필터"),
        ("post_action_type_range_filtered_count", "유형 범위 필터"),
    ):
        count = int(stats.get(key, 0) or 0)
        if count:
            filtered_parts.append(f"{label} {count}개")
    if filtered_parts:
        summary += "\n" + ",".join(filtered_parts) + "."
    return append_post_action_issue_summary(summary, stats)


def append_post_action_issue_summary(summary: str, stats: Mapping[str, Any]) -> str:
    """Keep the notice compact; individual scan indexes belong in details."""

    issue_count = int(stats.get("post_action_issue_count", 0) or 0)
    if not issue_count:
        return summary
    applied = int(stats.get("post_action_applied_count", 0) or 0)
    return (
        f"{summary}\n상태 관리 종료: 성공 {applied}개, 건너뜀 {issue_count}개. "
        "건너뛴 장비는 상태를 변경하지 않았습니다. 상세 정보를 확인하세요."
    )


def post_action_issue_details(stats: Mapping[str, Any]) -> str:
    """Describe only the frozen plan and the pre-action failure."""

    actions = {"locked": "잠금", "discarded": "폐기", "normal": "일반 상태 복원"}
    reasons = {
        "identity_mismatch": "상세 정보가 스캔 스크린샷과 일치하지 않음",
        "state_mismatch": "현재 상태가 계획과 일치하지 않음",
    }
    return "\n".join(
        f"{int(issue['index'])}번째 항목: {reasons.get(issue['reason'], '操作前校验未通过')}, "
        f"원래 계획한 {actions.get(issue['target_state'], '状态修改')} 작업을 건너뛰었습니다."
        for issue in stats.get("post_action_issues", ()) or ()
    )


def show_scan_completion(
    parent: QWidget | None, title: str, summary: str, stats: Mapping[str, Any],
) -> None:
    """Use the existing completion notice, with one warning when rows were skipped."""

    if not int(stats.get("post_action_issue_count", 0) or 0):
        QMessageBox.information(parent, title, summary)
        return
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(summary)
    box.setDetailedText(post_action_issue_details(stats))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.setDefaultButton(QMessageBox.StandardButton.Ok)
    box.adjustSize()
    fit_dialog_to_available_screen(box)
    QApplication.beep()
    box.exec()
