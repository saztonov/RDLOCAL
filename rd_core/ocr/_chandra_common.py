"""Общая логика для sync/async Chandra бэкендов."""
import json as _json
import logging
import os
import re
from typing import Optional, Tuple

from rd_core.ocr_result import make_error, make_non_retriable, is_error

# Специальный маркер для length-truncation (не OCR ошибка, а сигнал для retry)
LENGTH_TRUNCATED_PREFIX = "[LENGTH_TRUNCATED]"
# Маркер context overflow (parallel contention) — сигнал для isolated retry
CONTEXT_OVERFLOW_PREFIX = "[CONTEXT_OVERFLOW]"
from rd_core.ocr.utils import extract_message_text, strip_think_tags, strip_untagged_reasoning

logger = logging.getLogger(__name__)

# Промпт из официального репо Chandra (ocr_test.py)
ALLOWED_TAGS = "p, h1, h2, h3, h4, h5, h6, table, thead, tbody, tr, th, td, ul, ol, li, br, sub, sup, div, span, math, mi, mo, mn, msup, msub, mfrac, msqrt, mrow, mover, munder, munderover, mtable, mtr, mtd, mtext, mspace, input"
ALLOWED_ATTRIBUTES = "colspan, rowspan, type, checked, value, data-bbox, data-label"

# Максимальная сторона изображения для Chandra OCR 2 (из chandra/model/util.py: 3072×2048)
CHANDRA_MAX_IMAGE_SIZE = 3072

CHANDRA_DEFAULT_PROMPT = f"""OCR this image. Return a single JSON object: {{"ocr_html": "<HTML content>"}}.

The HTML inside ocr_html must use only these tags [{ALLOWED_TAGS}], and these attributes [{ALLOWED_ATTRIBUTES}].

Guidelines:
* Output format: Return ONLY a valid JSON object {{"ocr_html": "<...>"}}. No explanations, no markdown, no text outside the JSON.
* Inline math: Surround math with <math>...</math> tags. Math expressions should be rendered in KaTeX-compatible LaTeX. Use display for block math.
* Tables: Use colspan and rowspan attributes to match table structure.
* Formatting: Maintain consistent formatting with the image, including spacing, indentation, subscripts/superscripts, and special characters.
* Images: If the image contains photographs, architectural renderings, illustrations, diagrams, stamps, seals, or handwritten signatures, do NOT describe them in any language. Do not write sentences describing what is shown (e.g., "A modern building...", "An architectural rendering...", "A round seal with..."). Do not include <img> tags or data-label="Image" divs. Extract ONLY the text characters visible in the image. If a portion contains only a picture with no text overlay, skip it entirely and produce no output for it.
* Forms: Mark checkboxes and radio buttons properly.
* Text: join lines together properly into paragraphs using <p>...</p> tags. Use <br> tags for line breaks within paragraphs, but only when absolutely necessary to maintain meaning.
* Use the simplest possible HTML structure that accurately represents the content of the block.
* Make sure the text is accurate and easy for a human to read and interpret. Reading order should be correct and natural."""

CHANDRA_DEFAULT_SYSTEM = (
    "You are a specialist OCR system for Russian construction documentation "
    "(GOST, SNiP, SP, TU). You process technical specifications, working drawings, "
    "and Stage P documents. Preserve all dimensions, units of measurement, "
    "reference numbers, and table structures with absolute accuracy. "
    "Never describe images, photographs, seals, or visual scenes. "
    'Output only recognized text as a JSON object: {"ocr_html": "<your HTML here>"}. '
    "Do not output anything outside the JSON object — no explanations, no markdown."
)

DEFAULT_BASE_URL = "http://localhost:1234"

# LM Studio native API: конфигурация загрузки модели
# n_parallel задаётся ТОЛЬКО через UI LM Studio (REST API не поддерживает этот параметр)
CHANDRA_MODEL_KEY = os.getenv("CHANDRA_MODEL_KEY", "chandra-ocr-2")
CHANDRA_LOAD_CONFIG = {
    "context_length": 8000,
    "flash_attention": True,
    "eval_batch_size": 2048,
    "offload_kv_cache_to_gpu": True,
}

# Retry конфигурация
TRANSIENT_CODES = {404, 429, 500, 502, 503, 504}


from rd_core.ocr._lmstudio_helpers import needs_model_reload  # noqa: F401  — re-export


def init_base_url(base_url: Optional[str]) -> str:
    url = (base_url or os.getenv("CHANDRA_BASE_URL") or DEFAULT_BASE_URL).strip()
    return url.rstrip("/")


