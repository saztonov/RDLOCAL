"""Datalab OCR Backend (sync)"""
import logging
import os
import threading
import time
from typing import Optional

import requests
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
from rd_core.ocr.http_utils import create_retry_session
from rd_core.ocr_result import make_error

logger = logging.getLogger(__name__)


class DatalabOCRBackend:
    """OCR через Datalab Convert API"""

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

        self._deadline: Optional[float] = None
        self._cancel_event: Optional[threading.Event] = None
        self.session = create_retry_session()
        logger.info(
            f"Datalab OCR инициализирован (poll_interval={self.poll_interval}s, "
            f"poll_max_attempts={self.poll_max_attempts}, max_retries={self.max_retries}, "
            f"extras={self.extras}, quality_threshold={self.quality_threshold})"
        )

    def set_deadline(self, deadline: float) -> None:
        """Установить крайний срок (unix timestamp) для прекращения retry/polling."""
        self._deadline = deadline

    def set_cancel_event(self, event: threading.Event) -> None:
        """Установить event для кооперативной отмены."""
        self._cancel_event = event

    def _interruptible_sleep(self, seconds: float) -> bool:
        """Sleep с проверкой отмены. Возвращает True если отменено."""
        if self._cancel_event:
            return self._cancel_event.wait(timeout=seconds)
        time.sleep(seconds)
        return False

    def _is_budget_exhausted(self, planned_delay: float = 0, reserve: float = 120) -> bool:
        """Проверить, хватает ли времени на delay + reserve."""
        if self._deadline is None:
            return False
        return time.time() + planned_delay > self._deadline - reserve

    def supports_pdf_input(self) -> bool:
        return True

    def recognize(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        source = prepare_source(image, pdf_file_path)
        if source is None:
            return make_error("Datalab требует изображение или PDF")
        tmp_path, mime_type, need_cleanup = source

        if self.rate_limiter:
            if not self.rate_limiter.acquire():
                return make_error("таймаут ожидания rate limiter")

        try:
            try:
                for full_retry in range(self.max_retries):
                    if full_retry > 0:
                        logger.warning(
                            f"Datalab: повторная отправка запроса (попытка {full_retry + 1}/{self.max_retries})"
                        )

                    # Отправка с retry для 429
                    response = None
                    for retry in range(self.max_retries):
                        with open(tmp_path, "rb") as f:
                            files = {"file": (os.path.basename(tmp_path), f, mime_type)}
                            data = build_request_data(self.extras, skip_cache=full_retry > 0)

                            response = self.session.post(
                                API_URL, headers=self.headers,
                                files=files, data=data, timeout=120,
                            )

                        if response.status_code == 429:
                            wait_time = min(60, (2**retry) * 10)
                            if self._is_budget_exhausted(wait_time):
                                return make_error("Datalab: time budget exhausted (429 retry)")
                            logger.warning(f"Datalab API 429: ждём {wait_time}с (попытка {retry + 1}/{self.max_retries})")
                            if self._interruptible_sleep(wait_time):
                                return make_error("Datalab: операция отменена")
                            continue
                        break

                    if response is None or response.status_code == 429:
                        return make_error("Datalab API: превышен лимит запросов (429)")

                    if response.status_code != 200:
                        return handle_http_error(response.status_code, response.text)

                    result = response.json()
                    immediate = handle_immediate_result(result)
                    if immediate is not None:
                        return immediate

                    check_url = result["request_check_url"]
                    logger.info(f"Datalab: начало поллинга результата по URL: {check_url}")
                    low_quality_retry = False

                    for attempt in range(self.poll_max_attempts):
                        if self._is_budget_exhausted(self.poll_interval):
                            logger.warning("Datalab: time budget exhausted during polling")
                            break
                        if self._interruptible_sleep(self.poll_interval):
                            return make_error("Datalab: операция отменена")

                        logger.debug(f"Datalab: попытка поллинга {attempt + 1}/{self.poll_max_attempts}")
                        poll_response = self.session.get(check_url, headers=self.headers, timeout=30)

                        if poll_response.status_code == 429:
                            logger.warning("Datalab: 429 при поллинге, ждём 30с")
                            if self._interruptible_sleep(30):
                                return make_error("Datalab: операция отменена")
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

                            if (quality is not None and quality < self.quality_threshold
                                    and full_retry < self.max_retries - 1):
                                logger.warning(
                                    f"Datalab: низкое качество {quality} < {self.quality_threshold}, "
                                    f"retry {full_retry + 1}/{self.max_retries}"
                                )
                                low_quality_retry = True
                                break
                            return html_result

                        elif status == "failed":
                            error = poll_result.get("error", "Unknown error")
                            logger.error(f"Datalab: задача завершилась с ошибкой: {error}")
                            return make_error(f"Datalab: {error}")
                        elif status not in ["processing", "pending", "queued"]:
                            logger.warning(f"Datalab: неизвестный статус '{status}'. Полный ответ: {poll_result}")

                    if low_quality_retry:
                        if full_retry < self.max_retries - 1:
                            wait_time = (full_retry + 1) * 5
                            if self._is_budget_exhausted(wait_time):
                                logger.warning("Datalab: time budget exhausted, skipping quality retry")
                                return self.last_html_result or make_error("Datalab: time budget exhausted")
                            logger.info(f"Datalab: ожидание {wait_time}с перед retry из-за низкого качества")
                            if self._interruptible_sleep(wait_time):
                                return make_error("Datalab: операция отменена")
                        continue

                    logger.warning(
                        f"Datalab: таймаут поллинга после {self.poll_max_attempts} попыток, "
                        f"retry {full_retry + 1}/{self.max_retries}"
                    )
                    if full_retry < self.max_retries - 1:
                        wait_time = (full_retry + 1) * 10
                        if self._is_budget_exhausted(wait_time):
                            return make_error("Datalab: time budget exhausted")
                        logger.info(f"Datalab: ожидание {wait_time}с перед повторной отправкой")
                        if self._interruptible_sleep(wait_time):
                            return make_error("Datalab: операция отменена")

                logger.error(f"Datalab: превышено время ожидания после {self.max_retries} полных попыток")
                logger.warning("Datalab: пропускаем блок из-за таймаута, продолжаем обработку")
                return make_error("Datalab: таймаут после повторных попыток")

            finally:
                if need_cleanup and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        except Exception as e:
            logger.error(f"Ошибка Datalab OCR: {e}", exc_info=True)
            return make_error(f"Datalab OCR: {e}")
        finally:
            if self.rate_limiter:
                self.rate_limiter.release()
