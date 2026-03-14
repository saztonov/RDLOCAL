"""Async Qwen OCR Backend (LM Studio / OpenAI-compatible API)"""
import asyncio
import logging
from typing import Optional

import httpx
from PIL import Image

from rd_core.ocr._chandra_common import needs_model_reload, get_ngrok_auth
from rd_core.ocr._qwen_common import (
    QWEN_LOAD_CONFIG,
    QWEN_MODEL_KEY,
    TRANSIENT_CODES,
    build_payload,
    check_non_retriable_error,
    init_base_url,
    parse_response,
)
from rd_core.ocr.http_utils import create_async_client
from rd_core.ocr.utils import image_to_base64

logger = logging.getLogger(__name__)


class AsyncQwenBackend:
    """Асинхронный OCR через Qwen модель (LM Studio, OpenAI-compatible API)"""

    _MAX_APP_RETRIES = 2
    _APP_RETRY_DELAYS = [15, 30]

    def __init__(self, base_url: Optional[str] = None, mode: str = "text"):
        self.mode = mode
        self.base_url = init_base_url(base_url)
        self._model_id: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._auth = get_ngrok_auth()
        logger.info(f"AsyncQwenBackend инициализирован (base_url: {self.base_url}, mode: {self.mode})")

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = create_async_client(timeout=300.0, auth=self._auth)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _discover_model(self) -> str:
        if self._model_id:
            return self._model_id

        await self._ensure_model_loaded()

        try:
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}/v1/models", timeout=30.0)
            if resp.status_code == 200:
                for m in resp.json().get("data", []):
                    if "qwen" in m.get("id", "").lower():
                        self._model_id = m["id"]
                        logger.info(f"Qwen модель найдена: {self._model_id}")
                        return self._model_id
        except Exception as e:
            logger.warning(f"Ошибка определения модели Qwen: {e}")

        self._model_id = QWEN_MODEL_KEY
        logger.info(f"Qwen модель не найдена, используется fallback: {self._model_id}")
        return self._model_id

    async def _ensure_model_loaded(self) -> None:
        required_ctx = QWEN_LOAD_CONFIG["context_length"]
        try:
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}/api/v1/models", timeout=10.0)
            if resp.status_code != 200:
                logger.debug("LM Studio native API недоступен, пропускаем preload")
                return

            models = resp.json().get("models", [])
            target_model = None

            for m in models:
                if "qwen" in m.get("key", "").lower():
                    target_model = m
                    loaded = m.get("loaded_instances", [])
                    need_reload, reason = needs_model_reload(loaded, required_ctx)

                    if not need_reload:
                        logger.debug(f"Модель {m['key']}: {reason}")
                        return

                    logger.info(f"Модель {m['key']}: {reason}, выполняем reload")
                    for inst in loaded:
                        try:
                            await client.post(
                                f"{self.base_url}/api/v1/models/unload",
                                json={"instance_id": inst["id"]}, timeout=30.0,
                            )
                            logger.debug(f"Выгружен инстанс: {inst['id']}")
                        except Exception as e:
                            logger.warning(f"Ошибка выгрузки {inst.get('id')}: {e}")
                    break

            actual_key = target_model.get("key", QWEN_MODEL_KEY) if target_model else QWEN_MODEL_KEY
            logger.info(f"Загружаем модель {actual_key} (context_length={required_ctx})")
            load_resp = await client.post(
                f"{self.base_url}/api/v1/models/load",
                json={"model": actual_key, "echo_load_config": True, **QWEN_LOAD_CONFIG},
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
                logger.warning(f"Ошибка загрузки: {load_resp.status_code} - {load_resp.text[:300]}")

        except Exception as e:
            logger.debug(f"Native API preload недоступен: {e}")

    def unload_model(self) -> None:
        """Sync unload для finally-блока в tasks.py."""
        import httpx as _httpx

        if not self._model_id:
            return
        try:
            _ngrok_headers = {"ngrok-skip-browser-warning": "true"}
            resp = _httpx.get(
                f"{self.base_url}/api/v1/models", timeout=10.0,
                auth=self._auth, headers=_ngrok_headers,
            )
            if resp.status_code != 200:
                return

            for m in resp.json().get("models", []):
                if "qwen" in m.get("key", "").lower():
                    for inst in m.get("loaded_instances", []):
                        _httpx.post(
                            f"{self.base_url}/api/v1/models/unload",
                            json={"instance_id": inst["id"]}, timeout=30.0,
                            auth=self._auth, headers=_ngrok_headers,
                        )
                        logger.info(f"Qwen модель выгружена: {inst['id']}")
                    break
        except Exception as e:
            logger.debug(f"Ошибка выгрузки модели Qwen: {e}")

    def supports_pdf_input(self) -> bool:
        return False

    async def recognize_async(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        if image is None:
            return "[Ошибка: Qwen требует изображение]"

        try:
            client = await self._get_client()
            model_id = await self._discover_model()
            img_b64 = image_to_base64(image)
            payload = build_payload(model_id, self.mode, img_b64)

            last_error = None
            response = None
            for attempt in range(self._MAX_APP_RETRIES + 1):
                if attempt > 0:
                    delay = self._APP_RETRY_DELAYS[min(attempt - 1, len(self._APP_RETRY_DELAYS) - 1)]
                    logger.warning(
                        f"AsyncQwen retry {attempt}/{self._MAX_APP_RETRIES}, "
                        f"ожидание {delay}с (предыдущая ошибка: {last_error})"
                    )
                    await asyncio.sleep(delay)

                try:
                    response = await client.post(
                        f"{self.base_url}/v1/chat/completions",
                        headers={"Content-Type": "application/json", "ngrok-skip-browser-warning": "true"},
                        json=payload,
                    )
                except httpx.TimeoutException:
                    last_error = "Timeout"
                    logger.warning(f"AsyncQwen timeout (attempt {attempt})")
                    if attempt < self._MAX_APP_RETRIES:
                        continue
                    return "[Ошибка: превышен таймаут запроса к Qwen]"
                except httpx.ConnectError as e:
                    last_error = f"ConnectError: {e}"
                    logger.warning(f"AsyncQwen connection error (attempt {attempt}): {e}")
                    if attempt < self._MAX_APP_RETRIES:
                        continue
                    return f"[Ошибка Qwen: {last_error}]"

                if response.status_code == 200:
                    break

                non_retriable = check_non_retriable_error(response.status_code, response.text)
                if non_retriable:
                    return non_retriable

                if response.status_code in TRANSIENT_CODES:
                    last_error = f"HTTP {response.status_code}"
                    if attempt < self._MAX_APP_RETRIES:
                        logger.warning(f"AsyncQwen transient error {response.status_code} (attempt {attempt}), will retry")
                        continue
                    return f"[Ошибка Qwen API: {response.status_code}]"

                error_detail = response.text[:500] if response.text else "No details"
                logger.error(f"Qwen API error: {response.status_code} - {error_detail}")
                return f"[Ошибка Qwen API: {response.status_code}]"

            text = parse_response(response.json(), self.mode, "AsyncQwen")
            if not text.startswith("[Ошибка"):
                logger.debug(f"AsyncQwen OCR ({self.mode}): распознано {len(text)} символов")
            return text

        except Exception as e:
            logger.error(f"Ошибка AsyncQwen OCR: {e}", exc_info=True)
            return f"[Ошибка Qwen OCR: {e}]"

    def __del__(self):
        if self._client and not self._client.is_closed:
            logger.debug("AsyncQwenBackend: client not closed properly")