def build_payload(
    model_id: str,
    prompt: Optional[dict],
    img_b64: str,
    inference_params: Optional[dict] = None,
    context_length: Optional[int] = None,
) -> dict:
    """Собрать payload для Chandra API.

    Args:
        model_id: ID модели в LM Studio.
        prompt: не используется напрямую (Chandra всегда берёт свои промпты).
        img_b64: base64-encoded PNG изображение.
        inference_params: словарь с system_prompt, user_prompt, max_tokens,
            temperature и пр. Если None — используются дефолты из модуля.
    """
    params = inference_params or {}
    system_prompt = params.get("system_prompt", CHANDRA_DEFAULT_SYSTEM)
    user_prompt = params.get("user_prompt", CHANDRA_DEFAULT_PROMPT)

    messages: list[dict[str, object]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img_b64}"},
            },
            {
                "type": "text",
                "text": user_prompt,
            },
        ],
    })

    max_tokens = params.get("max_tokens", 12384)
    # Динамическое ограничение: max_tokens ≤ context_length // 2
    if context_length and max_tokens > context_length // 2:
        capped = context_length // 2
        logger.warning(
            f"Chandra: max_tokens {max_tokens} capped to {capped} "
            f"(context_length={context_length})"
        )
        max_tokens = capped

    payload = {
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": params.get("temperature", 0.1),
        "top_p": params.get("top_p", 0.95),
        "top_k": params.get("top_k", 40),
        "repetition_penalty": params.get("repetition_penalty", 1.1),
        "min_p": params.get("min_p", 0.05),
    }

    # Structured output: response_format (LM Studio json_object mode)
    response_format = params.get("response_format")
    if response_format:
        payload["response_format"] = response_format

    return payload


# Regex для поиска первого HTML-тега в тексте (OCR-контент Chandra)
_HTML_TAG_RE = re.compile(
    r'<(?:p|table|h[1-6]|div|ul|ol|span|br|hr|math|input|thead|tbody|tr|th|td)\b',
    re.IGNORECASE,
)


def _try_extract_structured_ocr(text: str) -> Optional[str]:
    """Попытка извлечь OCR HTML из structured JSON output.

    Если content — валидный JSON с ключом "ocr_html", извлекаем значение.
    Если нет — возвращаем None (fallback на обычный парсинг).
    """
    stripped = text.strip()
    if not (stripped.startswith('{') and stripped.endswith('}')):
        return None
    try:
        data = _json.loads(stripped)
        if isinstance(data, dict) and "ocr_html" in data:
            html = data["ocr_html"]
            if isinstance(html, str) and html.strip():
                return html.strip()
    except (_json.JSONDecodeError, ValueError):
        pass
    return None


def _try_extract_structured_array(text: str) -> Optional[str]:
    """Попытка извлечь OCR HTML из JSON-массива сегментов.

    Chandra иногда возвращает контент как массив элементов:
    [{"data-bbox": "...", "data-label": "...", "html": "..."}, ...]

    Если каждый элемент содержит непустой 'html' ключ, собираем HTML,
    оборачивая каждый фрагмент в <div> с атрибутами data-bbox/data-label.

    Returns:
        Реконструированный HTML или None если формат не подходит.
    """
    stripped = text.strip()
    if not (stripped.startswith('[') and stripped.endswith(']')):
        return None
    try:
        data = _json.loads(stripped)
    except (_json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, list) or len(data) == 0:
        return None

    # Все элементы должны быть dict с непустым 'html' ключом
    if not all(
        isinstance(item, dict)
        and isinstance(item.get("html"), str)
        and item["html"].strip()
        for item in data
    ):
        return None

    parts: list = []
    for item in data:
        html_content = item["html"].strip()
        bbox = item.get("data-bbox", "")
        label = item.get("data-label", "")
        if bbox or label:
            attrs = ""
            if bbox:
                attrs += f' data-bbox="{bbox}"'
            if label:
                attrs += f' data-label="{label}"'
            parts.append(f"<div{attrs}>{html_content}</div>")
        else:
            parts.append(html_content)

    result = "\n".join(parts)
    logger.info(
        f"Chandra: JSON array с html ключами → восстановлен HTML "
        f"({len(data)} элементов, {len(result)} симв.)"
    )
    return result


# ── Извлечение заголовка из reasoning-прозы перед HTML ──────────────

