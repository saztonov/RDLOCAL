"""Datalab OCR Backend"""
import logging
from typing import Optional

import requests
from PIL import Image

from rd_core.ocr.http_utils import create_retry_session

logger = logging.getLogger(__name__)


class DatalabOCRBackend:
    """OCR через Datalab Convert API"""

    API_URL = "https://www.datalab.to/api/v1/convert"
    MAX_WIDTH = 4000

    # Дефолтные значения (переопределяются через настройки)
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
        self.last_html_result: Optional[str] = None  # HTML результат последнего запроса

        # Настройки polling (из параметров или дефолт)
        self.poll_interval = poll_interval if poll_interval is not None else self.DEFAULT_POLL_INTERVAL
        self.poll_max_attempts = poll_max_attempts if poll_max_attempts is not None else self.DEFAULT_POLL_MAX_ATTEMPTS
        self.max_retries = max_retries if max_retries is not None else self.DEFAULT_MAX_RETRIES

        self.session = create_retry_session()
        logger.info(
            f"Datalab OCR инициализирован (poll_interval={self.poll_interval}s, "
            f"poll_max_attempts={self.poll_max_attempts}, max_retries={self.max_retries})"
        )

    def supports_pdf_input(self) -> bool:
        """Datalab не поддерживает прямой PDF ввод"""
        return False

    def recognize(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        """Распознать изображение через Datalab API"""
        if image is None:
            return "[Ошибка: Datalab требует изображение, PDF не поддерживается]"
        import os
        import tempfile
        import time

        if self.rate_limiter:
            if not self.rate_limiter.acquire():
                return "[Ошибка: таймаут ожидания rate limiter]"

        try:
            if image.width > self.MAX_WIDTH:
                ratio = self.MAX_WIDTH / image.width
                new_width = self.MAX_WIDTH
                new_height = int(image.height * ratio)
                logger.info(
                    f"Сжатие изображения {image.width}x{image.height} -> {new_width}x{new_height}"
                )
                image = image.resize((new_width, new_height), Image.LANCZOS)

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                image.save(tmp, format="PNG")
                tmp_path = tmp.name

            try:
                # Внешний retry loop для повторной отправки при таймауте polling
                for full_retry in range(self.max_retries):
                    if full_retry > 0:
                        logger.warning(
                            f"Datalab: повторная отправка запроса (попытка {full_retry + 1}/{self.max_retries})"
                        )

                    response = None
                    for retry in range(self.max_retries):
                        with open(tmp_path, "rb") as f:
                            import json

                            files = {"file": (os.path.basename(tmp_path), f, "image/png")}
                            data = {
                                "mode": "accurate",
                                "paginate": "true",
                                "output_format": "html",
                                "disable_image_extraction": "true",
                                "disable_image_captions": "true",
                                "additional_config": json.dumps(
                                    {"keep_pageheader_in_output": True}
                                ),
                            }

                            response = self.session.post(
                                self.API_URL,
                                headers=self.headers,
                                files=files,
                                data=data,
                                timeout=120,
                            )

                        if response.status_code == 429:
                            wait_time = min(60, (2**retry) * 10)
                            logger.warning(
                                f"Datalab API 429: ждём {wait_time}с (попытка {retry + 1}/{self.max_retries})"
                            )
                            time.sleep(wait_time)
                            continue
                        break

                    if response is None or response.status_code == 429:
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
                        if "json" in result:
                            json_result = result["json"]
                            if isinstance(json_result, dict):
                                import json as json_lib

                                return json_lib.dumps(json_result, ensure_ascii=False)
                            return json_result
                        return "[Ошибка: нет request_check_url]"

                    logger.info(f"Datalab: начало поллинга результата по URL: {check_url}")
                    poll_timeout = False
                    for attempt in range(self.poll_max_attempts):
                        time.sleep(self.poll_interval)

                        logger.debug(
                            f"Datalab: попытка поллинга {attempt + 1}/{self.poll_max_attempts}"
                        )
                        poll_response = self.session.get(
                            check_url, headers=self.headers, timeout=30
                        )

                        if poll_response.status_code == 429:
                            logger.warning("Datalab: 429 при поллинге, ждём 30с")
                            time.sleep(30)
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
                            quality = poll_result.get("parse_quality_score")
                            runtime = poll_result.get("runtime")
                            logger.info(
                                f"Datalab: задача успешно завершена"
                                f"{f', quality={quality}' if quality is not None else ''}"
                                f"{f', runtime={runtime}ms' if runtime is not None else ''}"
                            )
                            html_result = poll_result.get("html", "")
                            logger.debug(
                                f"Datalab: ключи ответа: {list(poll_result.keys())}"
                            )
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

                    # Таймаут поллинга - попробуем отправить новый запрос
                    logger.warning(
                        f"Datalab: таймаут поллинга после {self.poll_max_attempts} попыток, "
                        f"retry {full_retry + 1}/{self.max_retries}"
                    )
                    poll_timeout = True

                    if full_retry < self.max_retries - 1:
                        # Ждём перед повторной отправкой
                        wait_time = (full_retry + 1) * 10
                        logger.info(f"Datalab: ожидание {wait_time}с перед повторной отправкой")
                        time.sleep(wait_time)

                # Все retry исчерпаны
                logger.error(
                    f"Datalab: превышено время ожидания после {self.max_retries} полных попыток"
                )
                logger.warning(
                    f"Datalab: пропускаем блок из-за таймаута, продолжаем обработку"
                )
                return ""

            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        except Exception as e:
            logger.error(f"Ошибка Datalab OCR: {e}", exc_info=True)
            return f"[Ошибка Datalab OCR: {e}]"
        finally:
            if self.rate_limiter:
                self.rate_limiter.release()
