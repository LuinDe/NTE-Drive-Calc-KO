# 从 MainWindow 抽离的更新控制器方法。
"""Compatibility-installed MirrorChyan update controller."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)

from src.app.constants import (
    APP_VERSION,
    BILIBILI_HOME_URL,
    DISCORD_GROUP_URL,
    GROUP_CHAT_DEVELOPER_HINT,
    GROUP_CHAT_DISCORD_HINT,
    GITHUB_HOME_URL,
    GITHUB_LATEST_RELEASE_URL,
    GITHUB_RELEASES_URL,
    MIRROR_PROJECT_URL,
    MIRROR_UPDATE_API,
    QQ_GROUP_NUMBER,
    SUPPORT_US_URL,
)
from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.app.workers import WorkerThread
from src.observability.context import OperationContext
from src.observability.operation import log_event
from src.features.settings.updates import (
    download_update_installer,
    fetch_update_info,
    is_newer_version,
    should_show_startup_update,
    show_update_dialog,
)
from src.utils.logger import logger


def _new_update_operation(
    self: Any,
    *,
    feature: str = "update",
) -> OperationContext:
    app_context = getattr(self, "app_context", None)
    return OperationContext.create(
        feature,
        account_id=(
            app_context.account.active_account_id
            if app_context is not None
            else None
        ),
        context_generation=(
            app_context.generation if app_context is not None else None
        ),
    )


class _MirrorInstallerDownloadWorker(WorkerThread):
    progress = Signal(int, int)

    def __init__(self, url: str, parent: QObject | None = None) -> None:
        self._url = url
        self._cancel_event = threading.Event()
        super().__init__(target=self._download, parent=parent)

    def cancel(self) -> None:
        self._cancel_event.set()

    def _download(self):
        return download_update_installer(
            self._url,
            progress_callback=lambda current, total: self.progress.emit(current, total),
            cancel_check=self._cancel_event.is_set,
        )


def _maybe_check_updates_on_startup(self):
    if self._update_config.get("never_remind"):
        return
    def check_when_idle():
        if QApplication.activeModalWidget() is not None:
            QTimer.singleShot(1200, check_when_idle)
        else:
            self._check_updates(manual=False)
    QTimer.singleShot(1200, check_when_idle)


def _mirror_cdk_value(self):
    editor = getattr(self, "_mirror_cdk_edit", None)
    if editor is not None:
        return editor.text().strip()
    return str(self._update_config.get("mirror_cdk") or "").strip()


def _save_mirror_cdk(self):
    cdk = self._mirror_cdk_value()
    if self._update_config.get("mirror_cdk") != cdk:
        self._update_config["mirror_cdk"] = cdk
        self._save_update_config()
    return cdk


def _check_updates(self, manual=True):
    update_worker = getattr(self, "_update_worker", None)
    if update_worker is not None and update_worker.isRunning():
        if manual:
            self._update_status.setText("업데이트를 확인하는 중…")
        return
    self._update_check_manual = manual
    self._update_operation_context = _new_update_operation(self)
    log_event(
        "INFO",
        "update.check_started",
        "업데이트 확인 시작",
        self._update_operation_context,
        trigger="manual" if manual else "startup",
        current_version=APP_VERSION,
    )
    if manual:
        self._check_update_btn.setEnabled(False)
        self._update_status.setText("Mirror酱을 통해 업데이트를 확인하는 중…")
    self._update_worker = WorkerThread(
        target=self._fetch_update_info, parent=self,
    )
    self._update_worker.result_ready.connect(self._on_update_checked)
    self._update_worker.error.connect(self._on_update_error)
    self._update_worker.start()


def _fetch_update_info(self, cdk=""):
    info = fetch_update_info(MIRROR_UPDATE_API, APP_VERSION, cdk=cdk)
    if info.get("has_release"):
        info["release_url"] = GITHUB_RELEASES_URL
    return info


def _on_update_checked(self, info):
    manual = getattr(self, "_update_check_manual", True)
    operation = getattr(
        self, "_update_operation_context", _new_update_operation(self)
    )
    log_event(
        "INFO",
        "update.check_succeeded",
        "업데이트 확인 완료",
        operation,
        trigger="manual" if manual else "startup",
        has_release=bool(info.get("has_release")),
        newer=bool(info.get("newer")),
        latest_version=info.get("latest"),
    )
    if manual:
        self._check_update_btn.setEnabled(True)
    if not info.get("has_release"):
        message = info.get("message") or "Mirror酱이 사용 가능한 업데이트 정보를 반환하지 않았습니다."
        self._update_status.setText(message)
        if manual:
            self._show_update_failure_netdisk_prompt(info.get("error", message))
        return
    latest = info.get("latest") or "未知"
    if info.get("newer"):
        self._update_status.setText(f"새 버전 발견: {latest} (현재 {APP_VERSION})")
        if manual or self._should_show_startup_update(info):
            if manual:
                self._show_update_dialog(info, manual=True)
            else:
                def show_when_idle():
                    try:
                        if not self.isVisible():
                            return
                    except RuntimeError:
                        return
                    if QApplication.activeModalWidget() is not None:
                        QTimer.singleShot(1200, show_when_idle)
                    else:
                        self._show_update_dialog(info, manual=False)
                show_when_idle()
    else:
        self._update_status.setText(f"이미 최신 버전입니다: {APP_VERSION}")
        if manual:
            QMessageBox.information(
                self, "업데이트 확인",
                f"이미 최신 버전입니다.\n현재 버전: {APP_VERSION}\n최신 버전: {latest}",
            )


def _on_update_error(self, err):
    manual = getattr(self, "_update_check_manual", True)
    operation = getattr(
        self, "_update_operation_context", _new_update_operation(self)
    )
    log_event(
        "ERROR",
        "update.check_failed",
        "업데이트 확인 실패",
        operation,
        trigger="manual" if manual else "startup",
        error=err,
    )
    message = "Mirror酱 업데이트 서비스 요청에 실패했습니다. 잠시 후 다시 시도하세요."
    if manual:
        self._check_update_btn.setEnabled(True)
        self._update_status.setText(message)
        self._show_update_failure_netdisk_prompt(err)
    else:
        if hasattr(self, "_update_status"):
            self._update_status.setText(message)
        logger.warning("자동 업데이트 확인 시작 실패: {}", err)


def _start_mirror_download(self):
    download_worker = getattr(self, "_mirror_download_worker", None)
    if download_worker is not None and download_worker.isRunning():
        return
    cdk = self._save_mirror_cdk()
    if not cdk:
        _show_mirror_cdk_required_dialog(self)
        editor = getattr(self, "_mirror_cdk_edit", None)
        if editor is not None:
            editor.setFocus()
        return
    self._mirror_download_operation_context = _new_update_operation(
        self, feature="update_download"
    )
    log_event(
        "INFO",
        "update.download_request_started",
        "Mirror 다운로드 요청 시작",
        self._mirror_download_operation_context,
        cdk_present=True,
    )
    self._mirror_download_btn.setEnabled(False)
    self._update_status.setText("Mirror酱에 다운로드 주소를 요청하는 중…")
    self._mirror_download_worker = WorkerThread(
        target=lambda: self._fetch_update_info(cdk), parent=self,
    )
    self._mirror_download_worker.result_ready.connect(self._on_mirror_download_ready)
    self._mirror_download_worker.error.connect(
        lambda error: self._on_mirror_download_ready({"error": str(error)})
    )
    self._mirror_download_worker.start()


def _on_mirror_download_ready(self, info):
    operation = getattr(
        self,
        "_mirror_download_operation_context",
        _new_update_operation(self, feature="update_download"),
    )
    url = str(info.get("url") or "").strip()
    latest = str(info.get("latest") or "").strip()
    if url and not latest:
        url = ""
    if url:
        if not _mirror_download_version_is_available(latest, APP_VERSION):
            log_event(
                "WARNING",
                "update.download_historical_version_confirmation_required",
                "Mirror 다운로드 버전이 현재 버전보다 낮아 사용자 확인 대기 중",
                operation,
                current_version=APP_VERSION,
                latest_version=latest,
            )
            if not _confirm_mirror_older_version_download(self, APP_VERSION, latest):
                log_event(
                    "INFO", "update.download_historical_version_cancelled",
                    "사용자가 이전 버전 다운로드를 취소함", operation,
                    current_version=APP_VERSION, latest_version=latest,
                )
                if hasattr(self, "_mirror_download_btn"):
                    self._mirror_download_btn.setEnabled(True)
                self._update_status.setText("이전 버전 다운로드를 취소했습니다.")
                return
            log_event(
                "WARNING", "update.download_historical_version_confirmed",
                "사용자가 이전 버전 다운로드를 확인함", operation,
                current_version=APP_VERSION, latest_version=latest,
            )
        log_event(
            "INFO",
            "update.download_url_received",
            "Mirror 다운로드 주소를 받았습니다",
            operation,
            has_download_url=True,
        )
        self._start_mirror_installer_download(url)
        return
    log_event(
        "ERROR",
        "update.download_request_failed",
        "Mirror 다운로드 주소를 받지 못했습니다",
        operation,
        error=info.get("message") or info.get("error"),
    )
    if hasattr(self, "_mirror_download_btn"):
        self._mirror_download_btn.setEnabled(True)
    self._update_status.setText("Mirror 다운로드 주소를 받지 못했습니다. 프로젝트 페이지에서 다운로드를 시도할 수 있습니다.")
    _show_mirror_project_download_dialog(
        self,
        "Mirror 다운로드 주소를 받지 못했습니다. CDK가 유효하고 다운로드할 수 있는 새 버전이 있는지 확인하세요."
        "그래도 다운로드할 수 없으면 아래 프로젝트 페이지에서 다운로드를 시도할 수 있습니다.",
    )


def _start_mirror_installer_download(self, url):
    """Download and launch the installer without sending the user to a browser."""
    self._update_status.setText("Mirror酱을 통해 업데이트 설치 프로그램을 다운로드하는 중…")
    progress = QProgressDialog("업데이트 설치 프로그램을 다운로드하는 중…", "취소", 0, 0, self)
    progress.setWindowTitle("Mirror 다운로드")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.setMinimumDuration(0)
    progress.show()
    self._mirror_download_progress_dialog = progress
    worker = _MirrorInstallerDownloadWorker(str(url), parent=self)
    self._mirror_installer_download_worker = worker
    progress.canceled.connect(worker.cancel)
    worker.progress.connect(self._on_mirror_download_progress)
    worker.result_ready.connect(self._on_mirror_installer_downloaded)
    worker.error.connect(self._on_mirror_installer_download_error)
    worker.start()


def _on_mirror_download_progress(self, downloaded, total):
    progress = getattr(self, "_mirror_download_progress_dialog", None)
    if progress is None:
        return
    if total > 0:
        progress.setRange(0, total)
        progress.setValue(min(downloaded, total))
        progress.setLabelText(f"업데이트 설치 프로그램을 다운로드하는 중… {downloaded / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB")
    else:
        progress.setRange(0, 0)
        progress.setLabelText(f"업데이트 설치 프로그램을 다운로드하는 중… {downloaded / 1024 / 1024:.1f} MB")


def _finish_mirror_download_ui(self):
    progress = getattr(self, "_mirror_download_progress_dialog", None)
    if progress is not None:
        progress.close()
        progress.deleteLater()
    self._mirror_download_progress_dialog = None
    if hasattr(self, "_mirror_download_btn"):
        self._mirror_download_btn.setEnabled(True)


def _on_mirror_installer_downloaded(self, result):
    self._finish_mirror_download_ui()
    operation = getattr(
        self,
        "_mirror_download_operation_context",
        _new_update_operation(self, feature="update_download"),
    )
    path = Path(str((result or {}).get("path") or ""))
    if not path.is_file():
        self._on_mirror_installer_download_error("설치 프로그램 다운로드 완료 후 파일을 찾지 못했습니다.")
        return
    log_event(
        "INFO",
        "update.download_succeeded",
        "Mirror 설치 프로그램 다운로드 완료",
        operation,
        installer_name=path.name,
    )
    self._update_status.setText("설치 프로그램 다운로드가 완료되어 자동으로 시작하는 중…")
    QTimer.singleShot(150, lambda: self._launch_mirror_installer(str(path)))


def _on_mirror_installer_download_error(self, error):
    self._finish_mirror_download_ui()
    message = str(error or "설치 프로그램 다운로드에 실패했습니다. 잠시 후 다시 시도하세요.")
    operation = getattr(
        self,
        "_mirror_download_operation_context",
        _new_update_operation(self, feature="update_download"),
    )
    if "업데이트 설치 파일 다운로드를 취소했습니다" in message:
        log_event(
            "WARNING",
            "update.download_cancelled",
            "사용자가 Mirror 다운로드 취소",
            operation,
        )
        self._update_status.setText("Mirror 다운로드를 취소했습니다.")
        return
    log_event(
        "ERROR",
        "update.download_failed",
        "Mirror 설치 프로그램 다운로드 실패",
        operation,
        error=message,
    )
    self._update_status.setText("Mirror 다운로드에 실패했습니다. 프로젝트 페이지에서 다운로드를 시도할 수 있습니다.")
    _show_mirror_project_download_dialog(
        self,
        "설치 프로그램 다운로드 또는 시작에 실패했습니다. 잠시 후 다시 시도하고, 계속 실패하면 아래 프로젝트 페이지에서 다운로드를 시도하세요.",
    )


def _launch_mirror_installer(self, path):
    installer = Path(path)
    if not installer.is_file():
        self._on_mirror_installer_download_error("설치 프로그램 파일이 없습니다.")
        return
    try:
        subprocess.Popen([str(installer)], cwd=str(installer.parent))
    except OSError as exc:
        self._on_mirror_installer_download_error(str(exc))
        return
    self._update_status.setText("설치 프로그램이 시작되어 현재 프로그램이 곧 종료됩니다.")
    operation = getattr(
        self,
        "_mirror_download_operation_context",
        _new_update_operation(self, feature="update_download"),
    )
    log_event(
        "INFO",
        "update.installer_launched",
        "Mirror 업데이트 설치 프로그램 시작됨",
        operation,
        installer_name=installer.name,
    )
    application = QApplication.instance()
    if application is not None:
        QTimer.singleShot(250, application.quit)


def _show_mirror_cdk_required_dialog(self):
    dialog = QDialog(self)
    dialog.setWindowTitle("Mirror 다운로드")
    dialog.setMinimumWidth(460)
    if hasattr(self, "_current_style_sheet"):
        dialog.setStyleSheet(self._current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    message = QLabel("먼저 Mirror CDK를 입력한 뒤 다운로드하세요.<br><br>" + _mirror_project_link_text("CDK 받기"))
    message.setWordWrap(True)
    message.setTextFormat(Qt.TextFormat.RichText)
    message.setOpenExternalLinks(True)
    message.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextBrowserInteraction
    )
    layout.addWidget(message)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()


def _mirror_project_link_text(action: str) -> str:
    """Return the visible, clickable Mirror project link used by download dialogs."""
    return (
        f"Mirror 프로젝트 페이지에서 {action}할 수 있습니다:"
        f'<a href="{MIRROR_PROJECT_URL}">{MIRROR_PROJECT_URL}</a>'
    )


def _mirror_download_version_is_available(latest: str, current: str) -> bool:
    """Whether the remote release is current or newer; older ones need confirmation."""
    return bool(latest) and not is_newer_version(current, latest)


def _confirm_mirror_older_version_download(self: Any, current: str, latest: str) -> bool:
    dialog = QMessageBox(self)
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setWindowTitle("Mirror에서 이전 버전 다운로드")
    dialog.setText("Mirror가 제공하는 버전이 현재 설치된 버전보다 낮습니다.")
    dialog.setInformativeText(
        f"현재 버전: {current}\nMirror 버전: {latest}\n"
        "다운로드가 완료되면 설치 프로그램이 자동으로 실행되며, 현재 버전을 덮어쓸 수 있습니다. 계속 다운로드할까요?"
    )
    download = dialog.addButton("다운로드", QMessageBox.ButtonRole.AcceptRole)
    cancel = dialog.addButton("취소", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(cancel)
    dialog.setEscapeButton(cancel)
    if hasattr(self, "_current_style_sheet"):
        dialog.setStyleSheet(self._current_style_sheet())
    fit_dialog_to_available_screen(dialog, QSize(490, 210))
    QApplication.beep()
    dialog.exec()
    return dialog.clickedButton() is download


def _show_mirror_project_download_dialog(self: Any, summary: str) -> None:
    """Show a download failure with a direct, browser-openable Mirror link."""
    dialog = QDialog(self)
    dialog.setWindowTitle("Mirror 다운로드")
    dialog.setMinimumWidth(460)
    if hasattr(self, "_current_style_sheet"):
        dialog.setStyleSheet(self._current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    message = QLabel(summary)
    message.setWordWrap(True)
    layout.addWidget(message)
    link = QLabel(_mirror_project_link_text("다운로드 시도"))
    link.setWordWrap(True)
    link.setTextFormat(Qt.TextFormat.RichText)
    link.setOpenExternalLinks(True)
    link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    layout.addWidget(link)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()


def _should_show_startup_update(self, info):
    return should_show_startup_update(self._update_config, info)


def _show_update_dialog(self, info, manual=False):
    result = show_update_dialog(self, self._current_style_sheet(), info, APP_VERSION)
    if result.get("never_remind"):
        self._update_config["never_remind"] = True
    if result.get("ignored_version"):
        self._update_config["ignored_version"] = result["ignored_version"]
    if result.get("changed"):
        self._save_update_config()


def _show_update_failure_netdisk_prompt(self, detail=""):
    box = QMessageBox(self)
    box.setWindowTitle("업데이트 확인 실패")
    box.setText("Mirror酱 업데이트 서비스 요청에 실패했습니다. 잠시 후 다시 시도하세요.")
    if detail:
        box.setInformativeText(str(detail))
    box.addButton("확인", QMessageBox.AcceptRole)
    box.exec()


def _open_update_homepage(self):
    self._open_url(GITHUB_LATEST_RELEASE_URL)


def _open_bilibili_homepage(self):
    self._open_url(BILIBILI_HOME_URL)


def _open_project_homepage(self):
    self._open_url(GITHUB_HOME_URL)


def _open_support_homepage(self):
    self._open_url(SUPPORT_US_URL)


def _open_discord_group(self):
    self._open_url(DISCORD_GROUP_URL)


def _show_group_chat_notice(self):
    dialog = QDialog(self)
    dialog.setWindowTitle("단체 채팅 참여")
    dialog.setMinimumSize(410, 245)
    if hasattr(self, "_current_style_sheet"):
        dialog.setStyleSheet(self._current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 22, 24, 22)
    layout.setSpacing(11)
    intro = QLabel("문제가 생겼거나 계산기 사용 경험을 나누고 싶으신가요?")
    intro.setWordWrap(True)
    intro.setStyleSheet(themed_style("color:#8b949e"))
    layout.addWidget(intro)

    def add_option(title: str, detail: str, action: QPushButton, number: str = ""):
        card = QFrame()
        card.setObjectName("groupChatOption")
        card.setStyleSheet(themed_style(
            "QFrame#groupChatOption{background:#161b22;border:1px solid #30363d;border-radius:8px}"
        ))
        row = QHBoxLayout(card)
        row.setContentsMargins(15, 12, 15, 12)
        row.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(5)
        title_label = QLabel(title)
        title_label.setStyleSheet(themed_style("font-size:14px;font-weight:600;color:#f0f6fc"))
        text.addWidget(title_label)
        if number:
            number_label = QLabel(number)
            number_label.setObjectName("groupChatNumber")
            number_label.setStyleSheet(themed_style("font-size:18px;font-weight:600;color:#58a6ff"))
            text.addWidget(number_label)
        detail_label = QLabel(detail)
        detail_label.setWordWrap(True)
        detail_label.setStyleSheet(themed_style("color:#8b949e"))
        text.addWidget(detail_label)
        row.addLayout(text, 1)
        row.addWidget(action, 0, Qt.AlignVCenter)
        layout.addWidget(card)

    copy_button = QPushButton("그룹 번호 복사")

    def copy_group_number():
        QApplication.clipboard().setText(QQ_GROUP_NUMBER)
        copy_button.setText("복사됨")

    copy_button.clicked.connect(copy_group_number)
    add_option("QQ 단체 채팅", GROUP_CHAT_DEVELOPER_HINT, copy_button, QQ_GROUP_NUMBER)

    discord_button = QPushButton("Discord 참여")
    discord_button.setObjectName("btnNew")

    def open_discord_group():
        self._open_discord_group()
        dialog.accept()

    discord_button.clicked.connect(open_discord_group)
    add_option("Discord 서버", GROUP_CHAT_DISCORD_HINT, discord_button)
    fit_dialog_to_available_screen(dialog, QSize(490, 285))
    dialog.exec()
    return dialog


def _show_netdisk_download_dialog(self, links):
    links = tuple((str(name), str(url)) for name, url in links if name and url)
    if not links:
        return
    dialog = QDialog(self)
    dialog.setWindowTitle("클라우드 드라이브 다운로드")
    dialog.setMinimumSize(500, 315)
    if hasattr(self, "_current_style_sheet"):
        dialog.setStyleSheet(self._current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(10)

    for name, url in links:
        card = QFrame()
        card.setObjectName("netdiskOption")
        card.setStyleSheet(themed_style(
            "QFrame#netdiskOption{background:#161b22;border:1px solid #30363d;border-radius:8px}"
        ))
        row = QHBoxLayout(card)
        row.setContentsMargins(15, 11, 15, 11)
        row.setSpacing(12)
        description = QVBoxLayout()
        description.setSpacing(4)
        title = QLabel(name)
        title.setStyleSheet(themed_style("font-size:15px;font-weight:600;color:#f0f6fc"))
        description.addWidget(title)
        domain = QLabel(urlsplit(url).netloc)
        domain.setStyleSheet(themed_style("color:#8b949e"))
        description.addWidget(domain)
        row.addLayout(description, 1)
        open_button = QPushButton("클라우드 스토리지 열기")
        open_button.setAccessibleName(f"{name} 열기")
        open_button.setStyleSheet(themed_style(
            "QPushButton{background:#161b22;border:1px solid #58a6ff;color:#58a6ff;"
            "border-radius:6px;padding:7px 14px}"
            "QPushButton:hover{background:#30363d}"
        ))

        def open_netdisk(_checked=False, *, target_url=url):
            dialog.accept()
            self._open_url(target_url)

        open_button.clicked.connect(open_netdisk)
        row.addWidget(open_button, 0, Qt.AlignVCenter)
        layout.addWidget(card)

    fit_dialog_to_available_screen(dialog, QSize(650, 390))
    dialog.exec()
    return dialog


def _open_url(self, url):
    try:
        os.startfile(url)
    except Exception:
        import webbrowser
        webbrowser.open(url)


def _is_newer_version(self, remote, current):
    return is_newer_version(remote, current)


class UpdateControllerMixin:
    _maybe_check_updates_on_startup = _maybe_check_updates_on_startup
    _check_updates = _check_updates
    _fetch_update_info = _fetch_update_info
    _on_update_checked = _on_update_checked
    _on_update_error = _on_update_error
    _should_show_startup_update = _should_show_startup_update
    _show_update_dialog = _show_update_dialog
    _show_update_failure_netdisk_prompt = _show_update_failure_netdisk_prompt
    _open_update_homepage = _open_update_homepage
    _open_bilibili_homepage = _open_bilibili_homepage
    _open_project_homepage = _open_project_homepage
    _open_support_homepage = _open_support_homepage
    _open_discord_group = _open_discord_group
    _show_group_chat_notice = _show_group_chat_notice
    _show_netdisk_download_dialog = _show_netdisk_download_dialog
    _open_url = _open_url
    _is_newer_version = _is_newer_version
    _mirror_cdk_value = _mirror_cdk_value
    _save_mirror_cdk = _save_mirror_cdk
    _start_mirror_download = _start_mirror_download
    _on_mirror_download_ready = _on_mirror_download_ready
    _start_mirror_installer_download = _start_mirror_installer_download
    _on_mirror_download_progress = _on_mirror_download_progress
    _on_mirror_installer_downloaded = _on_mirror_installer_downloaded
    _on_mirror_installer_download_error = _on_mirror_installer_download_error
    _finish_mirror_download_ui = _finish_mirror_download_ui
    _launch_mirror_installer = _launch_mirror_installer
