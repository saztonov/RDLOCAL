"""Qwen OCR Backend (LM Studio / OpenAI-compatible API) — sync

Используется для IMAGE и STAMP блоков. В отличие от ChandraBackend,
принимает prompt из аргумента (image/stamp промпты из config.yaml).
"""
import logging
import threading
from typing import Optional

import httpx
from PIL import Image

from rd_core.ocr._lmstudio_helpers import LMStudioLifecycleMixin
from rd_core.ocr._qwen_common import (
    QWEN_LOAD_CONFIG,
    QWEN_MAX_IMAGE_SIZE,
    QWEN_MODEL_KEY,
    TRANSIENT_CODES,
    build_payload,
    check_non_retriable_error,
    init_base_url,
    parse_response,
)
from rd_core.ocr.http_utils import create_retry_session
from rd_core.ocr.utils import image_to_base64
from rd_core.ocr_result import is_error, make_error

logger = logging.getLogger(__name__)


class QwenBackend(LMStudioLifecycleMixin):
    """OCR через Qwen модель (LM Studio, OpenAI-compatible API).

    Для IMAGE и STAMP блоков. Промпты передаются через аргумент recognize().

    Args:
        base_url: URL LM Studio сервера.
        http_timeout: таймаут HTTP-запроса.
        model_config: опциональный dict с настройками из config.yaml.
            Ключи: model_key, context_length, flash_attention, eval_batch_size,
            offload_kv_cache, max_image_size, preload_timeout, max_retries,
            retry_delays, default_system_prompt, default_user_prompt, max_tokens,
            temperature, top_p, top_k, repetition_penalty, min_p.
    """

    _BACKEND_NAME = "Qwen"
    _MODEL_KEY = QWEN_MODEL_KEY
    _LOAD_CONFIG = QWEN_LOAD_CONFIG
    _PRELOAD_TIMEOUT = 120  # Qwen модель больше, даём больше времени
    _MAX_APP_RETRIES = 3
    _APP_RETRY_DELAYS = [30, 60, 120]

    def __init__(
        self,
        base_url: Optional[str] = None,
        http_timeout: int = 90,
        model_config: Optional[dict] = None,
        **kwargs,
    ):
        cfg = model_config or {}

        # Переопределяем class-level атрибуты instance-level значениями из конфига
        if "model_key" in cfg:
            self._MODEL_KEY = cfg["model_key"]
        if any(k in cfg for k in ("context_length", "flash_attention", "eval_batch_size", "offload_kv_cache")):
            self._LOAD_CONFIG = {
                "context_length": cfg.get("context_length", QWEN_LOAD_CONFIG["context_length"]),
                "flash_attention": cfg.get("flash_attention", QWEN_LOAD_CONFIG["flash_attention"]),
                "eval_batch_size": cfg.get("eval_batch_size", QWEN_LOAD_CONFIG["eval_batch_size"]),
                "offload_kv_cache_to_gpu": cfg.get("offload_kv_cache", QWEN_LOAD_CONFIG["offload_kv_cache_to_gpu"]),
            }
        if "preload_timeout" in cfg:
            self._PRELOAD_TIMEOUT = cfg["preload_timeout"]
        if "max_retries" in cfg:
            self._MAX_APP_RETRIES = cfg["max_retries"]
        if "retry_delays" in cfg:
            self._APP_RETRY_DELAYS = cfg["retry_delays"]

        self._max_image_size = cfg.get("max_image_size", QWEN_MAX_IMAGE_SIZE)
        self._inference_params = {
            k: cfg[k] for k in (
                "default_system_prompt", "default_user_prompt",
                "max_tokens", "temperature", "top_p", "top_k",
                "repetition_penalty", "min_p",
            ) if k in cfg
        }

        self.base_url = init_base_url(base_url)
        self._model_id: Optional[str] = None
        self._model_lock = threading.Lock()
        self._server_unreachable = False
        self.session = create_retry_session()
        self._preload_session = create_retry_session(preload_mode=True)
        self._deadline: Optional[float] = None
        self._cancel_event: Optional[threading.Event] = None
        self._http_timeout = http_timeout
        logger.info(f"QwenBackend инициализирован (base_url: {self.base_url})")

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
            return make_error("Qwen требует изображение")

        try:
            model_id = self._discover_model()
            img_b64 = image_to_base64(image, max_size=self._max_image_size)
            payload = build_payload(
                model_id, prompt, img_b64,
                inference_params=self._inference_params or None,
            )

            last_error = None
            for attempt in range(self._MAX_APP_RETRIES + 1):
                if attempt > 0:
                    delay = self._APP_RETRY_DELAYS[min(attempt - 1, len(self._APP_RETRY_DELAYS) - 1)]

                    if self._is_budget_exhausted(delay):
                        logger.warning(
                            f"Qwen: time budget exhausted before retry {attempt}, "
                            f"aborting (last error: {last_error})"
                        )
                        return make_error(
                            f"Qwen: time budget exhausted после {attempt - 1} попыток"
                        )

                    logger.warning(
                        f"Qwen API retry {attempt}/{self._MAX_APP_RETRIES}, "
                        f"ожидание {delay}с (предыдущая ошибка: {last_error})"
                    )
                    if self._interruptible_sleep(delay):
                        return make_error("Qwen: операция отменена")

                if self._is_budget_exhausted(self._http_timeout):
                    logger.warning(
                        f"Qwen: time budget exhausted before request (attempt {attempt}), aborting"
                    )
                    return make_error(
                        f"Qwen: time budget exhausted перед запросом (attempt {attempt})"
                    )

                try:
                    response = self.session.post(
                        f"{self.base_url}/v1/chat/completions",
                        headers={"Content-Type": "application/json"},
                        json=payload, timeout=self._http_timeout,
                    )
                except httpx.ConnectError as e:
                    last_error = f"ConnectionError: {e}"
                    logger.warning(f"Qwen connection error (attempt {attempt}): {e}")
                    if attempt < self._MAX_APP_RETRIES:
                        continue
                    return make_error(f"Qwen: {last_error} после {self._MAX_APP_RETRIES} попыток")
                except httpx.TimeoutException:
                    last_error = "Timeout"
                    logger.warning(f"Qwen timeout (attempt {attempt})")
                    if attempt < self._MAX_APP_RETRIES:
                        continue
                    return make_error("превышен таймаут запроса к Qwen")

                if response.status_code == 200:
                    break

                non_retriable = check_non_retriable_error(response.status_code, response.text)
                if non_retriable:
                    return non_retriable

                if response.status_code in TRANSIENT_CODES:
                    last_error = f"HTTP {response.status_code}"
                    if attempt < self._MAX_APP_RETRIES:
                        logger.warning(f"Qwen transient error {response.status_code} (attempt {attempt}), will retry")
                        continue
                    return make_error(f"Qwen API: {response.status_code} после {self._MAX_APP_RETRIES} попыток")

                error_detail = response.text[:500] if response.text else "No details"
                logger.error(f"Qwen API error: {response.status_code} - {error_detail}")
                return make_error(f"Qwen API: {response.status_code}")

            text = parse_response(response.json())
            if not is_error(text):
                logger.debug(f"Qwen OCR: распознано {len(text)} символов")
            return text

        except Exception as e:
            logger.error(f"Ошибка Qwen OCR: {e}", exc_info=True)
            return make_error(f"Qwen OCR: {e}")
