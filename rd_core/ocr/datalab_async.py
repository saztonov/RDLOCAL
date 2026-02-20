"""Async Datalab OCR Backend"""
import asyncio
import json
import logging
import os
import tempfile
from typing import Optional

import httpx
from PIL import Image

logger = logging.getLogger(__name__)


class AsyncDatalabOCRBackend:
    """Асинхронный OCR через Datalab Marker API"""

    API_URL = "https://www.datalab.to/api/v1/marker"
    MAX_WIDTH = 4000

    # Дефолтные значения
    DEFAULT_POLL_INTERVAL = 3
    DEFAULT_POLL_MAX_ATTEMPTS = 90
    DEFAULT_MAX_RETRIES = 3

    def __init__(
        self,
        api_key: str,
        rate_limiter=None,
        poll_interval: Optional[int] = None,
        poll_max_attempts: Optional[int] = None,
        max_retries: Optional[int] = None,
    ):
        if not api_key:
            raise ValueError("DATALAB_API_KEY не указан")
        self.api_key = api_key
        self.headers = {"X-Api-Key": api_key}
        self.rate_limiter = rate_limiter
        self.last_html_result: Optional[str] = None

        self.poll_interval = poll_interval if poll_interval is not None else self.DEFAULT_POLL_INTERVAL
        self.poll_max_attempts = poll_max_attempts if poll_max_attempts is not None else self.DEFAULT_POLL_MAX_ATTEMPTS
        self.max_retries = max_retries if max_retries is not None else self.DEFAULT_MAX_RETRIES

        # httpx async client с connection pooling и retry
        self._client: Optional[httpx.AsyncClient] = None

        logger.info(
            f"AsyncDatalabOCRBackend инициализирован (poll_interval={self.poll_interval}s, "
            f"poll_max_attempts={self.poll_max_attempts}, max_retries={self.max_retries})"
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Получить или создать httpx AsyncClient"""
        if self._client is None or self._client.is_closed:
            transport = httpx.AsyncHTTPTransport(
                retries=3,
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                    keepalive_expiry=30.0,
                ),
            )
            self._client = httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(120.0, connect=30.0),
            )
        return self._client

    async def close(self):
        """Закрыть HTTP клиент"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def supports_pdf_input(self) -> bool:
        """Datalab не поддерживает прямой PDF ввод"""
        return False

    async def recognize_async(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        """Асинхронно распознать изображение через Datalab API"""
        if image is None:
            return "[Ошибка: Datalab требует изображение, PDF не поддерживается]"

        # Async rate limiter
        if self.rate_limiter:
            acquired = await self._acquire_rate_limiter()
            if not acquired:
                return "[Ошибка: таймаут ожидания rate limiter]"

        try:
            # Ресайз если нужно
            if image.width > self.MAX_WIDTH:
                ratio = self.MAX_WIDTH / image.width
                new_width = self.MAX_WIDTH
                new_height = int(image.height * ratio)
                logger.info(
                    f"Сжатие изображения {image.width}x{image.height} -> {new_width}x{new_height}"
                )
                image = image.resize((new_width, new_height), Image.LANCZOS)

            # Сохранить во временный файл
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                image.save(tmp, format="PNG")
                tmp_path = tmp.name

            try:
                return await self._process_with_retries(tmp_path)
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        except Exception as e:
            logger.error(f"Ошибка AsyncDatalab OCR: {e}", exc_info=True)
            return f"[Ошибка Datalab OCR: {e}]"
        finally:
            if self.rate_limiter:
                await self._release_rate_limiter()

    async def _acquire_rate_limiter(self) -> bool:
        """Асинхронно получить разрешение от rate limiter"""
        if hasattr(self.rate_limiter, "acquire_async"):
            return await self.rate_limiter.acquire_async()
        elif hasattr(self.rate_limiter, "acquire"):
            # Fallback для sync rate limiter
            return await asyncio.to_thread(self.rate_limiter.acquire)
        return True

    async def _release_rate_limiter(self):
        """Асинхронно освободить rate limiter"""
        if hasattr(self.rate_limiter, "release_async"):
            await self.rate_limiter.release_async()
        elif hasattr(self.rate_limiter, "release"):
            self.rate_limiter.release()

    async def _process_with_retries(self, tmp_path: str) -> str:
        """Обработка с полными retry циклами"""
        client = await self._get_client()

        for full_retry in range(self.max_retries):
            if full_retry > 0:
                logger.warning(
                    f"Datalab: повторная отправка запроса (попытка {full_retry + 1}/{self.max_retries})"
                )

            # Отправка запроса с retry для 429
            response = await self._send_request_with_429_retry(client, tmp_path)

            if response is None:
                return "[Ошибка Datalab API: превышен лимит запросов (429)]"

            if response.status_code != 200:
                logger.error(
                    f"Datalab API error: {response.status_code} - {response.text}"
                )
                if response.status_code == 401:
                    return "[Ошибка Datalab API 401: Неверный или просроченный DATALAB_API_KEY]"
                elif response.status_code == 403:
                    return "[Ошибка Datalab API 403: Доступ запрещён]"
                return f"[Ошибка Datalab API: {response.status_code}]"

            result = response.json()

            if not result.get("success"):
                error = result.get("error", "Unknown error")
                return f"[Ошибка Datalab: {error}]"

            check_url = result.get("request_check_url")
            if not check_url:
                # Результат сразу в ответе
                if "json" in result:
                    json_result = result["json"]
                    if isinstance(json_result, dict):
                        return json.dumps(json_result, ensure_ascii=False)
                    return json_result
                return "[Ошибка: нет request_check_url]"

            # Polling результата
            logger.info(f"Datalab: начало поллинга результата по URL: {check_url}")
            poll_result = await self._poll_result(client, check_url)

            if poll_result is not None:
                return poll_result

            # Таймаут поллинга
            logger.warning(
                f"Datalab: таймаут поллинга после {self.poll_max_attempts} попыток, "
                f"retry {full_retry + 1}/{self.max_retries}"
            )

            if full_retry < self.max_retries - 1:
                wait_time = (full_retry + 1) * 10
                logger.info(f"Datalab: ожидание {wait_time}с перед повторной отправкой")
                await asyncio.sleep(wait_time)

        # Все retry исчерпаны
        logger.error(
            f"Datalab: превышено время ожидания после {self.max_retries} полных попыток"
        )
        logger.warning("Datalab: пропускаем блок из-за таймаута, продолжаем обработку")
        return ""

    async def _send_request_with_429_retry(
        self, client: httpx.AsyncClient, tmp_path: str
    ) -> Optional[httpx.Response]:
        """Отправить запрос с обработкой 429 ошибок"""
        response = None

        for retry in range(self.max_retries):
            with open(tmp_path, "rb") as f:
                file_content = f.read()

            files = {"file": (os.path.basename(tmp_path), file_content, "image/png")}
            data = {
                "mode": "accurate",
                "paginate": "true",
                "output_format": "html",
                "disable_image_extraction": "true",
                "disable_image_captions": "true",
                "additional_config": json.dumps({"keep_pageheader_in_output": True}),
            }

            response = await client.post(
                self.API_URL,
                headers=self.headers,
                files=files,
                data=data,
            )

            if response.status_code == 429:
                wait_time = min(60, (2**retry) * 10)
                logger.warning(
                    f"Datalab API 429: ждём {wait_time}с (попытка {retry + 1}/{self.max_retries})"
                )
                await asyncio.sleep(wait_time)
                continue

            break

        if response is not None and response.status_code == 429:
            return None

        return response

    async def _poll_result(
        self, client: httpx.AsyncClient, check_url: str
    ) -> Optional[str]:
        """Асинхронный polling результата"""
        for attempt in range(self.poll_max_attempts):
            await asyncio.sleep(self.poll_interval)

            logger.debug(
                f"Datalab: попытка поллинга {attempt + 1}/{self.poll_max_attempts}"
            )

            try:
                poll_response = await client.get(
                    check_url,
                    headers=self.headers,
                    timeout=30.0,
                )
            except httpx.TimeoutException:
                logger.warning("Datalab: таймаут при поллинге, продолжаем")
                continue

            if poll_response.status_code == 429:
                logger.warning("Datalab: 429 при поллинге, ждём 30с")
                await asyncio.sleep(30)
                continue

            if poll_response.status_code != 200:
                logger.warning(
                    f"Datalab: поллинг вернул статус {poll_response.status_code}: {poll_response.text}"
                )
                continue

            poll_result = poll_response.json()
            status = poll_result.get("status", "")

            logger.info(
                f"Datalab: текущий статус задачи: '{status}' (попытка {attempt + 1}/{self.poll_max_attempts})"
            )

            if status == "complete":
                logger.info("Datalab: задача успешно завершена")
                html_result = poll_result.get("html", "")
                logger.debug(f"Datalab: ключи ответа: {list(poll_result.keys())}")
                self.last_html_result = html_result if html_result else None
                return html_result if html_result else ""

            elif status == "failed":
                error = poll_result.get("error", "Unknown error")
                logger.error(f"Datalab: задача завершилась с ошибкой: {error}")
                return f"[Ошибка Datalab: {error}]"

            elif status not in ["processing", "pending", "queued"]:
                logger.warning(
                    f"Datalab: неизвестный статус '{status}'. Полный ответ: {poll_result}"
                )

        # Таймаут
        return None

    def __del__(self):
        """Cleanup при удалении объекта"""
        if self._client and not self._client.is_closed:
            # Нельзя использовать await в __del__, просто логируем
            logger.debug("AsyncDatalabOCRBackend: client not closed properly")
