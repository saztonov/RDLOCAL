"""Datalab OCR Backend (sync)"""
import logging
import os
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

        self.session = create_retry_session()
        logger.info(
            f"Datalab OCR инициализирован (poll_interval={self.poll_interval}s, "
            f"poll_max_attempts={self.poll_max_attempts}, max_retries={self.max_retries}, "
            f"extras={self.extras}, quality_threshold={self.quality_threshold})"
        )

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
            return "[Ошибка: Datalab требует изображение или PDF]"
        tmp_path, mime_type, need_cleanup = source

        if self.rate_limiter:
            if not self.rate_limiter.acquire():
                return "[Ошибка: таймаут ожидания rate limiter]"

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
                            logger.warning(f"Datalab API 429: ждём {wait_time}с (попытка {retry + 1}/{self.max_retries})")
                            time.sleep(wait_time)
                            continue
                        break

                    if response is None or response.status_code == 429:
                        return "[Ошибка Datalab API: превышен лимит запросов (429)]"

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
                        time.sleep(self.poll_interval)

                        logger.debug(f"Datalab: попытка поллинга {attempt + 1}/{self.poll_max_attempts}")
                        poll_response = self.session.get(check_url, headers=self.headers, timeout=30)

                        if poll_response.status_code == 429:
                            logger.warning("Datalab: 429 при поллинге, ждём 30с")
                            time.sleep(30)
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
                            return f"[Ошибка Datalab: {error}]"
                        elif status not in ["processing", "pending", "queued"]:
                            logger.warning(f"Datalab: неизвестный статус '{status}'. Полный ответ: {poll_result}")

                    if low_quality_retry:
                        if full_retry < self.max_retries - 1:
                            wait_time = (full_retry + 1) * 5
                            logger.info(f"Datalab: ожидание {wait_time}с перед retry из-за низкого качества")
                            time.sleep(wait_time)
                        continue

                    logger.warning(
                        f"Datalab: таймаут поллинга после {self.poll_max_attempts} попыток, "
                        f"retry {full_retry + 1}/{self.max_retries}"
                    )
                    if full_retry < self.max_retries - 1:
                        wait_time = (full_retry + 1) * 10
                        logger.info(f"Datalab: ожидание {wait_time}с перед повторной отправкой")
                        time.sleep(wait_time)

                logger.error(f"Datalab: превышено время ожидания после {self.max_retries} полных попыток")
                logger.warning("Datalab: пропускаем блок из-за таймаута, продолжаем обработку")
                return "[Ошибка Datalab: таймаут после повторных попыток]"

            finally:
                if need_cleanup and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        except Exception as e:
            logger.error(f"Ошибка Datalab OCR: {e}", exc_info=True)
            return f"[Ошибка Datalab OCR: {e}]"
        finally:
            if self.rate_limiter:
                self.rate_limiter.release()
