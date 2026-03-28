"""Chandra OCR Backend (LM Studio / OpenAI-compatible API) — sync"""
import logging
import threading
from typing import Optional

import httpx
from PIL import Image

from rd_core.ocr._chandra_common import (
    CHANDRA_LOAD_CONFIG,
    CHANDRA_MAX_IMAGE_SIZE,
    CHANDRA_MODEL_KEY,
    TRANSIENT_CODES,
    build_payload,
    check_non_retriable_error,
    init_base_url,
    parse_response,
)
from rd_core.ocr._lmstudio_helpers import LMStudioLifecycleMixin
from rd_core.ocr.http_utils import create_retry_session
from rd_core.ocr.utils import image_to_base64
from rd_core.ocr_result import is_error, make_error

logger = logging.getLogger(__name__)


class ChandraBackend(LMStudioLifecycleMixin):
    """OCR через Chandra модель (LM Studio, OpenAI-compatible API)"""

    _BACKEND_NAME = "Chandra"
    _MODEL_KEY = CHANDRA_MODEL_KEY
    _LOAD_CONFIG = CHANDRA_LOAD_CONFIG
    _PRELOAD_TIMEOUT = 60
    _MAX_APP_RETRIES = 3
    _APP_RETRY_DELAYS = [30, 60, 120]

    def __init__(self, base_url: Optional[str] = None, http_timeout: int = 90, **kwargs):
        self.base_url = init_base_url(base_url)
        self._model_id: Optional[str] = None
        self._model_lock = threading.Lock()
        self.session = create_retry_session()
        self._preload_session = create_retry_session(preload_mode=True)
        self._deadline: Optional[float] = None
        self._cancel_event: Optional[threading.Event] = None
        self._http_timeout = http_timeout
        logger.info(f"ChandraBackend инициализирован (base_url: {self.base_url})")

    def supports_pdf_input(self) -> bool:
        return False

    def recognize(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool | None = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        if image is None:
            return make_error("Chandra требует изображение")

        try:
            model_id = self._discover_model()
            img_b64 = image_to_base64(image, max_size=CHANDRA_MAX_IMAGE_SIZE)
            payload = build_payload(model_id, prompt, img_b64)

            last_error = None
            for attempt in range(self._MAX_APP_RETRIES + 1):
                if attempt > 0:
                    delay = self._APP_RETRY_DELAYS[min(attempt - 1, len(self._APP_RETRY_DELAYS) - 1)]

                    if self._is_budget_exhausted(delay):
                        logger.warning(
                            f"Chandra: time budget exhausted before retry {attempt}, "
                            f"aborting (last error: {last_error})"
                        )
                        return make_error(
                            f"Chandra: time budget exhausted после {attempt - 1} попыток"
                        )

                    logger.warning(
                        f"Chandra API retry {attempt}/{self._MAX_APP_RETRIES}, "
                        f"ожидание {delay}с (предыдущая ошибка: {last_error})"
                    )
                    if self._interruptible_sleep(delay):
                        return make_error("Chandra: операция отменена")

                if self._is_budget_exhausted(self._http_timeout):
                    logger.warning(
                        f"Chandra: time budget exhausted before request (attempt {attempt}), aborting"
                    )
                    return make_error(
                        f"Chandra: time budget exhausted перед запросом (attempt {attempt})"
                    )

                try:
                    response = self.session.post(
                        f"{self.base_url}/v1/chat/completions",
                        headers={"Content-Type": "application/json"},
                        json=payload, timeout=self._http_timeout,
                    )
                except httpx.ConnectError as e:
                    last_error = f"ConnectionError: {e}"
                    logger.warning(f"Chandra connection error (attempt {attempt}): {e}")
                    if attempt < self._MAX_APP_RETRIES:
                        continue
                    return make_error(f"Chandra: {last_error} после {self._MAX_APP_RETRIES} попыток")
                except httpx.TimeoutException:
                    last_error = "Timeout"
                    logger.warning(f"Chandra timeout (attempt {attempt})")
                    if attempt < self._MAX_APP_RETRIES:
                        continue
                    return make_error("превышен таймаут запроса к Chandra")

                if response.status_code == 200:
                    break

                non_retriable = check_non_retriable_error(response.status_code, response.text)
                if non_retriable:
                    return non_retriable

                if response.status_code in TRANSIENT_CODES:
                    last_error = f"HTTP {response.status_code}"
                    if attempt < self._MAX_APP_RETRIES:
                        logger.warning(f"Chandra transient error {response.status_code} (attempt {attempt}), will retry")
                        continue
                    return make_error(f"Chandra API: {response.status_code} после {self._MAX_APP_RETRIES} попыток")

                error_detail = response.text[:500] if response.text else "No details"
                logger.error(f"Chandra API error: {response.status_code} - {error_detail}")
                return make_error(f"Chandra API: {response.status_code}")

            text = parse_response(response.json())
            if not is_error(text):
                logger.debug(f"Chandra OCR: распознано {len(text)} символов")
            return text

        except Exception as e:
            logger.error(f"Ошибка Chandra OCR: {e}", exc_info=True)
            return make_error(f"Chandra OCR: {e}")
