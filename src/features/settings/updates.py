# 检查 Mirror 酱资源更新并显示版本更新弹窗。
"""MirrorChyan update API integration and update-dialog helpers."""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlencode
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from src.app.constants import NETDISK_DOWNLOAD_LINKS
from src.app.theme import themed_style

UPDATE_FAILURE_MESSAGE = "Mirror酱 업데이트 서비스 요청에 실패했습니다. 잠시 후 다시 시도하세요."
UPDATE_CHECK_TIMEOUT_SECONDS = 5
UPDATE_DOWNLOAD_TIMEOUT_SECONDS = 30
_UPDATE_DOWNLOAD_CHUNK_SIZE = 128 * 1024


class UpdateDownloadCancelled(RuntimeError):
    """Raised when the user cancels an in-app installer download."""


def is_newer_version(remote, current) -> bool:
    def nums(value):
        parts = [int(item) for item in re.findall(r"\d+", str(value))]
        return (parts + [0, 0, 0])[:3]

    return nums(remote) > nums(current)


def mirror_update_request_url(api_url: str, app_version: str, cdk: str = "") -> str:
    """Build the documented Mirror API request without logging the CDK."""
    params = {"current_version": str(app_version).strip()}
    if str(cdk).strip():
        params["cdk"] = str(cdk).strip()
    return f"{api_url}?{urlencode(params)}"


def fetch_update_info(
    api_url: str,
    app_version: str,
    *,
    cdk: str = "",
    timeout: int = UPDATE_CHECK_TIMEOUT_SECONDS,
) -> dict:
    """Read one Mirror resource response.

    ``data.url`` is a short-lived download URL.  It is intentionally kept out
    of account settings and logs; it is only opened when the user explicitly
    requests a download.
    """
    request = urllib.request.Request(
        mirror_update_request_url(api_url, app_version, cdk),
        headers={"User-Agent": f"NTE-Drive-Calc/{app_version}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {
            "has_release": False, "newer": False, "url": "", "message": UPDATE_FAILURE_MESSAGE,
            "error": f"Mirror酱 응답이 유효한 JSON이 아닙니다: {exc}",
        }
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "has_release": False, "newer": False, "url": "", "message": UPDATE_FAILURE_MESSAGE,
            "error": str(exc),
        }

    if not isinstance(payload, dict):
        return {
            "has_release": False, "newer": False, "url": "", "message": UPDATE_FAILURE_MESSAGE,
            "error": "Mirror酱 응답이 객체가 아닙니다.",
        }
    code = payload.get("code")
    if code != 0:
        return {
            "has_release": False,
            "newer": False,
            "url": "",
            "message": str(payload.get("msg") or "Mirror酱이 사용 가능한 업데이트 정보를 반환하지 않았습니다."),
            "error": f"Mirror酱 오류 코드: {code}",
        }
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    latest = str(data.get("version_name") or "").strip()
    if not latest:
        return {
            "has_release": False, "newer": False, "url": "", "message": UPDATE_FAILURE_MESSAGE,
            "error": "Mirror酱 응답에 version_name이 없습니다.",
        }
    return {
        "has_release": True,
        "latest": latest,
        "newer": is_newer_version(latest, app_version),
        "url": str(data.get("url") or "").strip(),
        "release_url": "",
        "message": str(data.get("release_note") or "").strip(),
        "name": latest,
    }


