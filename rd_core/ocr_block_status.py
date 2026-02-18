"""Утилиты для определения статуса OCR-распознавания блока."""
from enum import Enum
from typing import Optional


class OCRStatus(Enum):
    """Статус OCR-распознавания блока."""

    SUCCESS = "success"
    ERROR = "error"
    NOT_RECOGNIZED = "not_recognized"


_ERROR_PREFIX = "[Ошибка"


def get_ocr_status(ocr_text: Optional[str]) -> OCRStatus:
    """Определить статус OCR по тексту результата."""
    if not ocr_text or not ocr_text.strip():
        return OCRStatus.NOT_RECOGNIZED
    if ocr_text.startswith(_ERROR_PREFIX):
        return OCRStatus.ERROR
    return OCRStatus.SUCCESS


def needs_ocr(block) -> bool:
    """Блок нуждается в OCR: пустой результат, ошибка, или помечен для корректировки."""
    if getattr(block, "is_correction", False):
        return True
    return get_ocr_status(getattr(block, "ocr_text", None)) != OCRStatus.SUCCESS
