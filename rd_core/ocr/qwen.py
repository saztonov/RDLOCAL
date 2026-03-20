"""Qwen OCR Backend (LM Studio / OpenAI-compatible API) — sync

Два режима работы:
  mode="text"  — распознавание TEXT/TABLE блоков строительной документации
  mode="stamp" — распознавание штампов (основных надписей)
"""
import logging
import threading
import time
from typing import Optional

import requests
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
from rd_core.ocr.http_utils import create_retry_session
from rd_core.ocr.utils import image_to_base64
from rd_core.ocr_result import is_error, make_error

logger = logging.getLogger(__name__)


class QwenBackend:
    """OCR через Qwen модель (LM Studio, OpenAI-compatible API)"""

    _MAX_APP_RETRIES = 3
    _APP_RETRY_DELAYS = [30, 60, 120]

    def __init__(self, base_url: Optional[str] = None, mode: str = "text"):
        self.mode = mode
        self.base_url = init_base_url(base_url)
        self._model_id: Optional[str] = None
        self._model_lock = threading.Lock()
        self._auth = get_ngrok_auth()
        self.session = create_retry_session(auth=self._auth, ngrok_mode=True)
        self._preload_session = create_retry_session(auth=self._auth, preload_mode=True)
        self._deadline: Optional[float] = None
        self._cancel_event: Optional[threading.Event] = None
        logger.info(f"QwenBackend инициализирован (base_url: {self.base_url}, mode: {self.mode})")

    def set_deadline(self, deadline: float) -> None:
        """Установить крайний срок (unix timestamp) для прекращения retry."""
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
                    model_key_lower = QWEN_MODEL_KEY.lower()
                    for m in resp.json().get("data", []):
                        mid = m.get("id", "").lower()
                        if model_key_lower in mid or mid in model_key_lower:
                            self._model_id = m["id"]
                            logger.info(f"Qwen модель найдена: {self._model_id}")
                            return self._model_id
            except Exception as e:
                logger.warning(f"Ошибка определения модели Qwen: {e}")

            self._model_id = QWEN_MODEL_KEY
            logger.info(f"Qwen модель не найдена в /v1/models, используется fallback: {self._model_id}")
            return self._model_id

    def preload(self) -> None:
        """Предзагрузка модели. Non-fatal: при ошибке/таймауте логируем и продолжаем."""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

        PRELOAD_TIMEOUT = 60
        start = time.time()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self._discover_model)
                future.result(timeout=PRELOAD_TIMEOUT)
            elapsed = time.time() - start
            logger.info(f"Qwen модель предзагружена: {self._model_id} ({elapsed:.1f}с)")
        except FuturesTimeoutError:
            elapsed = time.time() - start
            logger.warning(f"Qwen preload timeout ({elapsed:.1f}с), продолжаем без preload")
        except Exception as e:
            elapsed = time.time() - start
            logger.warning(f"Qwen preload не удался ({elapsed:.1f}с, non-fatal): {e}")

    def _load_model_with_retry(self, model_key: str, load_config: dict):
        """POST /api/v1/models/load с retry при unrecognized_keys."""
        payload = {"model": model_key, "echo_load_config": True, **load_config}
        logger.info(f"Preload: POST /api/v1/models/load {model_key} (context_length={load_config.get('context_length')})...")
        resp = self._preload_session.post(
            f"{self.base_url}/api/v1/models/load", json=payload, timeout=120,
        )
        if resp.status_code == 400:
            try:
                err = resp.json().get("error", {})
                if err.get("code") == "unrecognized_keys":
                    msg = err.get("message", "")
                    bad_keys = [k.strip().strip("'\"") for k in msg.split(":")[-1].split(",")]
                    for k in bad_keys:
                        load_config.pop(k, None)
                    logger.warning(f"Preload: LM Studio не поддерживает ключи {bad_keys}, retry без них")
                    payload = {"model": model_key, "echo_load_config": True, **load_config}
                    resp = self._preload_session.post(
                        f"{self.base_url}/api/v1/models/load", json=payload, timeout=120,
                    )
            except Exception:
                pass
        return resp

    def _ensure_model_loaded(self) -> None:
        required_ctx = QWEN_LOAD_CONFIG["context_length"]
        try:
            logger.info(f"Preload: GET /api/v1/models (timeout=10s)...")
            resp = self._preload_session.get(f"{self.base_url}/api/v1/models", timeout=10)
            if resp.status_code != 200:
                logger.warning(f"Preload: GET /api/v1/models → {resp.status_code}, пропускаем")
                return

            models = resp.json().get("models", [])
            target_model = None

            model_key_lower = QWEN_MODEL_KEY.lower()
            for m in models:
                if model_key_lower in m.get("key", "").lower():
                    target_model = m
                    loaded = m.get("loaded_instances", [])
                    need_reload, reason = needs_model_reload(loaded, required_ctx)

                    if not need_reload:
                        ctx_list = [inst.get("context_length", "?") for inst in loaded]
                        logger.info(
                            f"Preload: модель {m['key']} уже загружена ({reason}), "
                            f"instances={len(loaded)}, context_lengths={ctx_list}"
                        )
                        return

                    logger.info(f"Preload: модель {m['key']}: {reason}, выполняем reload")
                    for inst in loaded:
                        try:
                            self._preload_session.post(
                                f"{self.base_url}/api/v1/models/unload",
                                json={"instance_id": inst["id"]}, timeout=30,
                            )
                            logger.debug(f"Выгружен инстанс: {inst['id']}")
                        except Exception as e:
                            logger.warning(f"Ошибка выгрузки {inst.get('id')}: {e}")
                    break

            actual_key = target_model.get("key", QWEN_MODEL_KEY) if target_model else QWEN_MODEL_KEY
            if target_model is None:
                logger.warning("Preload: Qwen модель не найдена в LM Studio, пробуем загрузить по ключу")

            load_config = {**QWEN_LOAD_CONFIG}
            load_resp = self._load_model_with_retry(actual_key, load_config)

            if load_resp and load_resp.status_code == 200:
                load_data = load_resp.json()
                lc = load_data.get("load_config", {})
                logger.info(
                    f"Preload: модель загружена: context_length={lc.get('context_length', '?')}, "
                    f"время={load_data.get('load_time_seconds', '?')}с"
                )
            elif load_resp:
                logger.warning(f"Preload: ошибка загрузки: {load_resp.status_code} - {load_resp.text[:300]}")

        except Exception as e:
            logger.warning(f"Preload: native API недоступен: {e}")

    def unload_model(self) -> None:
        if not self._model_id:
            return
        try:
            resp = self.session.get(f"{self.base_url}/api/v1/models", timeout=10)
            if resp.status_code != 200:
                return

            model_key_lower = QWEN_MODEL_KEY.lower()
            for m in resp.json().get("models", []):
                if model_key_lower in m.get("key", "").lower():
                    for inst in m.get("loaded_instances", []):
                        self.session.post(
                            f"{self.base_url}/api/v1/models/unload",
                            json={"instance_id": inst["id"]}, timeout=30,
                        )
                        logger.info(f"Qwen модель выгружена: {inst['id']}")
                    break
        except Exception as e:
            logger.warning(f"Ошибка выгрузки модели Qwen: {e}")

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
            return make_error("Qwen требует изображение")

        try:
            model_id = self._discover_model()
            img_b64 = image_to_base64(image)
            payload = build_payload(
                model_id, self.mode, img_b64,
                system_prompt=prompt.get("system") if prompt else None,
                user_prompt=prompt.get("user") if prompt else None,
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

                try:
                    response = self.session.post(
                        f"{self.base_url}/v1/chat/completions",
                        headers={"Content-Type": "application/json", "ngrok-skip-browser-warning": "true"},
                        json=payload, timeout=300,
                    )
                except requests.exceptions.ConnectionError as e:
                    last_error = f"ConnectionError: {e}"
                    logger.warning(f"Qwen connection error (attempt {attempt}): {e}")
                    if attempt < self._MAX_APP_RETRIES:
                        continue
                    return make_error(f"Qwen: {last_error} после {self._MAX_APP_RETRIES} попыток")
                except requests.exceptions.Timeout:
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

            text = parse_response(response.json(), self.mode, "Qwen")
            if not is_error(text):
                logger.debug(f"Qwen OCR ({self.mode}): распознано {len(text)} символов")
            return text

        except Exception as e:
            logger.error(f"Ошибка Qwen OCR: {e}", exc_info=True)
            return make_error(f"Qwen OCR: {e}")
