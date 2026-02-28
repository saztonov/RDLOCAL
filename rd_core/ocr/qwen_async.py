"""Async Qwen OCR Backend (LM Studio / OpenAI-compatible API)"""
import logging
import os
from typing import Optional

import httpx
from PIL import Image

from rd_core.ocr.chandra import needs_model_reload
from rd_core.ocr.http_utils import create_async_client
from rd_core.ocr.qwen import (
    QWEN_LOAD_CONFIG,
    QWEN_MODEL_KEY,
    QWEN_STAMP_PROMPT,
    QWEN_STAMP_SYSTEM,
    QWEN_TEXT_PROMPT,
    QWEN_TEXT_SYSTEM,
)
from rd_core.ocr.utils import image_to_base64, strip_think_tags, strip_untagged_reasoning

logger = logging.getLogger(__name__)


class AsyncQwenBackend:
    """Асинхронный OCR через Qwen модель (LM Studio, OpenAI-compatible API)

    Args:
        base_url: URL LM Studio (по умолчанию QWEN_BASE_URL или CHANDRA_BASE_URL)
        mode: "text" — для TEXT/TABLE блоков, "stamp" — для штампов
    """

    def __init__(self, base_url: Optional[str] = None, mode: str = "text"):
        self.mode = mode
        self.base_url = (
            base_url
            or os.getenv("QWEN_BASE_URL")
            or os.getenv("CHANDRA_BASE_URL", "")
        )
        self._model_id: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

        # HTTP Basic Auth для ngrok-туннеля (общий с Chandra)
        auth_user = os.getenv("NGROK_AUTH_USER")
        auth_pass = os.getenv("NGROK_AUTH_PASS")
        self._auth = (auth_user, auth_pass) if auth_user and auth_pass else None

        logger.info(
            f"AsyncQwenBackend инициализирован (base_url: {self.base_url}, mode: {self.mode})"
        )

    def _get_prompts(self) -> tuple:
        """Возвращает (system_prompt, user_prompt) по текущему mode."""
        if self.mode == "stamp":
            return QWEN_STAMP_SYSTEM, QWEN_STAMP_PROMPT
        return QWEN_TEXT_SYSTEM, QWEN_TEXT_PROMPT

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
                    if "qwen" in m.get("id", "").lower():
                        self._model_id = m["id"]
                        logger.info(f"Qwen модель найдена: {self._model_id}")
                        return self._model_id
        except Exception as e:
            logger.warning(f"Ошибка определения модели Qwen: {e}")

        self._model_id = QWEN_MODEL_KEY
        logger.info(
            f"Qwen модель не найдена, используется fallback: {self._model_id}"
        )
        return self._model_id

    async def _ensure_model_loaded(self) -> None:
        """
        Проверяет загружена ли модель через LM Studio native API.
        Если нет или context_length не совпадает — выгружает и загружает.
        """
        required_ctx = QWEN_LOAD_CONFIG["context_length"]
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

            target_model = None
            for m in models:
                if "qwen" in m.get("key", "").lower():
                    target_model = m
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

            actual_key = (
                target_model.get("key", QWEN_MODEL_KEY)
                if target_model
                else QWEN_MODEL_KEY
            )
            logger.info(
                f"Загружаем модель {actual_key} (context_length={required_ctx})"
            )
            load_resp = await client.post(
                f"{self.base_url}/api/v1/models/load",
                json={
                    "model": actual_key,
                    "echo_load_config": True,
                    **QWEN_LOAD_CONFIG,
                },
                timeout=120.0,
            )

            if load_resp.status_code == 200:
                load_data = load_resp.json()
                actual_ctx = (
                    load_data.get("load_config", {}).get("context_length", "?")
                )
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
                if "qwen" in m.get("key", "").lower():
                    for inst in m.get("loaded_instances", []):
                        _httpx.post(
                            f"{self.base_url}/api/v1/models/unload",
                            json={"instance_id": inst["id"]},
                            timeout=30.0,
                            auth=self._auth,
                            headers=_ngrok_headers,
                        )
                        logger.info(f"Qwen модель выгружена: {inst['id']}")
                    break
        except Exception as e:
            logger.debug(f"Ошибка выгрузки модели Qwen: {e}")

    def supports_pdf_input(self) -> bool:
        """Qwen не поддерживает прямой ввод PDF"""
        return False

    async def recognize_async(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        """Асинхронно распознать текст через Qwen (LM Studio API)"""
        if image is None:
            return "[Ошибка: Qwen требует изображение]"

        try:
            client = await self._get_client()
            model_id = await self._discover_model()
            img_b64 = image_to_base64(image)

            system_prompt, user_prompt = self._get_prompts()

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

            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "ngrok-skip-browser-warning": "true",
                },
                json=payload,
            )

            if response.status_code != 200:
                error_detail = response.text[:500] if response.text else "No details"
                logger.error(
                    f"Qwen API error: {response.status_code} - {error_detail}"
                )
                return f"[Ошибка Qwen API: {response.status_code}]"

            result = response.json()

            if "choices" not in result or not result["choices"]:
                err_msg = result.get("error", result)
                logger.error(f"Qwen: 'choices' missing: {err_msg}")
                return f"[Ошибка Qwen: некорректный ответ ({err_msg})]"

            message = result["choices"][0]["message"]

            # LM Studio v0.3.23+ выносит thinking в отдельное поле
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            raw_text = message.get("content", "").strip()

            if reasoning:
                logger.info(
                    f"AsyncQwen/{self.mode}: reasoning в отдельном поле "
                    f"({len(reasoning)} симв.), content={len(raw_text)} симв."
                )

            if not raw_text:
                logger.warning("AsyncQwen OCR: получен пустой ответ от модели")
                return "[Ошибка Qwen: пустой ответ модели]"

            # Слой 1: убрать <think>...</think> теги
            text = strip_think_tags(raw_text, backend_name=f"AsyncQwen/{self.mode}")
            # Слой 2: убрать не-тегированный reasoning
            text = strip_untagged_reasoning(text, backend_name=f"AsyncQwen/{self.mode}")

            if not text:
                logger.warning(
                    f"AsyncQwen OCR ({self.mode}): ответ только reasoning "
                    f"({len(raw_text)} симв.), HTML не сгенерирован"
                )
                return "[Ошибка Qwen: ответ содержит только reasoning]"
            logger.debug(
                f"AsyncQwen OCR ({self.mode}): распознано {len(text)} символов"
            )
            return text

        except httpx.TimeoutException:
            logger.error("AsyncQwen OCR: превышен таймаут")
            return "[Ошибка: превышен таймаут запроса к Qwen]"
        except Exception as e:
            logger.error(f"Ошибка AsyncQwen OCR: {e}", exc_info=True)
            return f"[Ошибка Qwen OCR: {e}]"

    def __del__(self):
        """Cleanup при удалении объекта"""
        if self._client and not self._client.is_closed:
            logger.debug("AsyncQwenBackend: client not closed properly")
