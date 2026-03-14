"""Общая логика для sync/async Qwen бэкендов."""
import json
import logging
import os
import re
from typing import Optional, Tuple

from rd_core.ocr._chandra_common import ALLOWED_ATTRIBUTES, ALLOWED_TAGS
from rd_core.ocr.utils import strip_think_tags, strip_untagged_reasoning

logger = logging.getLogger(__name__)

# ── Модель и конфиг загрузки ────────────────────────────────────────
QWEN_MODEL_KEY = os.getenv("QWEN_MODEL_KEY", "qwen/qwen3.5-9b")
QWEN_LOAD_CONFIG = {
    "context_length": 65536,
    "flash_attention": True,
    "eval_batch_size": 512,
}

# ── Промпты: TEXT / TABLE (fallback) ────────────────────────────────
QWEN_TEXT_SYSTEM = (
    "You are a specialized OCR system for recognizing Russian construction "
    "documentation: GOST, SNiP, SP, TU, working drawings (РД), preliminary "
    "design (стадия П). Your task is to recognize the content of the provided "
    "block with maximum accuracy. Preserve all dimensions, units of measurement, "
    "reference numbers, and table structure with absolute precision. "
    "Output the result STRICTLY in JSON format. No text outside JSON. "
    "All recognized text content MUST be in Russian (as it appears in the original document)."
)

QWEN_TEXT_PROMPT = (
    "Carefully analyze the structure of the provided block "
    "from a construction drawing or specification.\n\n"
    "This is a fragment of technical documentation (working documentation / стадия П). "
    "The block may contain:\n"
    "— text paragraphs with technical requirements\n"
    "— specification tables with dimensions, materials, quantities\n"
    "— notes and references to regulatory documents (ГОСТ, СНиП, СП)\n"
    "— mathematical formulas, indices, exponents\n\n"
    "Recognize all text with maximum accuracy, preserving the original structure. "
    "All recognized text must remain in Russian as it appears in the document.\n\n"
    'Return the result STRICTLY as a JSON object:\n'
    '{"type": "text"|"table"|"mixed", '
    '"content_html": "<p>...</p> or <table>...</table>", '
    '"confidence": 0.0-1.0}\n\n'
    "Rules for content_html:\n"
    f"* Tags: [{ALLOWED_TAGS}], attributes: [{ALLOWED_ATTRIBUTES}]\n"
    "* Tables: use colspan/rowspan for accurate structure\n"
    "* Math: <math>...</math> (KaTeX-compatible LaTeX)\n"
    "* Text: <p>...</p>, <br> only when necessary\n"
    "* Reading order must be correct and natural\n"
    "* Do not add anything of your own — only what you see"
)

# ── Промпты: STAMP (fallback) ──────────────────────────────────────
QWEN_STAMP_SYSTEM = (
    "You are a specialist in reading title blocks (основные надписи) from Russian "
    "construction documentation. You work with working documentation (РД) and "
    "preliminary design (стадия П). The title block contains metadata: organization, "
    "project, stage, sheet, signatures. Extract ALL information with maximum accuracy. "
    "Output the result STRICTLY in JSON format. No text outside JSON. "
    "All extracted values MUST be in Russian as they appear in the original document."
)

QWEN_STAMP_PROMPT = (
    "This is a title block (основная надпись) from a construction drawing.\n\n"
    "Extract ALL information and return STRICTLY as a JSON object:\n"
    '{"organization": "", "project_name": "", "project_code": "", '
    '"document_name": "", "stage": "П|Р", '
    '"sheet_number": "", "total_sheets": "", '
    '"scale": "", "format": "", '
    '"signatures": [{"role": "", "name": "", "date": ""}], '
    '"changes": [{"number": "", "name": "", "date": ""}], '
    '"stamp_html": "<table>...</table>", '
    '"confidence": 0.0-1.0}\n\n'
    "All text values must be in Russian as they appear in the original document.\n"
    f"For stamp_html use tags: [{ALLOWED_TAGS}], attributes: [{ALLOWED_ATTRIBUTES}]\n"
    "stamp_html must accurately reproduce the visual structure of the title block.\n"
    "Use colspan/rowspan for title block cells.\n"
    "Do not add anything of your own — only what you see."
)

