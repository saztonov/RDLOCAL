"""Общие утилиты для генераторов HTML, Markdown и result.json из OCR результатов."""
import json as json_module
import logging
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from rd_core.models.armor_id import is_armor_id, uuid_to_armor_id

logger = logging.getLogger(__name__)

# Поля штампа, наследуемые на страницы без штампа
INHERITABLE_STAMP_FIELDS = ("document_code", "project_name", "stage", "organization")

# HTML шаблон (общий для всех генераторов)
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - OCR</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 2rem; line-height: 1.6; }}
        .block {{ margin: 1.5rem 0; padding: 1rem; border-left: 3px solid #3498db; background: #f8f9fa; }}
        .block-header {{ font-size: 0.8rem; color: #666; margin-bottom: 0.5rem; }}
        .block-content {{ }}
        .block-type-text {{ border-left-color: #2ecc71; }}
        .block-type-table {{ border-left-color: #e74c3c; }}
        .block-type-image {{ border-left-color: #9b59b6; }}
        .block-content h3 {{ color: #555; font-size: 1rem; margin: 1rem 0 0.5rem 0; padding-bottom: 0.3rem; border-bottom: 1px solid #ddd; }}
        .block-content p {{ margin: 0.5rem 0; }}
        .block-content code {{ background: #e8f4f8; padding: 0.2rem 0.4rem; margin: 0.2rem; border-radius: 3px; display: inline-block; font-family: 'Consolas', 'Courier New', monospace; font-size: 0.9em; }}
        .stamp-info {{ font-size: 0.75rem; color: #2980b9; background: #eef6fc; padding: 0.4rem 0.6rem; margin-top: 0.5rem; border-radius: 3px; border: 1px solid #bde0f7; }}
        .stamp-inherited {{ color: #7f8c8d; background: #f5f5f5; border-color: #ddd; font-style: italic; }}
        table {{ border-collapse: collapse; width: 100%; margin: 0.5rem 0; }}
        th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; }}
        th {{ background: #f0f0f0; }}
        img {{ max-width: 100%; height: auto; }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 0.5rem; }}
        pre {{ white-space: pre-wrap; word-wrap: break-word; background: #fff; padding: 0.5rem; }}
    </style>
</head>
<body>
<h1>{title}</h1>
<p>Сгенерировано: {timestamp} UTC</p>
"""

HTML_FOOTER = "</body></html>"


def get_html_header(title: str) -> str:
    """Получить HTML заголовок с шаблоном."""
    return HTML_TEMPLATE.format(
        title=title,
        timestamp=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    )


def get_block_armor_id(block_id: str) -> str:
    """
    Получить armor ID блока.

    Новые блоки уже имеют ID в формате XXXX-XXXX-XXX.
    Для legacy UUID блоков - конвертируем в armor формат.
    """
    if is_armor_id(block_id):
        return block_id
    return uuid_to_armor_id(block_id)


def parse_stamp_json(ocr_text: Optional[str]) -> Optional[Dict]:
    """Извлечь JSON штампа из ocr_text."""
    if not ocr_text:
        return None

    text = ocr_text.strip()
    if not text:
        return None

    # Прямой JSON
    if text.startswith("{"):
        try:
            return json_module.loads(text)
        except json_module.JSONDecodeError:
            pass

    # JSON внутри ```json ... ```
    json_match = re.search(r"```json\s*([\s\S]*?)\s*```", text)
    if json_match:
        try:
            return json_module.loads(json_match.group(1))
        except json_module.JSONDecodeError:
            pass

    return None


def find_page_stamp(blocks: List) -> Optional[Dict]:
    """Найти данные штампа на странице (из блока с category_code='stamp')."""
    for block in blocks:
        if getattr(block, "category_code", None) == "stamp":
            stamp_data = parse_stamp_json(block.ocr_text)
            if stamp_data:
                return stamp_data
    return None


def collect_inheritable_stamp_data(pages: List) -> Optional[Dict]:
    """
    Собрать общие поля штампа со всех страниц.
    Для каждого поля выбирается наиболее часто встречающееся значение (мода).
    """
    field_values: Dict[str, List] = {field: [] for field in INHERITABLE_STAMP_FIELDS}

    for page in pages:
        stamp_data = find_page_stamp(page.blocks)
        if stamp_data:
            for field in INHERITABLE_STAMP_FIELDS:
                val = stamp_data.get(field)
                if val:
                    field_values[field].append(val)

    inherited = {}
    for field in INHERITABLE_STAMP_FIELDS:
        values = field_values[field]
        if values:
            counter = Counter(values)
            most_common = counter.most_common(1)[0][0]
            inherited[field] = most_common

    return inherited if inherited else None


# Паттерн для мусорных img тегов от datalab (хеш_img.ext)
DATALAB_IMG_PATTERN = re.compile(
    r'<img[^>]*src=["\']?[a-f0-9]{20,}_img(?:\.[a-z]{3,4})?["\']?[^>]*/?>',
    re.IGNORECASE
)

# Паттерн для markdown-ссылок на мусорные изображения [img:hash_img]
DATALAB_MD_IMG_PATTERN = re.compile(r'\[img:[a-f0-9]{20,}_img\]')


# Паттерн для надёжного определения HTML-контента в тексте
_HTML_TAG_PATTERN = re.compile(
    r'<(?:table|thead|tbody|tr|th|td|p|div|span|h[1-6]|ul|ol|li|br|img|math|sub|sup|pre|input)\b',
    re.IGNORECASE,
)


def contains_html(text: str) -> bool:
    """Надёжное определение HTML-контента (не только startswith('<'))."""
    if not text:
        return False
    return bool(_HTML_TAG_PATTERN.search(text))


def strip_code_fence(text: str) -> str:
    """Убрать ```lang ... ``` обёртку если есть (любой тип фенса)."""
    if not text:
        return text
    m = re.match(r'^```(?:\w+)?\s*\n(.*?)```\s*$', text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text


def sanitize_html(html: str) -> str:
    """
    Очистить HTML от артефактов datalab OCR.

    1. Удаляет мусорные img теги (хеш_img.jpg)
    2. Удаляет осиротевшие закрывающие теги в начале
    3. Удаляет незакрытые открывающие теги в конце
    4. Удаляет вложенные DOCTYPE/html/body артефакты
    5. Удаляет закрывающие </p> без соответствующего открывающего <p>
    """
    if not html:
        return ""

    text = html

    # 0. Удаляем <think>...</think> блоки (reasoning от LLM)
    if '<think' in text.lower() or '</think' in text.lower():
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<think>.*$', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'^.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # 1. Удаляем мусорные img теги от datalab
    text = DATALAB_IMG_PATTERN.sub("", text)

    # 1.5. Нейтрализация BLOCK-маркеров в OCR-контенте.
    # Маркеры BLOCK: XXXX-XXXX-XXX — артефакт визуального разделителя полос,
    # воспроизведённый OCR-движком (например, Datalab помещает текст баннера
    # в page-header div). Не должны присутствовать в содержимом блока.
    text = re.sub(
        r'BLOCK:\s*[A-Z0-9]{2,5}[-\s]*[A-Z0-9]{2,5}[-\s]*[A-Z0-9]{2,5}',
        '', text, flags=re.IGNORECASE
    )

    # 2. Удаляем вложенные DOCTYPE/html/head/body артефакты (бывает внутри блоков)
    text = re.sub(r'<!DOCTYPE\s+html[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<html[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</html\s*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<head[^>]*>.*?</head\s*>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<body[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</body\s*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<div\s+class="page"[^>]*>', '', text, flags=re.IGNORECASE)

    # 3. Удаляем осиротевшие закрывающие теги в начале (могут повторяться)
    while True:
        new_text = re.sub(r'^\s*</[a-z]+>\s*', '', text, flags=re.IGNORECASE)
        if new_text == text:
            break
        text = new_text

    # 4. Удаляем незакрытые открывающие теги в конце
    text = re.sub(r'\s*<p>\s*$', '', text)
    text = re.sub(r'\s*<div[^>]*>\s*$', '', text)

    # 5. Удаляем "висячие" </p> теги - те, которым не предшествует <p>
    # Используем итеративный подход: разбиваем на части по </p> и проверяем баланс
    def remove_orphan_closing_p(html_text: str) -> str:
        """Удалить </p> теги без соответствующего <p>."""
        result = []
        parts = re.split(r'(</p>)', html_text, flags=re.IGNORECASE)
        open_count = 0

        for part in parts:
            if re.match(r'</p>', part, re.IGNORECASE):
                if open_count > 0:
                    result.append(part)
                    open_count -= 1
                # else: пропускаем "висячий" </p>
            else:
                # Считаем открывающие <p> в этой части
                open_count += len(re.findall(r'<p\b[^>]*>', part, re.IGNORECASE))
                result.append(part)

        return ''.join(result)

    text = remove_orphan_closing_p(text)

    # 6. Удаляем незакрытые <p> в конце
    # Проверяем баланс и удаляем последние <p> если они не закрыты
    while True:
        open_p = len(re.findall(r'<p\b[^>]*>', text, re.IGNORECASE))
        close_p = len(re.findall(r'</p>', text, re.IGNORECASE))
        if open_p <= close_p:
            break
        # Удаляем последний незакрытый <p>
        text = re.sub(r'<p\b[^>]*>(?!.*<p\b)', '', text, flags=re.DOTALL | re.IGNORECASE)

    # 7. Удаляем пустые теги
    text = re.sub(r'<p>\s*</p>', '', text, flags=re.IGNORECASE)

    # 8. Нормализуем множественные пустые строки
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 9. Балансировка div тегов.
    # Safety-net: если OCR или парсинг фрагментов нарушил баланс div,
    # добавляем недостающие закрывающие теги или удаляем лишние.
    open_divs = len(re.findall(r'<div\b', text, re.IGNORECASE))
    close_divs = text.count('</div>')
    if open_divs > close_divs:
        text += '</div>' * (open_divs - close_divs)
    elif close_divs > open_divs:
        # Удаляем лишние </div> с конца (остатки обёрточных тегов)
        excess = close_divs - open_divs
        for _ in range(excess):
            idx = text.rfind('</div>')
            if idx >= 0:
                text = text[:idx] + text[idx + 6:]

    return text.strip()


def sanitize_markdown(md: str) -> str:
    """
    Очистить Markdown от артефактов datalab OCR.

    Удаляет ссылки вида [img:hash_img].
    """
    if not md:
        return ""

    # Удаляем мусорные markdown-ссылки на изображения
    text = DATALAB_MD_IMG_PATTERN.sub("", md)

    # Удаляем пустые строки после удаления
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def extract_image_ocr_data(data: dict) -> Dict[str, Any]:
    """
    Извлечь структурированные данные из JSON блока изображения.

    Returns:
        dict с полями: location, zone_name, grid_lines, content_summary,
        detailed_description, clean_ocr_text, key_entities
    """
    # Если есть обёртка "analysis", извлекаем её
    if "analysis" in data and isinstance(data["analysis"], dict):
        data = data["analysis"]

    result = {}

    # Локация
    location = data.get("location")
    if location:
        if isinstance(location, dict):
            result["zone_name"] = location.get("zone_name", "")
            result["grid_lines"] = location.get("grid_lines", "")
        else:
            result["location_text"] = str(location)

    # Описания
    result["content_summary"] = data.get("content_summary", "")
    result["detailed_description"] = data.get("detailed_description", "")

    # Распознанный текст - нормализуем
    clean_ocr = data.get("clean_ocr_text", "")
    if clean_ocr:
        clean_ocr = re.sub(r"•\s*", "", clean_ocr)
        clean_ocr = re.sub(r"\s+", " ", clean_ocr).strip()
    result["clean_ocr_text"] = clean_ocr

    # Ключевые сущности
    key_entities = data.get("key_entities", [])
    if isinstance(key_entities, list):
        result["key_entities"] = key_entities[:20]  # Максимум 20
    else:
        result["key_entities"] = []

    return result


def is_image_ocr_json(data: dict) -> bool:
    """Проверить, является ли JSON данными OCR изображения."""
    if not isinstance(data, dict):
        return False

    # Проверяем характерные поля
    image_fields = ["content_summary", "detailed_description", "clean_ocr_text"]
    return any(
        key in data or (data.get("analysis") and key in data["analysis"])
        for key in image_fields
    )


def is_qwen_ocr_json(data: dict) -> bool:
    """Проверить, является ли JSON ответом Qwen OCR (text/table с content_html)."""
    if not isinstance(data, dict):
        return False
    return "content_html" in data or "stamp_html" in data


def extract_qwen_html(data: dict) -> str:
    """Извлечь HTML из JSON ответа Qwen OCR."""
    return data.get("content_html") or data.get("stamp_html") or ""


def format_stamp_parts(stamp_data: Dict) -> List[tuple]:
    """
    Извлечь части штампа для форматирования.

    Returns:
        Список кортежей (ключ, значение) для форматирования.
    """
    parts = []

    if stamp_data.get("document_code"):
        parts.append(("Шифр", stamp_data["document_code"]))
    if stamp_data.get("stage"):
        parts.append(("Стадия", stamp_data["stage"]))

    # Лист
    sheet_num = stamp_data.get("sheet_number", "")
    total = stamp_data.get("total_sheets", "")
    if sheet_num or total:
        sheet_str = f"{sheet_num} (из {total})" if total else str(sheet_num)
        parts.append(("Лист", sheet_str))

    if stamp_data.get("project_name"):
        parts.append(("Объект", stamp_data["project_name"]))
    if stamp_data.get("sheet_name"):
        parts.append(("Наименование", stamp_data["sheet_name"]))
    if stamp_data.get("organization"):
        parts.append(("Организация", stamp_data["organization"]))

    # Ревизии/изменения
    revisions = stamp_data.get("revisions")
    if revisions:
        if isinstance(revisions, list) and revisions:
            last_rev = revisions[-1] if revisions else {}
            rev_num = last_rev.get("revision_number", "")
            doc_num = last_rev.get("document_number", "")
            rev_date = last_rev.get("date", "")
            if rev_num or doc_num:
                rev_str = f"Изм. {rev_num}"
                if doc_num:
                    rev_str += f" (Док. № {doc_num}"
                    if rev_date:
                        rev_str += f" от {rev_date}"
                    rev_str += ")"
                parts.append(("Статус", rev_str))
        elif isinstance(revisions, str):
            parts.append(("Статус", revisions))

    # Подписи
    signatures = stamp_data.get("signatures")
    if signatures:
        if isinstance(signatures, list):
            sig_parts = []
            for sig in signatures:
                if isinstance(sig, dict):
                    role = sig.get("role", "")
                    name = sig.get("name", "")
                    if role and name:
                        sig_parts.append(f"{role}: {name}")
                elif isinstance(sig, str):
                    sig_parts.append(sig)
            if sig_parts:
                parts.append(("Ответственные", "; ".join(sig_parts)))
        elif isinstance(signatures, str):
            parts.append(("Ответственные", signatures))

    return parts


# =============================================================================
# Функции для работы с dict (используются в result.json / ocr_result_merger)
# =============================================================================


def find_page_stamp_dict(page: Dict) -> Optional[Dict]:
    """Найти JSON штампа на странице (для dict структуры)."""
    for blk in page.get("blocks", []):
        if blk.get("block_type") == "image" and blk.get("category_code") == "stamp":
            return blk.get("ocr_json")
    return None


def collect_inheritable_stamp_data_dict(pages: List[Dict]) -> Optional[Dict]:
    """
    Собрать общие поля штампа со всех страниц (для dict структуры).
    Для каждого поля выбирается наиболее часто встречающееся значение (мода).
    """
    field_values: Dict[str, List] = {field: [] for field in INHERITABLE_STAMP_FIELDS}

    for page in pages:
        stamp_json = find_page_stamp_dict(page)
        if stamp_json:
            for field in INHERITABLE_STAMP_FIELDS:
                val = stamp_json.get(field)
                if val:
                    field_values[field].append(val)

    inherited = {}
    for field in INHERITABLE_STAMP_FIELDS:
        values = field_values[field]
        if values:
            counter = Counter(values)
            most_common = counter.most_common(1)[0][0]
            inherited[field] = most_common

    return inherited if inherited else None


def propagate_stamp_data(page: Dict, inherited_data: Optional[Dict] = None) -> None:
    """
    Распространить данные штампа на все блоки страницы.
    Если на странице есть штамп - мержим его с inherited_data.
    Если штампа нет - используем inherited_data.
    """
    blocks = page.get("blocks", [])
    stamp_json = find_page_stamp_dict(page)

    if stamp_json:
        merged = dict(stamp_json)
        if inherited_data:
            for field in INHERITABLE_STAMP_FIELDS:
                if not merged.get(field):
                    if inherited_data.get(field):
                        merged[field] = inherited_data[field]
        for blk in blocks:
            blk["stamp_data"] = merged
    elif inherited_data:
        for blk in blocks:
            blk["stamp_data"] = inherited_data