def download_update_installer(
    url: str,
    *,
    destination_dir: str | os.PathLike[str] | None = None,
    timeout: int = UPDATE_DOWNLOAD_TIMEOUT_SECONDS,
    progress_callback=None,
    cancel_check=None,
) -> dict:
    """Download a Mirror installer to a temporary directory.

    The URL returned by Mirror is short-lived, so the file is deliberately not
    stored in account settings.  A partial file is never launched and is
    removed if downloading fails or is cancelled.
    """
    if not str(url).strip().lower().startswith(("https://", "http://")):
        raise ValueError("Mirror 다운로드 주소가 잘못되었습니다.")
    if cancel_check and cancel_check():
        raise UpdateDownloadCancelled("업데이트 설치 파일 다운로드를 취소했습니다.")

    target_dir = Path(destination_dir) if destination_dir else Path(tempfile.mkdtemp(prefix="NTE-Drive-Calc-update-"))
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / "NTE-Drive-Calc-update.exe"
    partial_path = final_path.with_suffix(".exe.part")
    downloaded = 0
    expected = 0
    try:
        request = urllib.request.Request(
            str(url).strip(),
            headers={"User-Agent": "NTE-Drive-Calc updater", "Accept": "application/octet-stream"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            try:
                expected = max(0, int(response.headers.get("Content-Length") or 0))
            except (TypeError, ValueError):
                expected = 0
            if progress_callback:
                progress_callback(0, expected)
            with partial_path.open("wb") as stream:
                while True:
                    if cancel_check and cancel_check():
                        raise UpdateDownloadCancelled("업데이트 설치 파일 다운로드를 취소했습니다.")
                    block = response.read(_UPDATE_DOWNLOAD_CHUNK_SIZE)
                    if not block:
                        break
                    if downloaded == 0 and not block.startswith(b"MZ"):
                        raise RuntimeError("다운로드한 내용이 유효한 Windows 설치 프로그램이 아닙니다.")
                    stream.write(block)
                    downloaded += len(block)
                    if progress_callback:
                        progress_callback(downloaded, expected)
        if downloaded < 2:
            raise RuntimeError("다운로드한 설치 프로그램이 비어 있거나 불완전합니다.")
        if expected and downloaded != expected:
            raise RuntimeError("설치 프로그램 다운로드가 불완전합니다. 다시 다운로드하세요.")
        os.replace(partial_path, final_path)
        return {"path": str(final_path), "downloaded": downloaded, "total": expected}
    except BaseException:
        partial_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise


def update_dialog_link_url(info: dict) -> str:
    return str(info.get("url") or info.get("release_url") or "")


def update_notes_markdown(message: object) -> str:
    """Normalize common release-note shorthand into Qt-compatible Markdown."""

    text = str(message or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return "이 버전에는 업데이트 설명이 없습니다."
    normalized_lines: list[str] = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        # GitHub users often write ``1.修复内容``.  GitHub renders it
        # leniently, while Qt requires a space after the marker to create a
        # separate list item instead of one long paragraph.
        line = re.sub(r"^(\s*\d+)\.(?=\S)", r"\1. ", line)
        line = re.sub(r"^(\s*[-*+])(?=\S)", r"\1 ", line)
        # Qt merges adjacent quote lines into one paragraph.  Keep each
        # support/download call-to-action visibly separate.
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if line.lstrip().startswith(">") and next_line.lstrip().startswith(">"):
            line += "  "
        normalized_lines.append(line)
    return "\n".join(normalized_lines)


def should_show_startup_update(update_config: dict, info: dict) -> bool:
    latest = str(info.get("latest") or "")
    if update_config.get("never_remind"):
        return False
    if latest and update_config.get("ignored_version") == latest:
        return False
    return True


def show_update_dialog(parent, style_sheet: str, info: dict, app_version: str) -> dict:
    latest = str(info.get("latest") or "未知")
    dialog = QDialog(parent)
    dialog.setWindowTitle("업데이트 발견")
    dialog.setMinimumSize(560, 420)
    dialog.setStyleSheet(style_sheet)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)
    title = QLabel(f"새 버전 {latest} 발견")
    title.setStyleSheet("font-size:18px;font-weight:700;color:#58a6ff")
    layout.addWidget(title)
    subtitle = QLabel(f"현재 버전: {app_version}")
    subtitle.setStyleSheet(themed_style("color:#8b949e"))
    layout.addWidget(subtitle)
    notes = QTextBrowser()
    notes.setReadOnly(True)
    notes.setMinimumHeight(220)
    notes.setOpenExternalLinks(True)
    notes.setTextInteractionFlags(Qt.TextBrowserInteraction)
    notes.setMarkdown(update_notes_markdown(info.get("message")))
    layout.addWidget(notes, 1)
    release_url = str(info.get("release_url") or "").strip()
    if release_url:
        link = QLabel(f'GitHub Release: <a href="{release_url}">{release_url}</a>')
        link.setTextFormat(Qt.RichText)
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        link.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(link)
    never_cb = QCheckBox("다시 알리지 않기")
    ignore_cb = QCheckBox("현재 버전은 다시 알리지 않기")
    layout.addWidget(never_cb)
    layout.addWidget(ignore_cb)
    footer = QHBoxLayout()
    footer.addStretch()
    netdisk_button = QPushButton("클라우드 드라이브 다운로드")
    mirror_button = QPushButton("Mirror 다운로드")
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dialog.accept)
    def open_netdisk_download():
        dialog.accept()
        getattr(parent, "_show_netdisk_download_dialog")(NETDISK_DOWNLOAD_LINKS)

    def start_mirror_download():
        dialog.accept()
        getattr(parent, "_start_mirror_download")()

    netdisk_button.clicked.connect(open_netdisk_download)
    mirror_button.clicked.connect(start_mirror_download)
    footer.addWidget(netdisk_button)
    footer.addWidget(mirror_button)
    footer.addWidget(buttons)
    layout.addLayout(footer)
    dialog.exec()
    result = {"changed": False}
    if never_cb.isChecked():
        result["never_remind"] = True
        result["changed"] = True
    if ignore_cb.isChecked():
        result["ignored_version"] = latest
        result["changed"] = True
    return result
