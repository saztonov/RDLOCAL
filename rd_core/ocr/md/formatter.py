"""Форматирование контента в Markdown."""
import json as json_module
from typing import Dict

from ..generator_common import (
    contains_html,
    extract_image_ocr_data,
    extract_qwen_html,
    is_image_ocr_json,
    is_qwen_ocr_json,
    sanitize_markdown,
    strip_code_fence,
)
from .html_converter import html_to_markdown


def format_stamp_md(stamp_data: Dict, multiline: bool = False) -> str:
    """Форматировать данные штампа в Markdown строку.

    Канонический порядок полей:
    Шифр | Стадия | Лист N (из M) | Объект | Наименование листа | Организация

    Args:
        stamp_data: словарь с данными штампа.
        multiline: True — каждое поле на отдельной строке (для заголовка),
            False — через `` | `` (для per-block метаданных).
    """
    parts = []

    if stamp_data.get("document_code"):
        parts.append(f"Шифр: {stamp_data['document_code']}")
    if stamp_data.get("stage"):
        parts.append(f"Стадия: {stamp_data['stage']}")

    # Лист (page-level, может отсутствовать для inherited stamp)
    sheet_num = stamp_data.get("sheet_number", "")
    total_sheets = stamp_data.get("total_sheets", "")
    if sheet_num:
        if total_sheets:
            parts.append(f"Лист: {sheet_num} (из {total_sheets})")
        else:
            parts.append(f"Лист: {sheet_num}")

    if stamp_data.get("project_name"):
        parts.append(f"Объект: {stamp_data['project_name']}")

    # Наименование листа (page-level)
    if stamp_data.get("sheet_name"):
        parts.append(f"Наименование листа: {stamp_data['sheet_name']}")

    if stamp_data.get("organization"):
        parts.append(f"Организация выпустившая проект: {stamp_data['organization']}")

    if multiline:
        return "\n".join(parts) if parts else ""
    return " | ".join(parts) if parts else ""


def format_image_ocr_md(data: dict) -> str:
    """Форматировать данные OCR изображения в компактный Markdown."""
    img_data = extract_image_ocr_data(data)
    parts = []

    # Заголовок: [ИЗОБРАЖЕНИЕ] Фрагмент: XXX | Зона: XXX | Оси: XXX | Отм.: XXX
    header_parts = ["**[ИЗОБРАЖЕНИЕ]**"]
    if img_data.get("fragment_type") and img_data["fragment_type"] != "Не определено":
        header_parts.append(f"Фрагмент: {img_data['fragment_type']}")
    if img_data.get("zone_name") and img_data["zone_name"] != "Не определено":
        header_parts.append(f"Зона: {img_data['zone_name']}")
    if img_data.get("grid_lines") and img_data["grid_lines"] != "Не определены":
        header_parts.append(f"Оси: {img_data['grid_lines']}")
    if img_data.get("level_or_elevation") and img_data["level_or_elevation"] != "Не определено":
        header_parts.append(f"Отм.: {img_data['level_or_elevation']}")
    if img_data.get("location_text"):
        header_parts.append(img_data["location_text"])
    parts.append(" | ".join(header_parts))

    # Краткое описание
    if img_data.get("content_summary"):
        parts.append(f"**Краткое описание:** {img_data['content_summary']}")

    # Детальное описание
    if img_data.get("detailed_description"):
        parts.append(f"**Описание:** {img_data['detailed_description']}")

    # Рекомендации по верификации
    if img_data.get("verification_recommendations"):
        parts.append(f"**Что стоит проверить:** {img_data['verification_recommendations']}")

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

    # 1. JSON-парсинг ДО проверки HTML тегов,
    #    т.к. JSON может содержать HTML внутри строковых значений
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json_module.loads(text)
            if isinstance(parsed, dict):
                # Qwen OCR JSON (content_html / stamp_html)
                if is_qwen_ocr_json(parsed):
                    html = extract_qwen_html(parsed)
                    return html_to_markdown(html) if html else ""
                # Image OCR JSON
                if is_image_ocr_json(parsed):
                    return format_image_ocr_md(parsed)
                # Canonical Chandra JSON {"ocr_html": "<...>"}
                if "ocr_html" in parsed and isinstance(parsed["ocr_html"], str):
                    html = parsed["ocr_html"].strip()
                    return html_to_markdown(html) if html else ""
            if isinstance(parsed, list) and parsed:
                # Chandra JSON array: элементы с 'html' ключом
                html_parts = [
                    item["html"]
                    for item in parsed
                    if isinstance(item, dict)
                    and isinstance(item.get("html"), str)
                    and item["html"].strip()
                ]
                if html_parts:
                    return html_to_markdown("\n".join(html_parts))
                # Pure bbox dump без html — нет полезного контента
                if all(isinstance(item, dict) for item in parsed):
                    keys: set = set()
                    for item in parsed:
                        keys.update(item.keys())
                    if keys & {"data-bbox", "data-label", "bbox", "label"}:
                        return ""
            # Fallback для другого JSON
            return json_module.dumps(parsed, ensure_ascii=False, separators=(',', ':'))
        except json_module.JSONDecodeError:
            pass

    # 2. HTML контент (чистый HTML от Datalab и т.п.)
    if contains_html(text):
        return html_to_markdown(text)

    # 3. Обычный текст
    return sanitize_markdown(text)
