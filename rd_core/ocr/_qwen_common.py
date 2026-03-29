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
    "qwen3.5-27b-claude-4.6-opus-reasoning-distilled-v2@q4_k_m",
)
QWEN_LOAD_CONFIG = {
    "context_length": 32000,
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


def build_payload(
    model_id: str,
    prompt: Optional[dict],
    img_b64: str,
    inference_params: Optional[dict] = None,
) -> dict:
    """Собрать payload для Qwen LM Studio API.

    Args:
        model_id: ID модели в LM Studio.
        prompt: dict с ключами "system" и "user" (из worker_prompts / config.yaml).
        img_b64: base64-encoded PNG изображение.
        inference_params: словарь с default_system_prompt, default_user_prompt,
            max_tokens, temperature и пр. Если None — используются дефолты из модуля.
    """
    params = inference_params or {}
    default_sys = params.get("default_system_prompt", QWEN_DEFAULT_SYSTEM)
    default_usr = params.get("default_user_prompt", QWEN_DEFAULT_PROMPT)

    if prompt and isinstance(prompt, dict):
        system_prompt = prompt.get("system", default_sys)
        user_prompt = prompt.get("user", default_usr)
    else:
        system_prompt = default_sys
        user_prompt = default_usr

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

    payload = {
        "model": model_id,
        "messages": messages,
        "max_tokens": params.get("max_tokens", 12384),
        "temperature": params.get("temperature", 0.1),
        "top_p": params.get("top_p", 0.95),
        "top_k": params.get("top_k", 40),
        "repetition_penalty": params.get("repetition_penalty", 1.1),
        "min_p": params.get("min_p", 0.05),
    }

    # Structured output: response_format с JSON schema (LM Studio)
    if params.get("response_format"):
        payload["response_format"] = params["response_format"]

    return payload


# Regex для поиска первого HTML-тега в тексте
_HTML_TAG_RE = re.compile(
    r'<(?:p|table|h[1-6]|div|ul|ol|span|br|hr|math|input|thead|tbody|tr|th|td)\b',
    re.IGNORECASE,
)


_KNOWN_STAMP_KEYS = frozenset({"document_code", "project_name", "sheet_name"})
_KNOWN_IMAGE_KEYS = frozenset({"fragment_type", "content_summary", "detailed_description"})


def _try_extract_structured_ocr(text: str) -> Optional[str]:
    """Попытка извлечь OCR из structured JSON output (HTML, stamp, image)."""
    stripped = text.strip()
    if not (stripped.startswith('{') and stripped.endswith('}')):
        return None
    try:
        data = _json.loads(stripped)
        if not isinstance(data, dict):
            return None
        # HTML из structured output
        if "ocr_html" in data:
            html = data["ocr_html"]
            if isinstance(html, str) and html.strip():
                return html.strip()
        # Stamp / Image JSON (известные ключи схемы)
        if data.keys() & (_KNOWN_STAMP_KEYS | _KNOWN_IMAGE_KEYS):
            return stripped
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


def _try_extract_json_from_reasoning(text: str) -> Optional[str]:
    """Извлечь первый валидный JSON объект из reasoning-текста.

    Reasoning-модели (qwen3.5-9b) могут возвращать JSON в reasoning_content,
    окружённый текстом размышлений. Ищем первый `{...}` верхнего уровня.
    """
    if not text:
        return None
    stripped = text.strip()

    # Быстрый путь: весь текст — чистый JSON
    if stripped.startswith('{') and stripped.endswith('}'):
        try:
            _json.loads(stripped)
            return stripped
        except _json.JSONDecodeError:
            pass

    # Поиск первого JSON объекта в тексте
    brace_start = stripped.find('{')
    if brace_start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False
    for i in range(brace_start, len(stripped)):
        c = stripped[i]
        if escape_next:
            escape_next = False
            continue
        if c == '\\' and in_string:
            escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                candidate = stripped[brace_start:i + 1]
                try:
                    obj = _json.loads(candidate)
                    if isinstance(obj, dict) and obj.keys() & (
                        _KNOWN_STAMP_KEYS | _KNOWN_IMAGE_KEYS | {"ocr_html"}
                    ):
                        return candidate
                except _json.JSONDecodeError:
                    pass
                break

    return None


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

    # LM Studio 0.3.23+ использует message.reasoning, старые версии — reasoning_content
    reasoning = (
        message.get("reasoning")
        or message.get("reasoning_content")
        or ""
    ).strip()
    if not reasoning:
        return "", "empty"

    reasoning_field = "reasoning" if message.get("reasoning") else "reasoning_content"
    logger.info(
        f"Qwen: content пуст, используем {reasoning_field} ({len(reasoning)} симв.)"
    )

    text = strip_think_tags(reasoning, backend_name="Qwen")

    # Попытка извлечь JSON из reasoning (stamp/image structured output)
    json_result = _try_extract_json_from_reasoning(text)
    if json_result is not None:
        logger.info(
            f"Qwen: извлечён JSON из {reasoning_field} ({len(json_result)} симв.)"
        )
        return json_result, reasoning_field

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