# Retry конфигурация (общая с Chandra)
TRANSIENT_CODES = {404, 429, 500, 502, 503, 504}


def init_base_url(base_url: Optional[str]) -> str:
    return base_url or os.getenv("QWEN_BASE_URL") or os.getenv("CHANDRA_BASE_URL", "")


def get_prompts(mode: str) -> Tuple[str, str]:
    """Возвращает (system_prompt, user_prompt) по режиму."""
    if mode == "stamp":
        return QWEN_STAMP_SYSTEM, QWEN_STAMP_PROMPT
    return QWEN_TEXT_SYSTEM, QWEN_TEXT_PROMPT


def build_payload(
    model_id: str,
    mode: str,
    img_b64: str,
    system_prompt: Optional[str] = None,
    user_prompt: Optional[str] = None,
) -> dict:
    """Собрать payload для Qwen API. Внешние промпты имеют приоритет над хардкоженными."""
    if system_prompt or user_prompt:
        sys_p = system_prompt or get_prompts(mode)[0]
        usr_p = user_prompt or get_prompts(mode)[1]
    else:
        sys_p, usr_p = get_prompts(mode)

    messages = []
    if sys_p:
        messages.append({"role": "system", "content": sys_p})
    messages.append({
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img_b64}"},
            },
            {
                "type": "text",
                "text": usr_p,
            },
        ],
    })

    return {
        "model": model_id,
        "messages": messages,
        "max_tokens": 16384,
        "temperature": 0.15,
        "top_p": 0.8,
        "top_k": 20,
        "repetition_penalty": 1.05,
    }


def extract_json_response(text: str) -> str:
    """Извлечь JSON из ответа модели. Если не удалось — вернуть как есть."""
    md_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if md_match:
        candidate = md_match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
    brace_match = re.search(r'\{.*\}', text, re.DOTALL)
    if brace_match:
        candidate = brace_match.group(0)
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
    return text


def parse_response(response_json: dict, mode: str, backend_name: str = "Qwen") -> str:
    """Парсинг ответа Qwen API с обработкой reasoning."""
    if "choices" not in response_json or not response_json["choices"]:
        err_msg = response_json.get("error", response_json)
        logger.error(f"Qwen: 'choices' missing: {err_msg}")
        return f"[Ошибка Qwen: некорректный ответ ({err_msg})]"

    message = response_json["choices"][0]["message"]

    # LM Studio v0.3.23+ выносит thinking в отдельное поле
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    raw_text = message.get("content", "").strip()

    if reasoning:
        logger.info(
            f"{backend_name}/{mode}: reasoning в отдельном поле "
            f"({len(reasoning)} симв.), content={len(raw_text)} симв."
        )

    if not raw_text:
        logger.warning(f"{backend_name} OCR: получен пустой ответ от модели")
        return "[Ошибка Qwen: пустой ответ модели]"

    text = strip_think_tags(raw_text, backend_name=f"{backend_name}/{mode}")
    text = strip_untagged_reasoning(text, backend_name=f"{backend_name}/{mode}")
    text = extract_json_response(text)

    if not text:
        logger.warning(
            f"{backend_name} OCR ({mode}): ответ только reasoning "
            f"({len(raw_text)} симв.), HTML не сгенерирован"
        )
        return "[Ошибка Qwen: ответ содержит только reasoning]"
    return text


def check_non_retriable_error(status_code: int, response_text: str) -> Optional[str]:
    """Проверить детерминированную ошибку."""
    if status_code == 400 and "context size" in response_text.lower():
        logger.error(f"Qwen API error: {status_code} - {response_text[:500]}")
        return "[НеПовторяемая ошибка: контекст превышен — блок слишком большой для модели]"
    return None
