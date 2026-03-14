"""HTTP-клиент для удалённого OCR сервера."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.ocr_client.exceptions import (
    AuthenticationError,
    PayloadTooLargeError,
    RemoteOCRError,
    ServerError,
)
from app.ocr_client.http_pool import get_remote_ocr_client
from app.ocr_client.job_create import JobCreateMixin
from app.ocr_client.job_download import JobDownloadMixin
from app.ocr_client.job_lifecycle import JobLifecycleMixin
from app.ocr_client.job_read import JobReadMixin
from app.ocr_client.utils import hash_pdf

logger = logging.getLogger(__name__)


@dataclass
class RemoteOCRClient(
    JobCreateMixin, JobReadMixin, JobLifecycleMixin, JobDownloadMixin
):
    """Клиент для удалённого OCR сервера"""

    base_url: str = field(
        default_factory=lambda: os.getenv(
            "REMOTE_OCR_BASE_URL", "http://localhost:8000"
        )
    )
    api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("REMOTE_OCR_API_KEY")
    )
    timeout: float = 120.0
    upload_timeout: float = 600.0  # Для POST /jobs - большие PDF
    max_retries: int = 3

    def __post_init__(self):
        """Логирование конфигурации при инициализации"""
        logger.info(
            f"RemoteOCRClient initialized: base_url={self.base_url}, "
            f"api_key={'***' if self.api_key else 'None'}"
        )

    def _headers(self) -> dict:
        """Получить заголовки для запросов"""
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _handle_response_error(self, resp: httpx.Response):
        """Обработать ошибки ответа с понятными сообщениями"""
        if resp.status_code == 401:
            raise AuthenticationError("Неверный API ключ (REMOTE_OCR_API_KEY)")
        elif resp.status_code == 413:
            raise PayloadTooLargeError("Файл слишком большой для загрузки")
        elif resp.status_code >= 500:
            raise ServerError(f"Ошибка сервера: {resp.status_code}")
        resp.raise_for_status()

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        **kwargs,
    ) -> httpx.Response:
        """Выполнить запрос с ретраями и exponential backoff"""
        timeout = timeout or self.timeout
        retries = retries if retries is not None else self.max_retries

        last_error = None
        client = get_remote_ocr_client(self.base_url, timeout)
        for attempt in range(retries):
            try:
                resp = getattr(client, method)(
                    path, headers=self._headers(), timeout=timeout, **kwargs
                )

                # Для 5xx - ретраим
                if resp.status_code >= 500 and attempt < retries - 1:
                    delay = 2**attempt  # 1, 2, 4 сек
                    logger.warning(
                        f"Сервер вернул {resp.status_code}, ретрай через {delay}с..."
                    )
                    time.sleep(delay)
                    continue

                self._handle_response_error(resp)
                return resp

            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.TimeoutException,
                httpx.NetworkError,
            ) as e:
                last_error = e
                if attempt < retries - 1:
                    delay = 2**attempt
                    logger.warning(f"Сетевая ошибка: {e}, ретрай через {delay}с...")
                    time.sleep(delay)
                    continue
                # Не выбрасываем исключение - просто логируем
                logger.error(f"Все попытки подключения исчерпаны: {e}")
                if isinstance(e, (ConnectionError, TimeoutError, OSError)):
                    raise RemoteOCRError(f"Сервер недоступен: {e}")
                raise

        if last_error:
            raise last_error

    @staticmethod
    def hash_pdf(path: str) -> str:
        """Вычислить SHA256 хеш PDF файла."""
        return hash_pdf(path)

    def health(self) -> bool:
        """Проверить доступность сервера"""
        url = f"{self.base_url}/health"
        try:
            logger.debug(f"Health check: GET {url}")
            client = get_remote_ocr_client(self.base_url, self.timeout)
            resp = client.get("/health", headers=self._headers(), timeout=2.0)
            logger.debug(f"Health check response: {resp.status_code}")
            return resp.status_code == 200 and resp.json().get("ok", False)
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.TimeoutException,
            httpx.NetworkError,
        ) as e:
            logger.debug(f"Health check network error: {e}")
            return False
        except Exception as e:
            logger.warning(f"Health check failed: {url} -> {e}")
            return False

    def delete_job(self, job_id: str) -> bool:
        """Удалить задачу и все связанные файлы."""
        resp = self._request_with_retry("delete", f"/jobs/{job_id}")
        return resp.json().get("ok", False)


# Для обратной совместимости
RemoteOcrClient = RemoteOCRClient
