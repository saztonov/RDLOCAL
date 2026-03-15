"""Утилиты для определения статуса OCR-распознавания блока.

Re-export shim: вся логика в rd_core.ocr_result.
"""
from rd_core.ocr_result import (  # noqa: F401
    OCRStatus,
    get_status as get_ocr_status,
    needs_ocr,
)
