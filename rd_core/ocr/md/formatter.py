"""Форматирование контента в Markdown."""
import json as json_module
from typing import Dict

from ..generator_common import (
    contains_html,
    extract_image_ocr_data,
    is_image_ocr_json,
    sanitize_markdown,
    strip_code_fence,
)
from .html_converter import html_to_markdown


def format_stamp_md(stamp_data: Dict) -> str:
    """Форматировать данные штампа в компактную Markdown строку."""
    parts = []

    if stamp_data.get("document_code"):
        parts.append(f"Шифр: {stamp_data['document_code']}")
    if stamp_data.get("stage"):
        parts.append(f"Стадия: {stamp_data['stage']}")
    if stamp_data.get("project_name"):
        parts.append(f"Объект: {stamp_data['project_name']}")
    if stamp_data.get("organization"):
        parts.append(f"Организация: {stamp_data['organization']}")

    return " | ".join(parts) if parts else ""


def format_image_ocr_md(data: dict) -> str:
    """Форматировать данные OCR изображения в компактный Markdown."""
    img_data = extract_image_ocr_data(data)
    parts = []

    # Заголовок: [ИЗОБРАЖЕНИЕ] Тип: XXX | Оси: XXX
    header_parts = ["**[ИЗОБРАЖЕНИЕ]**"]
    if img_data.get("zone_name") and img_data["zone_name"] != "Не определено":
        header_parts.append(f"Тип: {img_data['zone_name']}")
    if img_data.get("grid_lines") and img_data["grid_lines"] != "Не определены":
        header_parts.append(f"Оси: {img_data['grid_lines']}")
    if img_data.get("location_text"):
        header_parts.append(img_data["location_text"])
    parts.append(" | ".join(header_parts))

    # Краткое описание
    if img_data.get("content_summary"):
        parts.append(f"**Краткое описание:** {img_data['content_summary']}")

    # Детальное описание
    if img_data.get("detailed_description"):
        parts.append(f"**Описание:** {img_data['detailed_description']}")

    # Распознанный текст
    if img_data.get("clean_ocr_text"):
        parts.append(f"**Текст на чертеже:** {img_data['clean_ocr_text']}")

    # Ключевые сущности - через запятую, без backticks
    if img_data.get("key_entities"):
        entities = ", ".join(img_data["key_entities"])
        parts.append(f"**Сущности:** {entities}")

    return "\n".join(parts) if parts else ""


def process_ocr_content(ocr_text: str) -> str:
    """Обработать содержимое блока и конвертировать в Markdown."""
    if not ocr_text:
        return ""

    text = strip_code_fence(ocr_text.strip())
    if not text:
        return ""

    # HTML контент — надёжное определение через поиск тегов
    if contains_html(text):
        return html_to_markdown(text)

    # JSON контент
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json_module.loads(text)
            if isinstance(parsed, dict) and is_image_ocr_json(parsed):
                return format_image_ocr_md(parsed)
            # Fallback для другого JSON
            return json_module.dumps(parsed, ensure_ascii=False, separators=(',', ':'))
        except json_module.JSONDecodeError:
            pass

    # Обычный текст - также применяем санитизацию markdown
    return sanitize_markdown(text)
