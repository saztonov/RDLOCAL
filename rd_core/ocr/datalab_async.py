"""Async Datalab OCR Backend"""
import asyncio
import json
import logging
import os
from typing import Optional

import httpx
from PIL import Image

from rd_core.ocr._datalab_common import (
    API_URL,
    build_request_data,
    handle_http_error,
    handle_immediate_result,
    handle_poll_complete,
    init_params,
    prepare_source,
)
from rd_core.ocr.http_utils import create_async_client

logger = logging.getLogger(__name__)


class AsyncDatalabOCRBackend:
    """Асинхронный OCR через Datalab Convert API"""

    def __init__(
        self,
        api_key: str,
        rate_limiter=None,
        poll_interval: Optional[int] = None,
        poll_max_attempts: Optional[int] = None,
        max_retries: Optional[int] = None,
        extras: Optional[str] = None,
        quality_threshold: Optional[float] = None,
    ):
        self.api_key = api_key
        self.headers = {"X-Api-Key": api_key}
        self.rate_limiter = rate_limiter
        self.last_html_result: Optional[str] = None
        self.last_quality_score: Optional[float] = None

        self.poll_interval, self.poll_max_attempts, self.max_retries, self.extras, self.quality_threshold = \
            init_params(api_key, poll_interval, poll_max_attempts, max_retries, extras, quality_threshold)

        self._client: Optional[httpx.AsyncClient] = None
        logger.info(
            f"AsyncDatalabOCRBackend инициализирован (poll_interval={self.poll_interval}s, "
            f"poll_max_attempts={self.poll_max_attempts}, max_retries={self.max_retries}, "
            f"extras={self.extras}, quality_threshold={self.quality_threshold})"
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = create_async_client(timeout=120.0)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def supports_pdf_input(self) -> bool:
        return True

    async def recognize_async(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        source = prepare_source(image, pdf_file_path)
        if source is None:
            return "[Ошибка: Datalab требует изображение или PDF]"
        tmp_path, mime_type, need_cleanup = source

        if self.rate_limiter:
            acquired = await self._acquire_rate_limiter()
            if not acquired:
                return "[Ошибка: таймаут ожидания rate limiter]"

        try:
            try:
                return await self._process_with_retries(tmp_path, mime_type)
            finally:
                if need_cleanup and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        except Exception as e:
            logger.error(f"Ошибка AsyncDatalab OCR: {e}", exc_info=True)
            return f"[Ошибка Datalab OCR: {e}]"
        finally:
            if self.rate_limiter:
                await self._release_rate_limiter()

    async def _acquire_rate_limiter(self) -> bool:
        if hasattr(self.rate_limiter, "acquire_async"):
            return await self.rate_limiter.acquire_async()
        elif hasattr(self.rate_limiter, "acquire"):
            return await asyncio.to_thread(self.rate_limiter.acquire)
        return True

    async def _release_rate_limiter(self):
        if hasattr(self.rate_limiter, "release_async"):
            await self.rate_limiter.release_async()
        elif hasattr(self.rate_limiter, "release"):
            self.rate_limiter.release()

    async def _process_with_retries(self, tmp_path: str, mime_type: str) -> str:
        client = await self._get_client()

        for full_retry in range(self.max_retries):
            if full_retry > 0:
                logger.warning(f"Datalab: повторная отправка запроса (попытка {full_retry + 1}/{self.max_retries})")

            response = await self._send_request_with_429_retry(client, tmp_path, mime_type, skip_cache=full_retry > 0)

            if response is None:
                return "[Ошибка Datalab API: превышен лимит запросов (429)]"

            if response.status_code != 200:
                return handle_http_error(response.status_code, response.text)

            result = response.json()
            immediate = handle_immediate_result(result)
            if immediate is not None:
                return immediate

            check_url = result["request_check_url"]
            logger.info(f"Datalab: начало поллинга результата по URL: {check_url}")
            poll_result = await self._poll_result(client, check_url)

            if poll_result is not None:
                if (self.last_quality_score is not None
                        and self.last_quality_score < self.quality_threshold
                        and full_retry < self.max_retries - 1):
                    logger.warning(
                        f"Datalab: низкое качество {self.last_quality_score} < {self.quality_threshold}, "
                        f"retry {full_retry + 1}/{self.max_retries}"
                    )
                    wait_time = (full_retry + 1) * 5
                    logger.info(f"Datalab: ожидание {wait_time}с перед retry из-за низкого качества")
                    await asyncio.sleep(wait_time)
                    continue
                return poll_result

            logger.warning(
                f"Datalab: таймаут поллинга после {self.poll_max_attempts} попыток, "
                f"retry {full_retry + 1}/{self.max_retries}"
            )
            if full_retry < self.max_retries - 1:
                wait_time = (full_retry + 1) * 10
                logger.info(f"Datalab: ожидание {wait_time}с перед повторной отправкой")
                await asyncio.sleep(wait_time)

        logger.error(f"Datalab: превышено время ожидания после {self.max_retries} полных попыток")
        logger.warning("Datalab: пропускаем блок из-за таймаута, продолжаем обработку")
        return ""

    async def _send_request_with_429_retry(
        self, client: httpx.AsyncClient, tmp_path: str,
        mime_type: str, skip_cache: bool = False,
    ) -> Optional[httpx.Response]:
        response = None

        for retry in range(self.max_retries):
            with open(tmp_path, "rb") as f:
                file_content = f.read()

            files = {"file": (os.path.basename(tmp_path), file_content, mime_type)}
            data = build_request_data(self.extras, skip_cache=skip_cache)

            response = await client.post(
                API_URL, headers=self.headers, files=files, data=data,
            )

            if response.status_code == 429:
                wait_time = min(60, (2**retry) * 10)
                logger.warning(f"Datalab API 429: ждём {wait_time}с (попытка {retry + 1}/{self.max_retries})")
                await asyncio.sleep(wait_time)
                continue
            break

        if response is not None and response.status_code == 429:
            return None
        return response

    async def _poll_result(self, client: httpx.AsyncClient, check_url: str) -> Optional[str]:
        for attempt in range(self.poll_max_attempts):
            await asyncio.sleep(self.poll_interval)

            logger.debug(f"Datalab: попытка поллинга {attempt + 1}/{self.poll_max_attempts}")

            try:
                poll_response = await client.get(check_url, headers=self.headers, timeout=30.0)
            except httpx.TimeoutException:
                logger.warning("Datalab: таймаут при поллинге, продолжаем")
                continue

            if poll_response.status_code == 429:
                logger.warning("Datalab: 429 при поллинге, ждём 30с")
                await asyncio.sleep(30)
                continue

            if poll_response.status_code != 200:
                logger.warning(f"Datalab: поллинг вернул статус {poll_response.status_code}: {poll_response.text}")
                continue

            poll_result = poll_response.json()
            status = poll_result.get("status", "")

            logger.info(f"Datalab: текущий статус задачи: '{status}' (попытка {attempt + 1}/{self.poll_max_attempts})")

            if status == "complete":
                html_result, quality = handle_poll_complete(poll_result)
                self.last_quality_score = quality
                self.last_html_result = html_result if html_result else None
                return html_result

            elif status == "failed":
                error = poll_result.get("error", "Unknown error")
                logger.error(f"Datalab: задача завершилась с ошибкой: {error}")
                return f"[Ошибка Datalab: {error}]"

            elif status not in ["processing", "pending", "queued"]:
                logger.warning(f"Datalab: неизвестный статус '{status}'. Полный ответ: {poll_result}")

        return None

    def __del__(self):
        if self._client and not self._client.is_closed:
            logger.debug("AsyncDatalabOCRBackend: client not closed properly")
