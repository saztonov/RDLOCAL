"""Общая логика для Qwen LM Studio бэкенда (image/stamp OCR)."""
import json as _json
import logging
import os
import re
from typing import Optional, Tuple

from rd_core.ocr_result import make_error, make_non_retriable
from rd_core.ocr.utils import extract_message_text, strip_think_tags, strip_untagged_reasoning

logger = logging.getLogger(__name__)

# Максимальная сторона изображения
QWEN_MAX_IMAGE_SIZE = 3072

# Дефолтные промпты (используются только если caller не передал prompt)
QWEN_DEFAULT_SYSTEM = (
    "You are an expert OCR system. Extract all visible text and structure "
    "from the provided image. Output clean HTML."
)
QWEN_DEFAULT_PROMPT = "Recognize all content in this image and output as HTML."

DEFAULT_BASE_URL = "http://localhost:1234"

# LM Studio native API: конфигурация загрузки модели
QWEN_MODEL_KEY = os.getenv(
    "QWEN_MODEL_KEY",
    "qwen3.5-27b-claude-4.6-opus-reasoning-distilled-v2@q5_k_m",
)
QWEN_LOAD_CONFIG = {
    "context_length": 42000,
    "flash_attention": True,
    "eval_batch_size": 1024,
    "offload_kv_cache_to_gpu": True,
}

# Retry конфигурация
TRANSIENT_CODES = {404, 429, 500, 502, 503, 504}


from rd_core.ocr._lmstudio_helpers import needs_model_reload  # noqa: F401  — re-export


def init_base_url(base_url: Optional[str]) -> str:
    url = (
        base_url
        or os.getenv("QWEN_BASE_URL")
        or os.getenv("CHANDRA_BASE_URL")
        or DEFAULT_BASE_URL
    ).strip()
    return url.rstrip("/")


def build_payload(model_id: str, prompt: Optional[dict], img_b64: str) -> dict:
    """Собрать payload для Qwen LM Studio API.

    В отличие от Chandra, Qwen принимает prompt из аргумента:
    если prompt передан — использует prompt["system"] и prompt["user"],
    иначе — дефолтные промпты.
    """
    if prompt and isinstance(prompt, dict):
        system_prompt = prompt.get("system", QWEN_DEFAULT_SYSTEM)
        user_prompt = prompt.get("user", QWEN_DEFAULT_PROMPT)
    else:
        system_prompt = QWEN_DEFAULT_SYSTEM
        user_prompt = QWEN_DEFAULT_PROMPT

    messages = []
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


# Regex для поиска первого HTML-тега в тексте
_HTML_TAG_RE = re.compile(
    r'<(?:p|table|h[1-6]|div|ul|ol|span|br|hr|math|input|thead|tbody|tr|th|td)\b',
    re.IGNORECASE,
)


def _try_extract_structured_ocr(text: str) -> Optional[str]:
    """Попытка извлечь OCR HTML из structured JSON output."""
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
    """Обрезать reasoning-текст перед HTML в reasoning_content ответе."""
    if not text:
        return text, 0

    stripped = text.lstrip()
    if stripped.startswith('<'):
        return text, 0

    match = _HTML_TAG_RE.search(text)
    if not match:
        return "", len(text)

    reasoning_len = match.start()
    return text[match.start():], reasoning_len


def _normalize_qwen_response(message: dict) -> Tuple[str, str]:
    """Нормализовать ответ Qwen: извлечь OCR-текст из content или reasoning_content."""
    raw_content = message.get("content")
    if isinstance(raw_content, list):
        content = extract_message_text(message)
    elif isinstance(raw_content, str):
        content = raw_content.strip()
    else:
        content = ""

    if content:
        extracted = _try_extract_structured_ocr(content)
        if extracted is not None:
            logger.debug(f"Qwen: structured output из content ({len(extracted)} симв.)")
            return extracted, "content"

        content = strip_untagged_reasoning(content, backend_name="Qwen")
        if not content:
            logger.warning(
                "Qwen: content содержал только reasoning, пробуем reasoning_content"
            )
        else:
            logger.debug(f"Qwen: ответ из content ({len(content)} симв.)")
            return content, "content"

    reasoning = (message.get("reasoning_content") or "").strip()
    if not reasoning:
        return "", "empty"

    logger.info(
        f"Qwen: content пуст, используем reasoning_content ({len(reasoning)} симв.)"
    )

    text = strip_think_tags(reasoning, backend_name="Qwen")
    text, stripped_chars = _strip_reasoning_before_html(text)
    text = text.strip()

    if stripped_chars > 0:
        logger.warning(
            f"Qwen: обрезан reasoning из reasoning_content "
            f"({stripped_chars} симв. удалено, {len(text)} симв. OCR осталось)"
        )

    if text and not _HTML_TAG_RE.search(text):
        logger.warning(
            f"Qwen: reasoning_content после очистки не содержит HTML "
            f"({len(text)} симв.), отклоняем"
        )
        text = ""

    if not text:
        logger.error(
            "Qwen: reasoning_content не содержит валидный OCR",
            extra={"event": "qwen_reasoning_only", "reasoning_len": len(reasoning)},
        )
        return "", "empty"

    logger.warning(
        "Qwen: LM Studio вернул OCR в reasoning_content "
        "(возможная несовместимость версий LM Studio/SDK)"
    )

    return text, "reasoning_content"


def parse_response(response_json: dict) -> str:
    """Парсинг ответа Qwen API. Возвращает текст или сообщение об ошибке."""
    if "choices" not in response_json or not response_json["choices"]:
        err_msg = response_json.get("error", response_json)
        logger.error(f"Qwen: 'choices' missing: {err_msg}")
        return make_error(f"Qwen: некорректный ответ ({err_msg})")

    message = response_json["choices"][0]["message"]
    text, source = _normalize_qwen_response(message)

    if not text:
        logger.warning("Qwen OCR: получен пустой ответ от модели")
        return make_error("Qwen: пустой ответ модели")
    return text


def check_non_retriable_error(status_code: int, response_text: str) -> Optional[str]:
    """Проверить детерминированную ошибку. Возвращает сообщение или None."""
    if status_code == 400 and "context size" in response_text.lower():
        logger.error(f"Qwen API error: {status_code} - {response_text[:500]}")
        return make_non_retriable("контекст превышен — блок слишком большой для модели")
    return None
