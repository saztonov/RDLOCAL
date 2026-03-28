"""Фильтрация и классификация качества TEXT/TABLE OCR результатов.

Решает две задачи:
1. filter_mixed_text_output — удаляет image-артефакты из Chandra TEXT блоков
   (модель fine-tuned с промптом, описывающим изображения, поэтому генерирует
   <img alt="..."> и описания картинок даже в TEXT блоках).
2. classify_text_output — определяет подозрительные результаты
   (layout-dump, JSON bbox/table-dump вместо HTML) для fallback retry.
   Делегирует shared helper is_suspicious_output() из rd_core.ocr_result.
"""
from __future__ import annotations

import re
from typing import Tuple

from .logging_config import get_logger
from .ocr_constants import is_error, is_non_retriable

logger = get_logger(__name__)

# Паттерн для <div data-label="Image">...</div> (non-greedy, без вложенных div)
_IMAGE_DIV_RE = re.compile(
    r'<div\b[^>]*\bdata-label\s*=\s*"Image"[^>]*>.*?</div>',
    re.DOTALL | re.IGNORECASE,
)

# Standalone <img ...> или <img .../> теги
_IMG_TAG_RE = re.compile(r'<img\b[^>]*/?\s*>', re.IGNORECASE)

# Множественные пробелы / пустые строки
_MULTI_WHITESPACE_RE = re.compile(r'\n{3,}')
_MULTI_SPACE_RE = re.compile(r'[ \t]{2,}')


def filter_mixed_text_output(ocr_text: str, engine: str) -> Tuple[str, dict]:
    """Удалить image-артефакты из TEXT/TABLE OCR результата Chandra.

    Применяется ТОЛЬКО к engine='chandra'. Для других engine возвращает текст без изменений.

    Args:
        ocr_text: OCR результат (HTML строка)
        engine: имя OCR движка ('lmstudio', 'chandra')

    Returns:
        (cleaned_text, metadata) где metadata содержит:
            removed_chars: количество удалённых символов
            removed_image_segments: количество удалённых image сегментов
    """
    meta = {"removed_chars": 0, "removed_image_segments": 0}

    if engine != "chandra" or not ocr_text:
        return ocr_text, meta

    original_len = len(ocr_text)
    result = ocr_text

    # 1. Удалить <div data-label="Image">...</div> блоки
    image_divs = _IMAGE_DIV_RE.findall(result)
    if image_divs:
        meta["removed_image_segments"] += len(image_divs)
        result = _IMAGE_DIV_RE.sub('', result)

    # 2. Удалить standalone <img ...> теги
    img_tags = _IMG_TAG_RE.findall(result)
    if img_tags:
        meta["removed_image_segments"] += len(img_tags)
        result = _IMG_TAG_RE.sub('', result)

    # 3. Нормализовать пробелы
    result = _MULTI_WHITESPACE_RE.sub('\n\n', result)
    result = _MULTI_SPACE_RE.sub(' ', result)
    result = result.strip()

    meta["removed_chars"] = original_len - len(result)

    if meta["removed_image_segments"] > 0:
        logger.debug(
            f"filter_mixed_text: удалено {meta['removed_image_segments']} image сегментов, "
            f"{meta['removed_chars']} символов"
        )

    return result, meta


def classify_text_output(ocr_text: str, ocr_html: str = "") -> dict:
    """Классифицировать качество TEXT/TABLE OCR результата.

    Использует shared helper is_suspicious_output() из rd_core.ocr_result
    для детекции suspicious output (JSON-dump, low density и т.п.).

    Args:
        ocr_text: OCR результат (сырой текст из бэкенда)
        ocr_html: OCR HTML (после sanitize, из result.json)

    Returns:
        dict с полями:
            quality: 'ok' | 'suspicious' | 'empty' | 'api_error'
            reason: описание причины
    """
    from rd_core.ocr_result import is_suspicious_output

    # Пустой результат
    if not ocr_text or not ocr_text.strip():
        return {"quality": "empty", "reason": "пустой ocr_text"}

    # API ошибка
    if is_error(ocr_text):
        if is_non_retriable(ocr_text):
            return {"quality": "api_error", "reason": "неповторяемая ошибка API"}
        return {"quality": "api_error", "reason": "ошибка API"}

    # Shared suspicious detection (JSON-dump, preformatted JSON, low density)
    suspicious, reason = is_suspicious_output(ocr_text, ocr_html)
    if suspicious:
        return {"quality": "suspicious", "reason": reason}

    return {"quality": "ok", "reason": ""}
