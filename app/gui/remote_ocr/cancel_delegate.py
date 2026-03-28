"""Delegate для кнопки отмены задачи в таблице Remote OCR."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from app.gui.remote_ocr.jobs_model import JOB_ID_ROLE

CANCELLABLE = frozenset({"queued", "processing", "paused"})

_BTN_W = 26
_BTN_H = 22
_RADIUS = 4
_COLOR_NORMAL = QColor("#c0392b")
_COLOR_HOVER = QColor("#922b21")


def _button_rect(cell_rect: QRect) -> QRect:
    """Прямоугольник кнопки по центру ячейки."""
    x = cell_rect.x() + (cell_rect.width() - _BTN_W) // 2
    y = cell_rect.y() + (cell_rect.height() - _BTN_H) // 2
    return QRect(x, y, _BTN_W, _BTN_H)


class CancelButtonDelegate(QStyledItemDelegate):
    """Рисует кнопку «✕» для отмены активных задач."""

    cancel_requested = Signal(str)  # job_id

    def paint(self, painter: QPainter, option, index) -> None:
        # Рисуем фон ячейки (selection highlight и т.д.)
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else None
        if style:
            style.drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)

        status = index.data(Qt.UserRole)
        if status not in CANCELLABLE:
            return

        rect = _button_rect(option.rect)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        color = _COLOR_HOVER if hovered else _COLOR_NORMAL

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(color.darker(120), 1))
        painter.setBrush(QBrush(color))
        painter.drawRoundedRect(rect, _RADIUS, _RADIUS)

        painter.setPen(QPen(Qt.white))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, "✕")
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if event.type() != QEvent.MouseButtonRelease:
            return False

        status = index.data(Qt.UserRole)
        if status not in CANCELLABLE:
            return False

        rect = _button_rect(option.rect)
        if not rect.contains(event.pos().toPoint()):
            return False

        job_id = index.data(JOB_ID_ROLE)
        if job_id:
            self.cancel_requested.emit(job_id)
            return True
        return False

    def sizeHint(self, option, index) -> QSize:
        return QSize(50, 28)
