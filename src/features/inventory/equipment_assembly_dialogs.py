# 生成游戏界面自动装配完成或未完成时的汇总对话框内容。
"""Presentation helpers for equipment-assembly confirmation reports."""

from __future__ import annotations


def assembly_report_dialog(
    action_name: str,
    report,
    expected_role_count: int | None = None,
):
    role_count = len(getattr(report, "role_reports", []) or [])
    action_count = getattr(report, "executed_actions", 0)
    missing = list(getattr(report, "missing_roles", []) or [])
    skipped = list(getattr(report, "skipped_roles", []) or [])
    duplicates = list(getattr(report, "duplicate_roles", []) or [])
    unrecognized = list(getattr(report, "unrecognized_roles", []) or [])
    verification_failures = list(
        getattr(report, "verification_failures", []) or []
    )
    incomplete = bool(
        missing or skipped or duplicates or verification_failures
    )
    if expected_role_count is not None and role_count < expected_role_count:
        incomplete = True
    if role_count == 0:
        incomplete = True

    title = f"{action_name} 미완료" if incomplete else f"{action_name} 완료"
    lines = [
        f"캐릭터 {role_count}명을 장착했고 동작 {action_count}개를 실행했습니다."
    ]
    if expected_role_count is not None and role_count < expected_role_count:
        lines.append(
            f"캐릭터 {expected_role_count}명 장착 예정이며,"
            f"{expected_role_count - role_count}명이 미완료입니다."
        )
    if missing:
        lines.append("찾지 못한 캐릭터:" + "、".join(str(role) for role in missing))
    if skipped:
        lines.append("건너뛴 캐릭터:" + "、".join(str(role) for role in skipped))
    if duplicates:
        lines.append(f"중복 인식된 캐릭터 슬롯: {len(duplicates)}개.")
    if unrecognized:
        lines.append(f"미인식 캐릭터 슬롯: {len(unrecognized)}개.")
        for entry in unrecognized:
            if not isinstance(entry, dict):
                lines.append(f"- {entry}")
                continue
            if entry.get("roster_index") is not None:
                position = f"{int(entry['roster_index']) + 1}번째 캐릭터"
            elif (
                entry.get("page_index") is not None
                and entry.get("slot_index") is not None
            ):
                position = (
                    f"{int(entry['page_index']) + 1}페이지"
                    f"{int(entry['slot_index']) + 1}번째 캐릭터"
                )
            else:
                position = "알 수 없는 위치"
            raw_text = (
                str(entry.get("raw_text") or "").strip() or "읽어낸 문자 없음"
            )
            lines.append(f"- {position} (OCR: {raw_text})")
    duplicate_missing: list[tuple[str, str, int | None]] = []
    if verification_failures:
        lines.append(
            f"청사진 스크린샷 검증 실패: 캐릭터 {len(verification_failures)}명."
        )
        ordinary_missing: list[tuple[str, str]] = []
        empty_screenshots: list[str] = []
        for failure in verification_failures:
            if not isinstance(failure, dict):
                continue
            role_name = str(failure.get("role_name") or "알 수 없는 캐릭터")
            if failure.get("reason") == "empty_screenshot":
                empty_screenshots.append(role_name)
                continue
            for item in failure.get("missing_blocks") or []:
                if (
                    not isinstance(item, dict)
                    or item.get("block_id") is None
                ):
                    continue
                block_id = str(item["block_id"])
                if item.get("is_duplicate_drive"):
                    duplicate_missing.append(
                        (
                            role_name,
                            block_id,
                            int(item["duplicate_count"])
                            if item.get("duplicate_count") is not None
                            else None,
                        )
                    )
                else:
                    ordinary_missing.append((role_name, block_id))
        for role_name, block_id, duplicate_count in duplicate_missing:
            count_text = (
                f"(같은 조건의 드라이브 총 {duplicate_count}개)"
                if duplicate_count
                else ""
            )
            lines.append(
                f"- {role_name}: 중복 드라이브 모듈 #{block_id}{count_text}의 장착을 확인하지 못했습니다."
            )
        for role_name, block_id in ordinary_missing:
            lines.append(
                f"- {role_name}: 드라이브 모듈 #{block_id}이(가) 스크린샷 검증을 통과하지 못했습니다."
            )
        for role_name in empty_screenshots:
            lines.append(f"- {role_name}: 게임 스크린샷을 얻지 못해 청사진 결과를 확인할 수 없습니다.")
        if duplicate_missing:
            lines.append(
                "원인: 이 드라이브들은 게임 필터에서 형태·품질·스탯 조건이 같아"
                "자동 장착이 대상을 유일하게 찾을 수 없어, 같은 종류의 드라이브를 잘못 장착하거나 빈자리가 남을 수 있습니다."
            )
            lines.append(
                "처리: 게임 안에서 위 드라이브를 직접 보충 장착하세요."
                "또는 중복 드라이브의 레벨, 잠금, 폐기 상태를 서로 다르게 만든 뒤 다시 시도하세요."
            )
    if incomplete and (missing or skipped or duplicates):
        lines.append("캐릭터 인식 결과를 확인한 뒤 다시 실행하세요.")
    elif incomplete and verification_failures and not duplicate_missing:
        lines.append("게임 화면이 안정적인지, 청사진 위치가 올바른지 확인한 뒤 다시 실행하세요.")
    elif unrecognized:
        lines.append("나머지 미인식 슬롯은 이번 대상 캐릭터가 아니므로 이번 장착 결과에 영향을 주지 않습니다.")
    return title, "\n".join(lines), not incomplete

