"""Async OpenRouter OCR Backend"""
import logging
from typing import Optional

import httpx
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
from rd_core.ocr.http_utils import create_async_client

logger = logging.getLogger(__name__)


class AsyncOpenRouterBackend:
    """Асинхронный OCR через OpenRouter API"""

    def __init__(
        self,
        api_key: str,
        model_name: str = "qwen/qwen3-vl-30b-a3b-instruct",
        base_url: Optional[str] = None,
    ):
        self.api_key, self.model_name, self.base_url = init_params(api_key, model_name, base_url)
        self._provider_order: Optional[list] = None
        self._client: Optional[httpx.AsyncClient] = None
        logger.info(f"AsyncOpenRouterBackend инициализирован (модель: {self.model_name}, base_url: {self.base_url})")

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = create_async_client(timeout=120.0)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _fetch_cheapest_providers(self) -> Optional[list]:
        if self.model_name in _providers_cache:
            return _providers_cache[self.model_name]
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.base_url}/api/v1/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30.0,
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

    async def recognize_async(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        try:
            client = await self._get_client()

            if self._provider_order is None:
                self._provider_order = await self._fetch_cheapest_providers() or []

            system_prompt, user_prompt = prepare_prompts(prompt)
            json_mode = detect_json_mode(system_prompt, user_prompt, json_mode)
            is_gemini3 = "gemini-3" in self.model_name.lower()

            media = prepare_media(image, pdf_file_path, is_gemini3)
            if media is None:
                return "[Ошибка: нет изображения или PDF]"
            file_b64, media_type = media

            payload = build_payload(
                self.model_name, system_prompt, user_prompt,
                file_b64, media_type, json_mode, self._provider_order,
            )

            response = await client.post(
                f"{self.base_url}/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            try:
                resp_json = response.json()
            except Exception:
                resp_json = None

            text = parse_response(response.status_code, resp_json, response.text)
            if not text.startswith("[Ошибка"):
                logger.debug(f"AsyncOpenRouter OCR: распознано {len(text)} символов")
            return text

        except httpx.TimeoutException:
            logger.error("AsyncOpenRouter OCR: превышен таймаут")
            return "[Ошибка: превышен таймаут запроса]"
        except Exception as e:
            logger.error(f"Ошибка AsyncOpenRouter OCR: {e}", exc_info=True)
            return f"[Ошибка OpenRouter OCR: {e}]"

    def __del__(self):
        if self._client and not self._client.is_closed:
            logger.debug("AsyncOpenRouterBackend: client not closed properly")
