# 用紧凑单行展示历史角色名单，并保留完整悬停内容与原生选中样式。
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QStyledItemDelegate, QStyle, QStyleOptionViewItem


class HistoryCharacterDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):  # noqa: N802 - Qt override
        return QSize(0, max(24, option.fontMetrics.lineSpacing() + 6))

    def paint(self, painter, option, index) -> None:
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text = styled.text
        styled.text = ""
        style = styled.widget.style() if styled.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)
        width = max(1, styled.rect.width() - 16)
        role = (QPalette.ColorRole.HighlightedText if styled.state & QStyle.StateFlag.State_Selected
                else QPalette.ColorRole.Text)
        painter.save()
        painter.setClipRect(styled.rect)
        painter.setFont(styled.font)
        group = QPalette.ColorGroup.Active if styled.state & QStyle.StateFlag.State_Enabled else QPalette.ColorGroup.Disabled
        painter.setPen(styled.palette.color(group, role))
        painter.drawText(styled.rect.adjusted(8, 0, -8, 0),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         styled.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, width))
        painter.restore()
