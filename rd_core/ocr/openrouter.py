"""OpenRouter OCR Backend (sync)"""
import logging
from typing import Optional

import requests
from PIL import Image

from rd_core.ocr._openrouter_common import (
    _providers_cache,
    build_payload,
    detect_json_mode,
    init_params,
    parse_providers_from_response,
    parse_response,
    prepare_media,
    prepare_prompts,
    supports_pdf_input as _supports_pdf,
)
from rd_core.ocr.http_utils import create_retry_session
from rd_core.ocr_result import is_error, make_error

logger = logging.getLogger(__name__)


class OpenRouterBackend:
    """OCR через OpenRouter API"""

    def __init__(
        self,
        api_key: str,
        model_name: str = "qwen/qwen3-vl-30b-a3b-instruct",
        base_url: Optional[str] = None,
    ):
        self.api_key, self.model_name, self.base_url = init_params(api_key, model_name, base_url)
        self._provider_order: Optional[list] = None
        self.session = create_retry_session()
        logger.info(f"OpenRouter инициализирован (модель: {self.model_name}, base_url: {self.base_url})")

    def _fetch_cheapest_providers(self) -> Optional[list]:
        if self.model_name in _providers_cache:
            return _providers_cache[self.model_name]
        try:
            response = self.session.get(
                f"{self.base_url}/api/v1/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30,
            )
            if response.status_code != 200:
                logger.warning(f"Не удалось получить список моделей: {response.status_code}")
                return None
            return parse_providers_from_response(response.json().get("data", []), self.model_name)
        except Exception as e:
            logger.warning(f"Ошибка получения провайдеров: {e}")
            return None

    def supports_pdf_input(self) -> bool:
        return _supports_pdf(self.model_name)

    def recognize(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        try:
            if self._provider_order is None:
                self._provider_order = self._fetch_cheapest_providers() or []

            system_prompt, user_prompt = prepare_prompts(prompt)
            json_mode = detect_json_mode(system_prompt, user_prompt, json_mode)
            is_gemini3 = "gemini-3" in self.model_name.lower()

            media = prepare_media(image, pdf_file_path, is_gemini3)
            if media is None:
                return make_error("нет изображения или PDF")
            file_b64, media_type = media

            payload = build_payload(
                self.model_name, system_prompt, user_prompt,
                file_b64, media_type, json_mode, self._provider_order,
            )

            response = self.session.post(
                f"{self.base_url}/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )

            try:
                resp_json = response.json()
            except Exception:
                resp_json = None

            text = parse_response(response.status_code, resp_json, response.text)
            if not is_error(text):
                logger.debug(f"OpenRouter OCR: распознано {len(text)} символов")
            return text

        except requests.exceptions.Timeout:
            logger.error("OpenRouter OCR: превышен таймаут")
            return make_error("превышен таймаут запроса")
        except Exception as e:
            logger.error(f"Ошибка OpenRouter OCR: {e}", exc_info=True)
            return make_error(f"OpenRouter OCR: {e}")
