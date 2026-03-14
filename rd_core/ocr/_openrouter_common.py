"""Общая логика для sync/async OpenRouter бэкендов."""
import base64
import logging
import os
from typing import List, Optional, Tuple

from PIL import Image

from rd_core.ocr.utils import extract_message_text, image_to_base64, image_to_pdf_base64

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai"
DEFAULT_SYSTEM = "You are an expert design engineer and automation specialist. Your task is to analyze technical drawings and extract data into structured JSON or Markdown formats with 100% accuracy. Do not omit details. Do not hallucinate values."
DEFAULT_USER = "Распознай содержимое изображения."
DEFAULT_MODEL = "qwen/qwen3-vl-30b-a3b-instruct"

# Shared across sync/async instances
_providers_cache: dict = {}


def init_params(api_key: str, model_name: str, base_url: Optional[str]) -> Tuple[str, str, str]:
    """Нормализация параметров __init__."""
    resolved_url = base_url or os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL)
    return api_key, model_name or DEFAULT_MODEL, resolved_url


def supports_pdf_input(model_name: str) -> bool:
    return "gemini-3" in model_name.lower()


def parse_providers_from_response(models_data: list, model_name: str) -> Optional[List[str]]:
    """Извлечь и отсортировать провайдеров по цене из ответа /api/v1/models."""
    model_info = None
    for m in models_data:
        if m.get("id") == model_name:
            model_info = m
            break

    if not model_info:
        logger.warning(f"Модель {model_name} не найдена в списке")
        return None

    pricing = model_info.get("endpoint", {}).get("pricing", {})
    if not pricing:
        pricing = model_info.get("pricing", {})

    providers_pricing = []
    if isinstance(pricing, dict) and "providers" in pricing:
        for provider_id, pdata in pricing.get("providers", {}).items():
            prompt_cost = float(pdata.get("prompt", 0) or 0)
            completion_cost = float(pdata.get("completion", 0) or 0)
            providers_pricing.append((provider_id, prompt_cost + completion_cost))
    elif isinstance(pricing, list):
        for pdata in pricing:
            provider_id = pdata.get("provider_id") or pdata.get("provider")
            if provider_id:
                prompt_cost = float(pdata.get("prompt", 0) or 0)
                completion_cost = float(pdata.get("completion", 0) or 0)
                providers_pricing.append((provider_id, prompt_cost + completion_cost))

    if not providers_pricing:
        logger.info("Pricing по провайдерам не найден, используется дефолт")
        return None

    providers_pricing.sort(key=lambda x: x[1])
    provider_order = [p[0] for p in providers_pricing]
    logger.info(f"Провайдеры для {model_name} (по цене): {provider_order}")
    _providers_cache[model_name] = provider_order
    return provider_order


def prepare_prompts(prompt: Optional[dict]) -> Tuple[str, str]:
    """Извлечь system/user промпты."""
    if prompt and isinstance(prompt, dict):
        system_prompt = prompt.get("system", "") or DEFAULT_SYSTEM
        user_prompt = prompt.get("user", "") or DEFAULT_USER
    else:
        system_prompt = DEFAULT_SYSTEM
        user_prompt = DEFAULT_USER
    return system_prompt, user_prompt


def detect_json_mode(system_prompt: str, user_prompt: str, json_mode: Optional[bool]) -> bool:
    """Авто-определение json_mode по содержимому промптов."""
    if json_mode is not None:
        return json_mode
    prompt_text = (system_prompt + user_prompt).lower()
    return "json" in prompt_text and ("верни" in prompt_text or "return" in prompt_text)


def prepare_media(
    image: Optional[Image.Image],
    pdf_file_path: Optional[str],
    is_gemini3: bool,
) -> Optional[Tuple[str, str]]:
    """Подготовить base64-контент и media_type. Возвращает None если нет данных."""
    if is_gemini3 and pdf_file_path and os.path.exists(pdf_file_path):
        with open(pdf_file_path, "rb") as f:
            file_b64 = base64.b64encode(f.read()).decode("utf-8")
        logger.info(f"Используется PDF-кроп: {pdf_file_path}")
        return file_b64, "application/pdf"
    elif is_gemini3 and image:
        return image_to_pdf_base64(image), "application/pdf"
    elif image:
        return image_to_base64(image), "image/png"
    return None


def build_payload(
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    file_b64: str,
    media_type: str,
    json_mode: bool,
    provider_order: Optional[List[str]],
) -> dict:
    """Собрать payload для OpenRouter API."""
    is_gemini3 = "gemini-3" in model_name.lower()

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{file_b64}"},
                    },
                    {"type": "text", "text": user_prompt},
                ],
            },
        ],
        "max_tokens": 16384,
        "temperature": 0.0 if is_gemini3 else 0.1,
        "top_p": 0.9,
    }

    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if is_gemini3:
        payload["transforms"] = {"media_resolution": "MEDIA_RESOLUTION_HIGH"}
    if provider_order:
        payload["provider"] = {"order": provider_order}

    return payload


def parse_response(status_code: int, response_json: Optional[dict], response_text: str = "") -> str:
    """Парсинг ответа OpenRouter API. Возвращает текст или сообщение об ошибке."""
    if status_code != 200:
        error_detail = response_text[:500] if response_text else "No details"
        logger.error(f"OpenRouter API error: {status_code} - {error_detail}")

        if status_code == 403:
            try:
                err_msg = response_json.get("error", {}).get("message", "Доступ запрещён") if response_json else "Доступ запрещён"
            except Exception:
                err_msg = "Проверьте API ключ и баланс на openrouter.ai"
            return f"[Ошибка OpenRouter 403: {err_msg}]"
        elif status_code == 401:
            return "[Ошибка OpenRouter 401: Неверный API ключ]"
        elif status_code == 429:
            return "[Ошибка OpenRouter 429: Превышен лимит запросов]"
        elif status_code == 402:
            return "[Ошибка OpenRouter 402: Недостаточно кредитов]"
        return f"[Ошибка OpenRouter API: {status_code}]"

    result = response_json
    if not result:
        return "[Ошибка OpenRouter: пустой ответ]"

    if "error" in result:
        err_obj = result["error"]
        err_msg = err_obj.get("message", str(err_obj)) if isinstance(err_obj, dict) else str(err_obj)
        logger.error(f"OpenRouter API error in body: {err_msg}")
        return f"[Ошибка OpenRouter: {err_msg}]"

    if "choices" not in result or not result["choices"]:
        logger.error(f"OpenRouter: 'choices' missing. Keys: {list(result.keys())}")
        return "[Ошибка OpenRouter: некорректный ответ API]"

    choice = result["choices"][0]
    message = choice.get("message") or {}
    text = extract_message_text(message)
    if not text:
        text = extract_message_text({"content": choice.get("text")})

    if not text:
        logger.warning("OpenRouter returned an empty content payload. Choice keys: %s", list(choice.keys()))
        return "[Ошибка OpenRouter: empty response content]"

    return text
