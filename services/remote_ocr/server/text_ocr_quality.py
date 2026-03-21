"""Фильтрация и классификация качества TEXT/TABLE OCR результатов.

Решает две задачи:
1. filter_mixed_text_output — удаляет image-артефакты из Chandra TEXT блоков
   (модель fine-tuned с промптом, описывающим изображения, поэтому генерирует
   <img alt="..."> и описания картинок даже в TEXT блоках).
2. classify_text_output — определяет подозрительные результаты
   (layout-dump, JSON bbox вместо HTML) для fallback retry.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
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
        # Fallback: strip тегов regex-ом
        return re.sub(r'<[^>]+>', '', html)
    return parser.get_text()


def filter_mixed_text_output(ocr_text: str, engine: str) -> Tuple[str, dict]:
    """Удалить image-артефакты из TEXT/TABLE OCR результата Chandra.

    Применяется ТОЛЬКО к engine='chandra'. Для других engine возвращает текст без изменений.

    Args:
        ocr_text: OCR результат (HTML строка)
        engine: имя OCR движка ('chandra', 'datalab', 'openrouter')

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

    Args:
        ocr_text: OCR результат (сырой текст из бэкенда)
        ocr_html: OCR HTML (после sanitize, из result.json)

    Returns:
        dict с полями:
            quality: 'ok' | 'suspicious' | 'empty' | 'api_error'
            reason: описание причины
    """
    # Пустой результат
    if not ocr_text or not ocr_text.strip():
        return {"quality": "empty", "reason": "пустой ocr_text"}

    # API ошибка
    if is_error(ocr_text):
        if is_non_retriable(ocr_text):
            return {"quality": "api_error", "reason": "неповторяемая ошибка API"}
        return {"quality": "api_error", "reason": "ошибка API"}

    stripped = ocr_text.strip()

    # JSON array bbox-объектов (layout-only dump)
    if _is_bbox_json_dump(stripped):
        return {"quality": "suspicious", "reason": "JSON layout-dump (bbox без HTML content)"}

    # Preformatted JSON в HTML
    if ocr_html:
        html_stripped = ocr_html.strip()
        if html_stripped.startswith("<pre>") and html_stripped.endswith("</pre>"):
            inner = html_stripped[5:-6].strip()
            # HTML-encoded JSON
            if inner.startswith("[{") or inner.startswith("[{"):
                return {"quality": "suspicious", "reason": "preformatted JSON dump в HTML"}
            if inner.startswith("[{") or "&quot;" in inner[:50]:
                return {"quality": "suspicious", "reason": "preformatted JSON dump в HTML"}

    # Низкая текстовая плотность
    if len(stripped) > 50:
        plain = _extract_plain_text(stripped)
        plain_clean = plain.strip()
        if len(plain_clean) < 20:
            return {
                "quality": "suspicious",
                "reason": f"низкая текстовая плотность ({len(plain_clean)} символов чистого текста)",
            }

    return {"quality": "ok", "reason": ""}


def _is_bbox_json_dump(text: str) -> bool:
    """Определить JSON array/object с data-bbox/data-label без HTML content."""
    if not (text.startswith('[') and text.endswith(']')):
        return False
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return False

    if not isinstance(data, list) or len(data) == 0:
        return False

    # Все элементы — dict с data-bbox
    return all(
        isinstance(item, dict) and ("data-bbox" in item or "data-label" in item)
        for item in data
    )
