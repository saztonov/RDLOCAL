"""Chandra OCR Backend (LM Studio / OpenAI-compatible API) — sync"""
import logging
import threading
import time
from typing import Optional

import requests
from PIL import Image

from rd_core.ocr._chandra_common import (
    CHANDRA_LOAD_CONFIG,
    CHANDRA_MODEL_KEY,
    TRANSIENT_CODES,
    build_payload,
    check_non_retriable_error,
    get_ngrok_auth,
    init_base_url,
    needs_model_reload,
    parse_response,
)
from rd_core.ocr.http_utils import create_retry_session
from rd_core.ocr.utils import image_to_base64

logger = logging.getLogger(__name__)


class ChandraBackend:
    """OCR через Chandra модель (LM Studio, OpenAI-compatible API)"""

    _MAX_APP_RETRIES = 3
    _APP_RETRY_DELAYS = [30, 60, 120]

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = init_base_url(base_url)
        self._model_id: Optional[str] = None
        self._model_lock = threading.Lock()
        self._auth = get_ngrok_auth()
        self.session = create_retry_session(auth=self._auth, ngrok_mode=True)
        logger.info(f"ChandraBackend инициализирован (base_url: {self.base_url})")

    def _discover_model(self) -> str:
        if self._model_id:
            return self._model_id

        with self._model_lock:
            if self._model_id:
                return self._model_id

            self._ensure_model_loaded()

            try:
                resp = self.session.get(f"{self.base_url}/v1/models", timeout=30)
                if resp.status_code == 200:
                    for m in resp.json().get("data", []):
                        if "chandra" in m.get("id", "").lower():
                            self._model_id = m["id"]
                            logger.info(f"Chandra модель найдена: {self._model_id}")
                            return self._model_id
            except Exception as e:
                logger.warning(f"Ошибка определения модели Chandra: {e}")

            self._model_id = "chandra-ocr"
            logger.info(f"Chandra модель не найдена, используется fallback: {self._model_id}")
            return self._model_id

    def preload(self) -> None:
        self._discover_model()
        logger.info(f"Chandra модель предзагружена: {self._model_id}")

    def _ensure_model_loaded(self) -> None:
        required_ctx = CHANDRA_LOAD_CONFIG["context_length"]
        try:
            resp = self.session.get(f"{self.base_url}/api/v1/models", timeout=10)
            if resp.status_code != 200:
                logger.debug("LM Studio native API недоступен, пропускаем preload")
                return

            models = resp.json().get("models", [])
            actual_key = CHANDRA_MODEL_KEY

            for m in models:
                if "chandra" in m.get("key", "").lower():
                    loaded = m.get("loaded_instances", [])
                    need_reload, reason = needs_model_reload(loaded, required_ctx)

                    if not need_reload:
                        logger.debug(f"Модель {m['key']}: {reason}")
                        return

                    logger.info(f"Модель {m['key']}: {reason}, выполняем reload")
                    for inst in loaded:
                        try:
                            self.session.post(
                                f"{self.base_url}/api/v1/models/unload",
                                json={"instance_id": inst["id"]}, timeout=30,
                            )
                            logger.debug(f"Выгружен инстанс: {inst['id']}")
                        except Exception as e:
                            logger.warning(f"Ошибка выгрузки {inst.get('id')}: {e}")
                    actual_key = m.get("key", CHANDRA_MODEL_KEY)
                    break

            logger.info(f"Загружаем модель {actual_key} (context_length={required_ctx})")
            load_resp = self.session.post(
                f"{self.base_url}/api/v1/models/load",
                json={"model": actual_key, "echo_load_config": True, **CHANDRA_LOAD_CONFIG},
                timeout=120,
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
        if not self._model_id:
            return
        try:
            resp = self.session.get(f"{self.base_url}/api/v1/models", timeout=10)
            if resp.status_code != 200:
                return

            for m in resp.json().get("models", []):
                if "chandra" in m.get("key", "").lower():
                    for inst in m.get("loaded_instances", []):
                        self.session.post(
                            f"{self.base_url}/api/v1/models/unload",
                            json={"instance_id": inst["id"]}, timeout=30,
                        )
                        logger.info(f"Модель выгружена: {inst['id']}")
                    break
        except Exception as e:
            logger.debug(f"Ошибка выгрузки модели: {e}")

    def supports_pdf_input(self) -> bool:
        return False

    def recognize(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        if image is None:
            return "[Ошибка: Chandra требует изображение]"

        try:
            model_id = self._discover_model()
            img_b64 = image_to_base64(image)
            payload = build_payload(model_id, prompt, img_b64)

            last_error = None
            for attempt in range(self._MAX_APP_RETRIES + 1):
                if attempt > 0:
                    delay = self._APP_RETRY_DELAYS[min(attempt - 1, len(self._APP_RETRY_DELAYS) - 1)]
                    logger.warning(
                        f"Chandra API retry {attempt}/{self._MAX_APP_RETRIES}, "
                        f"ожидание {delay}с (предыдущая ошибка: {last_error})"
                    )
                    time.sleep(delay)

                try:
                    response = self.session.post(
                        f"{self.base_url}/v1/chat/completions",
                        headers={"Content-Type": "application/json", "ngrok-skip-browser-warning": "true"},
                        json=payload, timeout=300,
                    )
                except requests.exceptions.ConnectionError as e:
                    last_error = f"ConnectionError: {e}"
                    logger.warning(f"Chandra connection error (attempt {attempt}): {e}")
                    if attempt < self._MAX_APP_RETRIES:
                        continue
                    return f"[Ошибка Chandra: {last_error} после {self._MAX_APP_RETRIES} попыток]"
                except requests.exceptions.Timeout:
                    last_error = "Timeout"
                    logger.warning(f"Chandra timeout (attempt {attempt})")
                    if attempt < self._MAX_APP_RETRIES:
                        continue
                    return "[Ошибка: превышен таймаут запроса к Chandra]"

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
                    return f"[Ошибка Chandra API: {response.status_code} после {self._MAX_APP_RETRIES} попыток]"

                error_detail = response.text[:500] if response.text else "No details"
                logger.error(f"Chandra API error: {response.status_code} - {error_detail}")
                return f"[Ошибка Chandra API: {response.status_code}]"

            text = parse_response(response.json())
            if not text.startswith("[Ошибка"):
                logger.debug(f"Chandra OCR: распознано {len(text)} символов")
            return text

        except Exception as e:
            logger.error(f"Ошибка Chandra OCR: {e}", exc_info=True)
            return f"[Ошибка Chandra OCR: {e}]"
