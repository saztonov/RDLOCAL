"""OpenRouter OCR Backend"""
import base64
import logging
import os
from typing import List, Optional

from PIL import Image

from rd_core.ocr.utils import image_to_base64, image_to_pdf_base64

logger = logging.getLogger(__name__)


class OpenRouterBackend:
    """OCR через OpenRouter API"""

    _providers_cache: dict = {}

    DEFAULT_BASE_URL = "https://openrouter.ai"
    DEFAULT_SYSTEM = "You are an expert design engineer and automation specialist. Your task is to analyze technical drawings and extract data into structured JSON or Markdown formats with 100% accuracy. Do not omit details. Do not hallucinate values."
    DEFAULT_USER = "Распознай содержимое изображения."

    def __init__(
        self,
        api_key: str,
        model_name: str = "qwen/qwen3-vl-30b-a3b-instruct",
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url or os.getenv("OPENROUTER_BASE_URL", self.DEFAULT_BASE_URL)
        self._provider_order: Optional[List[str]] = None
        try:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry

            self.session = requests.Session()
            retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[502, 503, 504])
            adapter = HTTPAdapter(
                pool_connections=5, pool_maxsize=10, max_retries=retry
            )
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)
            self.requests = requests  # для exceptions
        except ImportError:
            raise ImportError("Требуется установить requests: pip install requests")
        logger.info(f"OpenRouter инициализирован (модель: {self.model_name}, base_url: {self.base_url})")

    def _fetch_cheapest_providers(self) -> Optional[List[str]]:
        """Получить список провайдеров отсортированных по цене"""
        if self.model_name in OpenRouterBackend._providers_cache:
            return OpenRouterBackend._providers_cache[self.model_name]

        try:
            response = self.session.get(
                f"{self.base_url}/api/v1/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30,
            )
            if response.status_code != 200:
                logger.warning(
                    f"Не удалось получить список моделей: {response.status_code}"
                )
                return None

            models_data = response.json().get("data", [])

            model_info = None
            for m in models_data:
                if m.get("id") == self.model_name:
                    model_info = m
                    break

            if not model_info:
                logger.warning(f"Модель {self.model_name} не найдена в списке")
                return None

            pricing = model_info.get("endpoint", {}).get("pricing", {})
            if not pricing:
                pricing = model_info.get("pricing", {})

            providers_pricing = []
            if isinstance(pricing, dict) and "providers" in pricing:
                for provider_id, pdata in pricing.get("providers", {}).items():
                    prompt_cost = float(pdata.get("prompt", 0) or 0)
                    completion_cost = float(pdata.get("completion", 0) or 0)
                    total = prompt_cost + completion_cost
                    providers_pricing.append((provider_id, total))
            elif isinstance(pricing, list):
                for pdata in pricing:
                    provider_id = pdata.get("provider_id") or pdata.get("provider")
                    if provider_id:
                        prompt_cost = float(pdata.get("prompt", 0) or 0)
                        completion_cost = float(pdata.get("completion", 0) or 0)
                        total = prompt_cost + completion_cost
                        providers_pricing.append((provider_id, total))

            if not providers_pricing:
                logger.info("Pricing по провайдерам не найден, используется дефолт")
                return None

            providers_pricing.sort(key=lambda x: x[1])
            provider_order = [p[0] for p in providers_pricing]

            logger.info(f"Провайдеры для {self.model_name} (по цене): {provider_order}")
            OpenRouterBackend._providers_cache[self.model_name] = provider_order
            return provider_order

        except Exception as e:
            logger.warning(f"Ошибка получения провайдеров: {e}")
            return None

    def supports_pdf_input(self) -> bool:
        """Возвращает True для моделей Gemini 3, поддерживающих PDF"""
        return "gemini-3" in self.model_name.lower()

    def recognize(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        """Распознать текст через OpenRouter API"""
        try:
            if self._provider_order is None:
                self._provider_order = self._fetch_cheapest_providers() or []

            if prompt and isinstance(prompt, dict):
                system_prompt = prompt.get("system", "") or self.DEFAULT_SYSTEM
                user_prompt = prompt.get("user", "") or self.DEFAULT_USER
            else:
                system_prompt = self.DEFAULT_SYSTEM
                user_prompt = self.DEFAULT_USER

            if json_mode is None:
                prompt_text = (system_prompt + user_prompt).lower()
                json_mode = "json" in prompt_text and (
                    "верни" in prompt_text or "return" in prompt_text
                )

            is_gemini3 = "gemini-3" in self.model_name.lower()

            # Приоритет: PDF файл для Gemini 3
            if is_gemini3 and pdf_file_path and os.path.exists(pdf_file_path):
                with open(pdf_file_path, "rb") as f:
                    file_b64 = base64.b64encode(f.read()).decode("utf-8")
                media_type = "application/pdf"
                logger.info(f"Используется PDF-кроп: {pdf_file_path}")
            elif is_gemini3 and image:
                file_b64 = image_to_pdf_base64(image)
                media_type = "application/pdf"
            elif image:
                file_b64 = image_to_base64(image)
                media_type = "image/png"
            else:
                return "[Ошибка: нет изображения или PDF]"

            payload = {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{file_b64}"
                                },
                            },
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

            if self._provider_order:
                payload["provider"] = {"order": self._provider_order}

            response = self.session.post(
                f"{self.base_url}/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )

            if response.status_code != 200:
                error_detail = response.text[:500] if response.text else "No details"
                logger.error(
                    f"OpenRouter API error: {response.status_code} - {error_detail}"
                )

                # Детализация ошибок
                if response.status_code == 403:
                    try:
                        err_json = response.json()
                        err_msg = err_json.get("error", {}).get(
                            "message", "Доступ запрещён"
                        )
                    except:
                        err_msg = "Проверьте API ключ и баланс на openrouter.ai"
                    return f"[Ошибка OpenRouter 403: {err_msg}]"
                elif response.status_code == 401:
                    return "[Ошибка OpenRouter 401: Неверный API ключ]"
                elif response.status_code == 429:
                    return "[Ошибка OpenRouter 429: Превышен лимит запросов]"
                elif response.status_code == 402:
                    return "[Ошибка OpenRouter 402: Недостаточно кредитов]"

                return f"[Ошибка OpenRouter API: {response.status_code}]"

            result = response.json()

            # OpenRouter может вернуть HTTP 200 с error в теле
            if "error" in result:
                err_obj = result["error"]
                if isinstance(err_obj, dict):
                    err_msg = err_obj.get("message", str(err_obj))
                else:
                    err_msg = str(err_obj)
                logger.error(f"OpenRouter API error in body: {err_msg}")
                return f"[Ошибка OpenRouter: {err_msg}]"

            if "choices" not in result or not result["choices"]:
                logger.error(f"OpenRouter: 'choices' missing. Keys: {list(result.keys())}")
                return "[Ошибка OpenRouter: некорректный ответ API]"

            text = result["choices"][0]["message"]["content"].strip()
            logger.debug(f"OpenRouter OCR: распознано {len(text)} символов")
            return text

        except self.requests.exceptions.Timeout:
            logger.error("OpenRouter OCR: превышен таймаут")
            return "[Ошибка: превышен таймаут запроса]"
        except Exception as e:
            logger.error(f"Ошибка OpenRouter OCR: {e}", exc_info=True)
            return f"[Ошибка OpenRouter OCR: {e}]"