# Явные паттерны, в которых модель называет заголовок таблицы/секции
_TITLE_IN_REASONING_RE = re.compile(
    r'(?:'
    r'[Tt]he\s+title\s+is\s+["\u201c\'](.+?)["\u201d\']'       # The title is "..."
    r'|[Tt]itle:\s*["\u201c\']?(.+?)["\u201d\']?\s*[.\n]'       # Title: ...
    r'|[Hh]eader:\s*["\u201c\']?(.+?)["\u201d\']?\s*[.\n]'      # Header: ...
    r'|\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a:\s*[\xab"\u201c]?(.+?)[\xbb"\u201d]?\s*[.\n]'  # Заголовок: ...
    r'|\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435:\s*[\xab"\u201c]?(.+?)[\xbb"\u201d]?\s*[.\n]'        # Название: ...
    r')',
    re.IGNORECASE,
)

# HTML начинается с таблицы (опционально обёрнутой в div)
_TABLE_START_RE = re.compile(
    r'^\s*(?:<div\b[^>]*>\s*)?<table\b',
    re.IGNORECASE,
)

# Наличие h1-h6 в HTML
_HEADING_IN_HTML_RE = re.compile(r'<h[1-6]\b', re.IGNORECASE)


def _extract_title_from_reasoning(reasoning: str) -> Optional[str]:
    """Извлечь явно указанный заголовок из reasoning-прозы.

    Ищет только explicit patterns: 'The title is "..."', 'Title: ...', и т.д.
    НЕ пытается превращать произвольную reasoning-прозу в OCR-контент.
    """
    m = _TITLE_IN_REASONING_RE.search(reasoning)
    if not m:
        return None
    # Первая непустая группа
    title = next((g for g in m.groups() if g), None)
    if title and len(title.strip()) > 3:
        return title.strip()
    return None


def _strip_reasoning_before_html(text: str) -> Tuple[str, int]:
    """Обрезать reasoning-текст перед HTML в reasoning_content ответе.

    Вызывается ТОЛЬКО для текста из reasoning_content (не content),
    поэтому любой текст до первого HTML-тега — гарантированно reasoning.

    Если reasoning содержит явно названный заголовок (The title is "..."),
    а HTML начинается с таблицы без h1-h6, заголовок восстанавливается
    как <div data-label="Section-Header"><h1>...</h1></div>.

    Returns:
        (cleaned_text, stripped_chars) — очищенный текст и кол-во удалённых символов.
    """
    if not text:
        return text, 0

    stripped = text.lstrip()
    if stripped.startswith('<'):
        return text, 0

    match = _HTML_TAG_RE.search(text)
    if not match:
        return "", len(text)  # нет HTML в reasoning_content → чистый reasoning

    reasoning_part = text[:match.start()]
    html_part = text[match.start():]
    reasoning_len = len(reasoning_part)

    # Попытка спасти заголовок из reasoning, только если:
    # 1. HTML начинается с таблицы (или div>table)
    # 2. В HTML ещё нет h1-h6
    if (_TABLE_START_RE.match(html_part)
            and not _HEADING_IN_HTML_RE.search(html_part)):
        title = _extract_title_from_reasoning(reasoning_part)
        if title:
            html_part = (
                f'<div data-label="Section-Header"><h1>{title}</h1></div>\n'
                + html_part
            )
            logger.info(
                f"Chandra: спасён заголовок из reasoning: {title!r}"
            )

    return html_part, reasoning_len


