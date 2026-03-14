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

# ── Промпты: TEXT / TABLE ───────────────────────────────────────────
QWEN_TEXT_SYSTEM = (
    "Ты — специализированная OCR-система для распознавания российской "
    "строительной документации: ГОСТ, СНиП, СП, ТУ, рабочие чертежи, стадия П. "
    "Твоя задача — максимально точно распознать содержимое переданного блока. "
    "Сохраняй все размеры, единицы измерения, номера ссылок и структуру таблиц "
    "с абсолютной точностью. "
    "Выводи результат СТРОГО в формате JSON. Никакого текста вне JSON."
)

QWEN_TEXT_PROMPT = (
    "Внимательно проанализируй структуру переданного блока "
    "из строительного чертежа или спецификации.\n\n"
    "Это фрагмент технической документации (рабочая документация / стадия П). "
    "Блок может содержать:\n"
    "— текстовые параграфы с техническими требованиями\n"
    "— таблицы спецификаций с размерами, материалами, количествами\n"
    "— примечания и ссылки на нормативные документы (ГОСТ, СНиП, СП)\n"
    "— математические формулы, индексы, степени\n\n"
    "Максимально точно распознай весь текст, сохраняя оригинальную структуру.\n\n"
    'Верни результат СТРОГО как JSON объект:\n'
    '{"type": "text"|"table"|"mixed", '
    '"content_html": "<p>...</p> или <table>...</table>", '
    '"confidence": 0.0-1.0}\n\n'
    "Правила для content_html:\n"
    f"* Теги: [{ALLOWED_TAGS}], атрибуты: [{ALLOWED_ATTRIBUTES}]\n"
    "* Таблицы: colspan/rowspan для точной структуры\n"
    "* Математика: <math>...</math> (KaTeX-совместимый LaTeX)\n"
    "* Текст: <p>...</p>, <br> только при необходимости\n"
    "* Порядок чтения — корректный и естественный\n"
    "* Не добавляй ничего от себя — только то, что видишь"
)

# ── Промпты: STAMP ─────────────────────────────────────────────────
QWEN_STAMP_SYSTEM = (
    "Ты — специалист по чтению штампов (основных надписей) из российской "
    "строительной документации. Ты работаешь с рабочей документацией и стадией П. "
    "Штамп содержит метаинформацию: организация, проект, стадия, лист, подписи. "
    "Извлекай ВСЮ информацию с максимальной точностью. "
    "Выводи результат СТРОГО в формате JSON. Никакого текста вне JSON."
)

QWEN_STAMP_PROMPT = (
    "Это штамп (основная надпись) из строительного чертежа.\n\n"
    "Извлеки ВСЮ информацию и верни СТРОГО как JSON объект:\n"
    '{"organization": "", "project_name": "", "project_code": "", '
    '"document_name": "", "stage": "П|Р", '
    '"sheet_number": "", "total_sheets": "", '
    '"scale": "", "format": "", '
    '"signatures": [{"role": "", "name": "", "date": ""}], '
    '"changes": [{"number": "", "name": "", "date": ""}], '
    '"stamp_html": "<table>...</table>", '
    '"confidence": 0.0-1.0}\n\n'
    f"Для stamp_html используй теги: [{ALLOWED_TAGS}], атрибуты: [{ALLOWED_ATTRIBUTES}]\n"
    "stamp_html должен точно воспроизводить визуальную структуру штампа.\n"
    "Используй colspan/rowspan для ячеек штампа.\n"
    "Не добавляй ничего от себя — только то, что видишь."
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


def build_payload(model_id: str, mode: str, img_b64: str) -> dict:
    """Собрать payload для Qwen API."""
    system_prompt, user_prompt = get_prompts(mode)

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
