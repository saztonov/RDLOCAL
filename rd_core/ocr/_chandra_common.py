"""Общая логика для sync/async Chandra бэкендов."""
import json as _json
import logging
import os
import re
from typing import Optional, Tuple

from rd_core.ocr_result import make_error, make_non_retriable
from rd_core.ocr.utils import extract_message_text, strip_think_tags, strip_untagged_reasoning

logger = logging.getLogger(__name__)

# Промпт из официального репо Chandra (ocr_test.py)
ALLOWED_TAGS = "p, h1, h2, h3, h4, h5, h6, table, thead, tbody, tr, th, td, ul, ol, li, br, sub, sup, div, span, math, mi, mo, mn, msup, msub, mfrac, msqrt, mrow, mover, munder, munderover, mtable, mtr, mtd, mtext, mspace, input"
ALLOWED_ATTRIBUTES = "colspan, rowspan, type, checked, value, data-bbox, data-label"

# Максимальная сторона изображения для Chandra OCR 2 (из chandra/model/util.py: 3072×2048)
CHANDRA_MAX_IMAGE_SIZE = 3072

CHANDRA_DEFAULT_PROMPT = f"""OCR this image to HTML.

Only use these tags [{ALLOWED_TAGS}], and these attributes [{ALLOWED_ATTRIBUTES}].

Guidelines:
* Inline math: Surround math with <math>...</math> tags. Math expressions should be rendered in KaTeX-compatible LaTeX. Use display for block math.
* Tables: Use colspan and rowspan attributes to match table structure.
* Formatting: Maintain consistent formatting with the image, including spacing, indentation, subscripts/superscripts, and special characters.
* Images: Ignore any images, diagrams, or illustrations entirely. Do not include <img> tags in the output. Focus only on text content.
* Forms: Mark checkboxes and radio buttons properly.
* Text: join lines together properly into paragraphs using <p>...</p> tags. Use <br> tags for line breaks within paragraphs, but only when absolutely necessary to maintain meaning.
* Use the simplest possible HTML structure that accurately represents the content of the block.
* Make sure the text is accurate and easy for a human to read and interpret. Reading order should be correct and natural."""

CHANDRA_DEFAULT_SYSTEM = (
    "You are a specialist OCR system for Russian construction documentation "
    "(GOST, SNiP, SP, TU). You process technical specifications, working drawings, "
    "and Stage P documents. Preserve all dimensions, units of measurement, "
    "reference numbers, and table structures with absolute accuracy. "
    "Output clean HTML."
)

DEFAULT_BASE_URL = "http://localhost:1234"

# LM Studio native API: конфигурация загрузки модели
# n_parallel задаётся ТОЛЬКО через UI LM Studio (REST API не поддерживает этот параметр)
CHANDRA_MODEL_KEY = os.getenv("CHANDRA_MODEL_KEY", "chandra-ocr-2")
CHANDRA_LOAD_CONFIG = {
    "context_length": 57000,
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


def build_payload(model_id: str, prompt: Optional[dict], img_b64: str) -> dict:
    """Собрать payload для Chandra API.

    Chandra всегда использует специализированные промпты для строительной
    документации (CHANDRA_DEFAULT_SYSTEM / CHANDRA_DEFAULT_PROMPT),
    игнорируя generic промпты из worker_prompts.
    """
    system_prompt = CHANDRA_DEFAULT_SYSTEM

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
                "text": CHANDRA_DEFAULT_PROMPT,
            },
        ],
    })

    return {
        "model": model_id,
        "messages": messages,
        "max_tokens": 12384,
        "temperature": 0.1,
        "top_p": 0.95,
        "top_k": 40,
        "repetition_penalty": 1.1,
        "min_p": 0.05,
    }


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


def _strip_reasoning_before_html(text: str) -> Tuple[str, int]:
    """Обрезать reasoning-текст перед HTML в reasoning_content ответе.

    Вызывается ТОЛЬКО для текста из reasoning_content (не content),
    поэтому любой текст до первого HTML-тега — гарантированно reasoning.

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

    reasoning_len = match.start()
    return text[match.start():], reasoning_len


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
    """
    if "choices" not in response_json or not response_json["choices"]:
        err_msg = response_json.get("error", response_json)
        logger.error(f"Chandra: 'choices' missing: {err_msg}")
        return make_error(f"Chandra: некорректный ответ ({err_msg})")

    message = response_json["choices"][0]["message"]
    text, source = _normalize_chandra_response(message)

    if not text:
        logger.warning("Chandra OCR: получен пустой ответ от модели")
        return make_error("Chandra: пустой ответ модели")
    return text


def check_non_retriable_error(status_code: int, response_text: str) -> Optional[str]:
    """Проверить детерминированную ошибку. Возвращает сообщение или None."""
    if status_code == 400 and "context size" in response_text.lower():
        logger.error(f"Chandra API error: {status_code} - {response_text[:500]}")
        return make_non_retriable("контекст превышен — блок слишком большой для модели")
    return None
