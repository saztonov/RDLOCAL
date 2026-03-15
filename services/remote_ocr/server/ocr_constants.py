"""Общие константы для OCR обработки.

Re-export shim: вся логика в rd_core.ocr_result.
"""
from rd_core.ocr_result import (  # noqa: F401
    ERROR_PREFIX,
    NON_RETRIABLE_PREFIX,
    is_error,
    is_non_retriable,
    is_any_error,
    is_success,
    make_error,
    make_non_retriable,
)
