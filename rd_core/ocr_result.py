"""Единый модуль для работы с OCR-статусами и маркерами ошибок.

Единственный источник истины для констант ERROR_PREFIX / NON_RETRIABLE_PREFIX
и функций проверки / создания OCR-результатов.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class OCRStatus(Enum):
    """Статус OCR-распознавания блока."""

    SUCCESS = "success"
    ERROR = "error"
    NOT_RECOGNIZED = "not_recognized"


# ── Префиксы ошибок OCR ──────────────────────────────────────────────
ERROR_PREFIX = "[Ошибка"
NON_RETRIABLE_PREFIX = "[НеПовторяемая"


# ── Создание маркеров ────────────────────────────────────────────────

def make_error(msg: str) -> str:
    """Создать строку повторяемой ошибки OCR."""
    return f"[Ошибка: {msg}]"


def make_non_retriable(msg: str) -> str:
    """Создать строку неповторяемой ошибки OCR."""
    return f"[НеПовторяемая ошибка: {msg}]"


# ── Проверка маркеров ────────────────────────────────────────────────

def is_error(text: Optional[str]) -> bool:
    """Текст содержит маркер повторяемой ошибки (retry possible)."""
    return bool(text) and text.startswith(ERROR_PREFIX)


def is_non_retriable(text: Optional[str]) -> bool:
    """Текст содержит маркер неповторяемой ошибки (don't retry)."""
    return bool(text) and text.startswith(NON_RETRIABLE_PREFIX)


def is_any_error(text: Optional[str]) -> bool:
    """Текст содержит любой маркер ошибки."""
    return is_error(text) or is_non_retriable(text)


def is_success(text: Optional[str]) -> bool:
    """Текст содержит успешный OCR-результат (непустой, без маркеров ошибок)."""
    if not text or not text.strip():
        return False
    return not is_any_error(text)


# ── Высокоуровневые функции ──────────────────────────────────────────

def get_status(text: Optional[str]) -> OCRStatus:
    """Определить статус OCR по тексту результата."""
    if not text or not text.strip():
        return OCRStatus.NOT_RECOGNIZED
    if is_any_error(text):
        return OCRStatus.ERROR
    return OCRStatus.SUCCESS


def needs_ocr(block) -> bool:
    """Блок нуждается в OCR: пустой результат, ошибка, или помечен для корректировки."""
    if getattr(block, "is_correction", False):
        return True
    return get_status(getattr(block, "ocr_text", None)) != OCRStatus.SUCCESS
