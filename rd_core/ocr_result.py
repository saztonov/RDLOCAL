"""Единый модуль для работы с OCR-статусами и маркерами ошибок.

Единственный источник истины для констант ERROR_PREFIX / NON_RETRIABLE_PREFIX
и функций проверки / создания OCR-результатов.

Включает shared валидацию качества OCR-вывода (is_suspicious_output),
используемую и сервером, и desktop-клиентом.
"""
from __future__ import annotations

import json
import re
from enum import Enum
from html.parser import HTMLParser
from typing import Optional, Tuple


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
    """Текст содержит успешный OCR-результат (непустой, без маркеров ошибок, не suspicious)."""
    if not text or not text.strip():
        return False
    if is_any_error(text):
        return False
    suspicious, _ = is_suspicious_output(text)
    return not suspicious


# ── Высокоуровневые функции ──────────────────────────────────────────

def get_status(text: Optional[str]) -> OCRStatus:
    """Определить статус OCR по тексту результата."""
    if not text or not text.strip():
        return OCRStatus.NOT_RECOGNIZED
    if is_any_error(text):
        return OCRStatus.ERROR
    suspicious, _ = is_suspicious_output(text)
    if suspicious:
        return OCRStatus.ERROR
    return OCRStatus.SUCCESS


def needs_ocr(block) -> bool:
    """Блок нуждается в OCR: пустой результат, ошибка, или помечен для корректировки."""
    if getattr(block, "is_correction", False):
        return True
    return get_status(getattr(block, "ocr_text", None)) != OCRStatus.SUCCESS


# ── Валидация качества OCR-вывода ────────────────────────────────────

# Ключи, указывающие на layout/bbox dump
_BBOX_KEYS = frozenset({"data-bbox", "data-label"})

# Ключи, указывающие на table/structure dump (не HTML)
_TABLE_STRUCTURE_KEYS = frozenset({
    "table", "rowspan", "colspan", "cells", "rows", "columns",
})

# Паттерны reasoning/self-talk модели (English + Russian)
_REASONING_PATTERNS = re.compile(
    r'(?:'
    r'(?:The user wants|I need to|I will now|I should|Let me|Looking at|Analyzing)\b|'
    r'(?:Давай|Мне нужно|Я должен|Рассмотрим|Анализируя)\b|'
    r'^\d+\.\s+\*\*'
    r')',
    re.IGNORECASE | re.MULTILINE,
)

_REASONING_CONCLUSION_RE = re.compile(
    r'(?:I will now generate|based on this analysis|Let me now|I will now output)\b',
    re.IGNORECASE,
)

_HTML_TAG_RE_SIMPLE = re.compile(
    r'<(?:p|table|div|h[1-6]|ul|ol|tr|td|th)\b', re.IGNORECASE,
)


class _TextExtractor(HTMLParser):
    """Извлекает чистый текст из HTML, игнорируя теги."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str):
        self.parts.append(data)

    def get_text(self) -> str:
        return "".join(self.parts)


def _extract_plain_text(html: str) -> str:
    """Извлечь чистый текст из HTML строки."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return re.sub(r'<[^>]+>', '', html)
    return parser.get_text()


def _collect_keys(obj, keys: set, depth: int) -> None:
    """Рекурсивно собрать ключи из вложенных dict/list (max depth=3)."""
    if depth > 3:
        return
    if isinstance(obj, dict):
        keys.update(obj.keys())
        for v in obj.values():
            _collect_keys(v, keys, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _collect_keys(item, keys, depth + 1)


def _is_json_structure_dump(text: str) -> Tuple[bool, str]:
    """Определить JSON array/object — layout-dump или table-structure вместо HTML.

    Returns:
        (is_dump, reason) — True + причина если это структурный JSON-dump.
    """
    if not (text.startswith('[') and text.endswith(']')):
        return False, ""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return False, ""

    if not isinstance(data, list) or len(data) == 0:
        return False, ""

    all_keys: set = set()
    _collect_keys(data, all_keys, depth=0)

    if all_keys & _BBOX_KEYS:
        return True, "JSON layout-dump (bbox без HTML content)"

    if all_keys & _TABLE_STRUCTURE_KEYS:
        return True, "JSON table-structure dump (не HTML)"

    return False, ""


def is_suspicious_output(ocr_text: str, ocr_html: str = "") -> Tuple[bool, str]:
    """Проверить OCR-вывод на подозрительный контент.

    Shared helper для сервера и desktop-клиента. Определяет вывод, который
    формально непуст и без маркеров ошибок, но не является валидным OCR-результатом.

    Args:
        ocr_text: OCR результат (сырой текст из бэкенда)
        ocr_html: OCR HTML (после sanitize, из result.json). Опционально.

    Returns:
        (is_suspicious, reason) — True + описание если вывод подозрительный.
    """
    if not ocr_text or not ocr_text.strip():
        return False, ""
    if is_any_error(ocr_text):
        return False, ""

    stripped = ocr_text.strip()

    # 1. JSON structure dump (layout-dump, table-dump)
    is_dump, reason = _is_json_structure_dump(stripped)
    if is_dump:
        return True, reason

    # 2. Preformatted JSON в HTML
    if ocr_html:
        html_stripped = ocr_html.strip()
        if html_stripped.startswith("<pre>") and html_stripped.endswith("</pre>"):
            inner = html_stripped[5:-6].strip()
            if inner.startswith("[{") or inner.startswith("{") or "&quot;" in inner[:50]:
                return True, "preformatted JSON dump в HTML"

    # 3. Низкая текстовая плотность
    if len(stripped) > 50:
        plain = _extract_plain_text(stripped)
        plain_clean = plain.strip()
        if len(plain_clean) < 20:
            return True, f"низкая текстовая плотность ({len(plain_clean)} символов чистого текста)"

    # 4. Reasoning-like response (self-talk модели вместо OCR)
    if _REASONING_PATTERNS.search(stripped):
        reasoning_matches = list(_REASONING_PATTERNS.finditer(stripped))
        has_conclusion = bool(_REASONING_CONCLUSION_RE.search(stripped))
        if len(reasoning_matches) >= 2 or has_conclusion:
            has_html = bool(_HTML_TAG_RE_SIMPLE.search(stripped))
            if not has_html or len(reasoning_matches) >= 3:
                return True, "suspicious OCR output: reasoning-like response"

    return False, ""
