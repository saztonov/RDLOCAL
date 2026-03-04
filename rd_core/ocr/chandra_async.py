"""Async Chandra OCR Backend (LM Studio / OpenAI-compatible API)"""
import asyncio
import logging
import os
from typing import Optional

import httpx
from PIL import Image

from rd_core.ocr.http_utils import create_async_client
from rd_core.ocr.chandra import (
    CHANDRA_DEFAULT_PROMPT,
    CHANDRA_DEFAULT_SYSTEM,
    CHANDRA_LOAD_CONFIG,
    CHANDRA_MODEL_KEY,
    needs_model_reload,
)
from rd_core.ocr.utils import image_to_base64

logger = logging.getLogger(__name__)


class AsyncChandraBackend:
    """Асинхронный OCR через Chandra модель (LM Studio, OpenAI-compatible API)"""

    DEFAULT_BASE_URL = "https://louvred-madie-gigglier.ngrok-free.dev"

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or os.getenv("CHANDRA_BASE_URL", self.DEFAULT_BASE_URL)
        self._model_id: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

        # HTTP Basic Auth для ngrok-туннеля
        auth_user = os.getenv("NGROK_AUTH_USER")
        auth_pass = os.getenv("NGROK_AUTH_PASS")
        self._auth = (auth_user, auth_pass) if auth_user and auth_pass else None

        logger.info(f"AsyncChandraBackend инициализирован (base_url: {self.base_url})")

    async def _get_client(self) -> httpx.AsyncClient:
        """Получить или создать httpx AsyncClient с connection pooling"""
        if self._client is None or self._client.is_closed:
            self._client = create_async_client(timeout=300.0, auth=self._auth)
        return self._client

    async def close(self):
        """Закрыть HTTP клиент"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _discover_model(self) -> str:
        """Авто-определение модели через /v1/models + preload через native API"""
        if self._model_id:
            return self._model_id

        await self._ensure_model_loaded()

        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.base_url}/v1/models",
                timeout=30.0,
            )
            if resp.status_code == 200:
                for m in resp.json().get("data", []):
                    if "chandra" in m.get("id", "").lower():
                        self._model_id = m["id"]
                        logger.info(f"Chandra модель найдена: {self._model_id}")
                        return self._model_id
        except Exception as e:
            logger.warning(f"Ошибка определения модели Chandra: {e}")

        self._model_id = "chandra-ocr"
        logger.info(f"Chandra модель не найдена, используется fallback: {self._model_id}")
        return self._model_id

    async def _ensure_model_loaded(self) -> None:
        """
        Проверяет загружена ли модель через LM Studio native API.
        Если нет или context_length не совпадает — выгружает и загружает с правильным конфигом.
        """
        required_ctx = CHANDRA_LOAD_CONFIG["context_length"]
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.base_url}/api/v1/models",
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.debug("LM Studio native API недоступен, пропускаем preload")
                return

            models = resp.json().get("models", [])

            for m in models:
                if "chandra" in m.get("key", "").lower():
                    loaded = m.get("loaded_instances", [])
                    needs_reload, reason = needs_model_reload(loaded, required_ctx)

                    if not needs_reload:
                        logger.debug(f"Модель {m['key']}: {reason}")
                        return

                    logger.info(f"Модель {m['key']}: {reason}, выполняем reload")
                    for inst in loaded:
                        try:
                            await client.post(
                                f"{self.base_url}/api/v1/models/unload",
                                json={"instance_id": inst["id"]},
                                timeout=30.0,
                            )
                            logger.debug(f"Выгружен инстанс: {inst['id']}")
                        except Exception as e:
                            logger.warning(f"Ошибка выгрузки {inst.get('id')}: {e}")
                    break

            # Используем фактический ключ модели из LM Studio (а не hardcoded)
            actual_key = m.get("key", CHANDRA_MODEL_KEY)
            logger.info(
                f"Загружаем модель {actual_key} "
                f"(context_length={required_ctx})"
            )
            load_resp = await client.post(
                f"{self.base_url}/api/v1/models/load",
                json={"model": actual_key, "echo_load_config": True, **CHANDRA_LOAD_CONFIG},
                timeout=120.0,
            )

            if load_resp.status_code == 200:
                load_data = load_resp.json()
                actual_ctx = load_data.get("load_config", {}).get("context_length", "?")
                logger.info(
                    f"Модель загружена: context_length={actual_ctx}, "
                    f"время={load_data.get('load_time_seconds', '?')}с"
                )
            else:
                logger.warning(
                    f"Ошибка загрузки: {load_resp.status_code} - {load_resp.text[:300]}"
                )

        except Exception as e:
            logger.debug(f"Native API preload недоступен: {e}")

    def unload_model(self) -> None:
        """Выгрузить модель из LM Studio (освобождает VRAM). Sync для finally."""
        import httpx as _httpx

        if not self._model_id:
            return
        try:
            _ngrok_headers = {"ngrok-skip-browser-warning": "true"}
            resp = _httpx.get(
                f"{self.base_url}/api/v1/models",
                timeout=10.0,
                auth=self._auth,
                headers=_ngrok_headers,
            )
            if resp.status_code != 200:
                return

            models = resp.json().get("models", [])
            for m in models:
                if "chandra" in m.get("key", "").lower():
                    for inst in m.get("loaded_instances", []):
                        _httpx.post(
                            f"{self.base_url}/api/v1/models/unload",
                            json={"instance_id": inst["id"]},
                            timeout=30.0,
                            auth=self._auth,
                            headers=_ngrok_headers,
                        )
                        logger.info(f"Модель выгружена: {inst['id']}")
                    break
        except Exception as e:
            logger.debug(f"Ошибка выгрузки модели: {e}")

    def supports_pdf_input(self) -> bool:
        """Chandra не поддерживает прямой ввод PDF"""
        return False

    # Transient HTTP коды, при которых имеет смысл retry (ngrok tunnel issues)
    _TRANSIENT_CODES = {404, 429, 500, 502, 503, 504}
    _MAX_APP_RETRIES = 2
    _APP_RETRY_DELAYS = [15, 30]

    async def recognize_async(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        """Асинхронно распознать текст через Chandra (LM Studio API)"""
        if image is None:
            return "[Ошибка: Chandra требует изображение]"

        try:
            client = await self._get_client()
            model_id = await self._discover_model()
            img_b64 = image_to_base64(image)

            # Chandra всегда использует свой специализированный HTML промпт
            # System prompt берём из переданного dict (контекст задачи)
            if prompt and isinstance(prompt, dict):
                system_prompt = prompt.get("system", "") or CHANDRA_DEFAULT_SYSTEM
            else:
                system_prompt = CHANDRA_DEFAULT_SYSTEM
            user_prompt = CHANDRA_DEFAULT_PROMPT

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img_b64}"
                            },
                        },
                        {
                            "type": "text",
                            "text": user_prompt,
                        },
                    ],
                }
            )

            payload = {
                "model": model_id,
                "messages": messages,
                "max_tokens": 12384,
                "temperature": 0,
                "top_p": 0.1,
            }

            last_error = None
            response = None
            for attempt in range(self._MAX_APP_RETRIES + 1):
                if attempt > 0:
                    delay = self._APP_RETRY_DELAYS[min(attempt - 1, len(self._APP_RETRY_DELAYS) - 1)]
                    logger.warning(
                        f"AsyncChandra retry {attempt}/{self._MAX_APP_RETRIES}, "
                        f"ожидание {delay}с (предыдущая ошибка: {last_error})"
                    )
                    await asyncio.sleep(delay)

                try:
                    response = await client.post(
                        f"{self.base_url}/v1/chat/completions",
                        headers={
                            "Content-Type": "application/json",
                            "ngrok-skip-browser-warning": "true",
                        },
                        json=payload,
                    )
                except httpx.TimeoutException:
                    last_error = "Timeout"
                    logger.warning(f"AsyncChandra timeout (attempt {attempt})")
                    if attempt < self._MAX_APP_RETRIES:
                        continue
                    return "[Ошибка: превышен таймаут запроса к Chandra]"
                except httpx.ConnectError as e:
                    last_error = f"ConnectError: {e}"
                    logger.warning(f"AsyncChandra connection error (attempt {attempt}): {e}")
                    if attempt < self._MAX_APP_RETRIES:
                        continue
                    return f"[Ошибка Chandra: {last_error}]"

                if response.status_code == 200:
                    break

                error_detail = response.text[:500] if response.text else "No details"

                # Детерминированная ошибка: context size exceeded — retry бессмысленно
                if response.status_code == 400 and "context size" in error_detail.lower():
                    logger.error(f"Chandra API error: {response.status_code} - {error_detail}")
                    return "[НеПовторяемая ошибка: контекст превышен — блок слишком большой для модели]"

                if response.status_code in self._TRANSIENT_CODES:
                    last_error = f"HTTP {response.status_code}"
                    if attempt < self._MAX_APP_RETRIES:
                        logger.warning(
                            f"AsyncChandra transient error {response.status_code} "
                            f"(attempt {attempt}), will retry"
                        )
                        continue
                    return f"[Ошибка Chandra API: {response.status_code}]"

                # Non-transient ошибка — возвращаем сразу
                logger.error(f"Chandra API error: {response.status_code} - {error_detail}")
                return f"[Ошибка Chandra API: {response.status_code}]"

            result = response.json()

            if "choices" not in result or not result["choices"]:
                err_msg = result.get("error", result)
                logger.error(f"Chandra: 'choices' missing: {err_msg}")
                return f"[Ошибка Chandra: некорректный ответ ({err_msg})]"

            text = result["choices"][0]["message"]["content"].strip()
            if not text:
                logger.warning("AsyncChandra OCR: получен пустой ответ от модели")
                return "[Ошибка Chandra: пустой ответ модели]"
            logger.debug(f"AsyncChandra OCR: распознано {len(text)} символов")
            return text

        except Exception as e:
            logger.error(f"Ошибка AsyncChandra OCR: {e}", exc_info=True)
            return f"[Ошибка Chandra OCR: {e}]"

    def __del__(self):
        """Cleanup при удалении объекта"""
        if self._client and not self._client.is_closed:
            logger.debug("AsyncChandraBackend: client not closed properly")
