"""Виджет статистики OCR-распознавания блоков."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


@dataclass
class OCRStats:
    """Статистика распознавания по типам блоков."""

    text_ok: int = 0
    text_total: int = 0
    image_ok: int = 0
    image_total: int = 0
    stamp_ok: int = 0
    stamp_total: int = 0
    errors: int = 0
    not_recognized: int = 0

    @property
    def total_ok(self) -> int:
        return self.text_ok + self.image_ok + self.stamp_ok

    @property
    def total(self) -> int:
        return self.text_total + self.image_total + self.stamp_total


def compute_ocr_stats(blocks: List) -> OCRStats:
    """Подсчитать статистику OCR по списку блоков."""
    from rd_core.models.enums import BlockType
    from rd_core.ocr_block_status import OCRStatus, get_ocr_status

    stats = OCRStats()

    for block in blocks:
        is_stamp = (
            getattr(block, "block_type", None) == BlockType.IMAGE
            and getattr(block, "category_code", None) == "stamp"
        )
        is_image = (
            getattr(block, "block_type", None) == BlockType.IMAGE and not is_stamp
        )

        status = get_ocr_status(getattr(block, "ocr_text", None))

        if is_stamp:
            stats.stamp_total += 1
            if status == OCRStatus.SUCCESS:
                stats.stamp_ok += 1
        elif is_image:
            stats.image_total += 1
            if status == OCRStatus.SUCCESS:
                stats.image_ok += 1
        else:
            stats.text_total += 1
            if status == OCRStatus.SUCCESS:
                stats.text_ok += 1

        if status == OCRStatus.ERROR:
            stats.errors += 1
        elif status == OCRStatus.NOT_RECOGNIZED:
            stats.not_recognized += 1

    return stats


class OCRStatsWidget(QFrame):
    """Компактный виджет статистики OCR-распознавания."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "OCRStatsWidget { background: #2d2d30; border: 1px solid #3e3e42; "
            "border-radius: 4px; padding: 2px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(1)

        self._main_label = QLabel()
        self._main_label.setStyleSheet("color: #cccccc; font-size: 11px;")
        layout.addWidget(self._main_label)

        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: #e74c3c; font-size: 11px;")
        layout.addWidget(self._error_label)

        self.hide()

    def update_stats(self, stats: OCRStats):
        """Обновить отображение статистики."""
        if stats.total == 0:
            self.hide()
            return

        self.show()

        if stats.total_ok == stats.total:
            self._main_label.setText(
                f"\u2705 \u0412\u0441\u0435 \u0431\u043b\u043e\u043a\u0438 \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u043d\u044b ({stats.total}/{stats.total})"
            )
            self._main_label.setStyleSheet("color: #27ae60; font-size: 11px;")
            self._error_label.hide()
            return

        self._main_label.setStyleSheet("color: #cccccc; font-size: 11px;")

        parts = []
        if stats.text_total > 0:
            parts.append(f"\u0422\u0435\u043a\u0441\u0442: {stats.text_ok}/{stats.text_total}")
        if stats.image_total > 0:
            parts.append(f"\u0418\u0437\u043e\u0431\u0440.: {stats.image_ok}/{stats.image_total}")
        if stats.stamp_total > 0:
            parts.append(f"\u0428\u0442\u0430\u043c\u043f\u044b: {stats.stamp_ok}/{stats.stamp_total}")

        self._main_label.setText("\U0001f4ca " + " | ".join(parts))

        error_parts = []
        if stats.errors > 0:
            error_parts.append(f"\u041e\u0448\u0438\u0431\u043e\u043a: {stats.errors}")
        if stats.not_recognized > 0:
            error_parts.append(f"\u041d\u0435 \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u043d\u043e: {stats.not_recognized}")

        if error_parts:
            self._error_label.setText("\u274c " + " | ".join(error_parts))
            self._error_label.show()
        else:
            self._error_label.hide()

    def clear_stats(self):
        """Очистить статистику."""
        self.hide()