def _normalize_chandra_response(message: dict) -> Tuple[str, str]:
    """Нормализовать ответ Chandra: извлечь OCR-текст из content или reasoning_content.

    Порядок: structured JSON → content (str/list) → reasoning_content (с очисткой reasoning).
    Структурированное логирование источника и метрик.

    Returns:
        (text, source) — OCR-текст и источник ("content" | "reasoning_content" | "empty").
    """
    # 1. Извлечь content (поддержка str и list форматов)
    raw_content = message.get("content")
    if isinstance(raw_content, list):
        content = extract_message_text(message)
    elif isinstance(raw_content, str):
        content = raw_content.strip()
    else:
        content = ""

    if content:
        # Попытка парсинга structured output (JSON с ocr_html)
        extracted = _try_extract_structured_ocr(content)
        if extracted is not None:
            logger.debug(
                f"Chandra: structured output из content ({len(extracted)} симв.)",
            )
            return extracted, "content"

        # Попытка парсинга JSON array с html ключами (legacy format)
        extracted_array = _try_extract_structured_array(content)
        if extracted_array is not None:
            logger.debug(
                f"Chandra: structured array из content ({len(extracted_array)} симв.)",
            )
            return extracted_array, "content"

        # Очистка reasoning без <think> тегов в content
        content = strip_untagged_reasoning(content, backend_name="Chandra")
        if not content:
            logger.warning(
                "Chandra: content содержал только reasoning, "
                "пробуем reasoning_content",
            )
        else:
            logger.debug(
                f"Chandra: ответ из content ({len(content)} симв.)",
            )
            return content, "content"

    # 2. Fallback: reasoning_content
    reasoning = (message.get("reasoning_content") or "").strip()
    if not reasoning:
        return "", "empty"

    logger.info(
        f"Chandra: content пуст, используем reasoning_content "
        f"({len(reasoning)} симв.)"
    )

    # Очистка: <think> теги → reasoning-проза перед HTML
    text = strip_think_tags(reasoning, backend_name="Chandra")
    text, stripped_chars = _strip_reasoning_before_html(text)
    text = text.strip()

    if stripped_chars > 0:
        logger.warning(
            f"Chandra: обрезан reasoning из reasoning_content "
            f"({stripped_chars} симв. удалено, {len(text)} симв. OCR осталось)",
        )

    # Валидация: после очистки reasoning текст должен содержать HTML
    if text and not _HTML_TAG_RE.search(text):
        logger.warning(
            f"Chandra: reasoning_content после очистки не содержит HTML "
            f"({len(text)} симв.), отклоняем",
        )
        text = ""

    if not text:
        logger.error(
            "Chandra: reasoning_content не содержит валидный OCR "
            "(чистый reasoning без HTML), блок будет помечен как ошибка",
            extra={"event": "chandra_reasoning_only", "reasoning_len": len(reasoning)},
        )
        return "", "empty"

    logger.warning(
        "Chandra: LM Studio вернул OCR в reasoning_content "
        "(возможная несовместимость версий LM Studio/SDK)",
    )

    return text, "reasoning_content"


def parse_response(response_json: dict) -> str:
    """Парсинг ответа Chandra API. Возвращает текст или сообщение об ошибке.

    LM Studio может возвращать OCR-результат в reasoning_content вместо content
    (поведение reasoning/thinking моделей). Нормализация через
    _normalize_chandra_response() обеспечивает:
    - Поддержку str и list форматов content
    - Автоматическую очистку reasoning-прозы из reasoning_content
    - Структурированное логирование источника ответа

    При finish_reason="length" возвращает маркер LENGTH_TRUNCATED_PREFIX + partial text,
    чтобы вызывающий код мог выполнить retry с повышенным max_tokens.
    """
    if "choices" not in response_json or not response_json["choices"]:
        err_msg = response_json.get("error", response_json)
        logger.error(f"Chandra: 'choices' missing: {err_msg}")
        return make_error(f"Chandra: некорректный ответ ({err_msg})")

    choice = response_json["choices"][0]
    finish_reason = choice.get("finish_reason", "")

    # Логируем usage-метрики
    usage = response_json.get("usage", {})
    if usage:
        logger.info(
            f"Chandra usage: prompt_tokens={usage.get('prompt_tokens', '?')}, "
            f"completion_tokens={usage.get('completion_tokens', '?')}, "
            f"reasoning_tokens={usage.get('reasoning_tokens', '?')}, "
            f"finish_reason={finish_reason}"
        )

    message = choice["message"]
    text, source = _normalize_chandra_response(message)

    if not text:
        logger.warning("Chandra OCR: получен пустой ответ от модели")
        return make_error("Chandra: пустой ответ модели")

    # finish_reason="length" → ответ обрезан по max_tokens
    if finish_reason == "length":
        logger.warning(
            f"Chandra: finish_reason=length — ответ обрезан "
            f"(completion_tokens={usage.get('completion_tokens', '?')}, "
            f"source={source}, text_len={len(text)})"
        )
        return f"{LENGTH_TRUNCATED_PREFIX}{text}"

    return text


def check_non_retriable_error(status_code: int, response_text: str) -> Optional[str]:
    """Проверить детерминированную ошибку. Возвращает сообщение или None.

    Context overflow возвращает CONTEXT_OVERFLOW_PREFIX (не non-retriable),
    чтобы вызывающий код мог попробовать isolated retry.
    """
    if status_code == 400 and "context size" in response_text.lower():
        logger.warning(
            f"Chandra: context overflow (may be parallel contention): "
            f"{response_text[:300]}"
        )
        return CONTEXT_OVERFLOW_PREFIX
    return None
