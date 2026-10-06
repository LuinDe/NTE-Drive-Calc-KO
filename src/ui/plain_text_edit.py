# 提供只接受纯文本的输入控件。
"""Text edit variants used by feature pages."""

from __future__ import annotations

from PySide6.QtWidgets import QTextEdit


class PlainTextOnlyTextEdit(QTextEdit):
    def insertFromMimeData(self, source):
        if source.hasText():
            cursor = self.textCursor()   # not insertPlainText: i18n_display would translate what the parser reads
            cursor.insertText(source.text())
            self.setTextCursor(cursor)
