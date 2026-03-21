"""Общая логика для sync/async Chandra бэкендов."""
import logging
import os
from typing import Optional, Tuple

from rd_core.ocr_result import make_error, make_non_retriable

logger = logging.getLogger(__name__)

# Промпт из официального репо Chandra (ocr_test.py)
ALLOWED_TAGS = "p, h1, h2, h3, h4, h5, h6, table, thead, tbody, tr, th, td, ul, ol, li, br, sub, sup, div, span, img, math, mi, mo, mn, msup, msub, mfrac, msqrt, mrow, mover, munder, munderover, mtable, mtr, mtd, mtext, mspace, input"
ALLOWED_ATTRIBUTES = "colspan, rowspan, alt, type, checked, value, data-bbox, data-label"

CHANDRA_DEFAULT_PROMPT = f"""OCR this image to HTML.

Only use these tags [{ALLOWED_TAGS}], and these attributes [{ALLOWED_ATTRIBUTES}].

Guidelines:
* Inline math: Surround math with <math>...</math> tags. Math expressions should be rendered in KaTeX-compatible LaTeX. Use display for block math.
* Tables: Use colspan and rowspan attributes to match table structure.
* Formatting: Maintain consistent formatting with the image, including spacing, indentation, subscripts/superscripts, and special characters.
* Images: Include a description of any images in the alt attribute of an <img> tag. Do not fill out the src property.
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

DEFAULT_BASE_URL = "https://louvred-madie-gigglier.ngrok-free.dev"

# LM Studio native API: конфигурация загрузки модели
# n_parallel задаётся ТОЛЬКО через UI LM Studio (REST API не поддерживает этот параметр)
CHANDRA_MODEL_KEY = os.getenv("CHANDRA_MODEL_KEY", "chandra-ocr-2-GGUF")
CHANDRA_LOAD_CONFIG = {
    "context_length": 36601,
    "flash_attention": True,
    "eval_batch_size": 512,
    "offload_kv_cache_to_gpu": True,
}

# Retry конфигурация
TRANSIENT_CODES = {404, 429, 500, 502, 503, 504}


def needs_model_reload(loaded_instances: list, required_context: int) -> Tuple[bool, str]:
    """Проверяет нужна ли перезагрузка модели из-за несовпадения context_length."""
    if not loaded_instances:
        return True, "модель не загружена"
    for inst in loaded_instances:
        inst_id = inst.get("id", "unknown")
        ctx = inst.get("context_length")
        if ctx is None:
            return True, f"instance {inst_id}: context_length недоступен в API"
        if ctx != required_context:
            return True, f"instance {inst_id}: context_length={ctx}, требуется {required_context}"
    return False, f"context_length={required_context} OK"


def init_base_url(base_url: Optional[str]) -> str:
    return base_url or os.getenv("CHANDRA_BASE_URL", DEFAULT_BASE_URL)


def get_ngrok_auth() -> Optional[tuple]:
    auth_user = os.getenv("NGROK_AUTH_USER")
    auth_pass = os.getenv("NGROK_AUTH_PASS")
    return (auth_user, auth_pass) if auth_user and auth_pass else None


def build_payload(model_id: str, prompt: Optional[dict], img_b64: str) -> dict:
    """Собрать payload для Chandra API.

    Chandra всегда использует специализированные промпты для строительной
    документации (CHANDRA_DEFAULT_SYSTEM / CHANDRA_DEFAULT_PROMPT),
    игнорируя generic промпты из worker_prompts.
    """
    system_prompt = CHANDRA_DEFAULT_SYSTEM

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


def parse_response(response_json: dict) -> str:
    """Парсинг ответа Chandra API. Возвращает текст или сообщение об ошибке."""
    if "choices" not in response_json or not response_json["choices"]:
        err_msg = response_json.get("error", response_json)
        logger.error(f"Chandra: 'choices' missing: {err_msg}")
        return make_error(f"Chandra: некорректный ответ ({err_msg})")

    text = response_json["choices"][0]["message"]["content"].strip()
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
