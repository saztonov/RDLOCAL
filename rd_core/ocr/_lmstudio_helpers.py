"""Общая инфраструктурная логика для LM Studio OCR backends (Chandra, Qwen).

Содержит mixin для interruptible sleep, budget exhaustion, model discovery,
preload, load/unload — всё, что идентично между ChandraBackend и QwenBackend.
"""
import logging
import threading
import time
from typing import Tuple

logger = logging.getLogger(__name__)


def needs_model_reload(loaded_instances: list, required_context: int) -> Tuple[bool, str]:
    """Проверяет нужна ли перезагрузка модели из-за несовпадения context_length."""
    if not loaded_instances:
        return True, "модель не загружена"
    for inst in loaded_instances:
        inst_id = inst.get("id", "unknown")
        ctx = inst.get("context_length")
        if ctx is None:
            return True, f"instance {inst_id}: context_length недоступен в API"
        if ctx != required_context:
            return True, f"instance {inst_id}: context_length={ctx}, требуется {required_context}"
    return False, f"context_length={required_context} OK"


class LMStudioLifecycleMixin:
    """Mixin для общей LM Studio lifecycle-логики.

    Ожидает от класса-наследника атрибуты:
        base_url: str
        session: httpx client
        _preload_session: httpx client
        _model_id: Optional[str]
        _model_lock: threading.Lock
        _deadline: Optional[float]
        _cancel_event: Optional[threading.Event]
    """

    # Переопределяются в наследниках
    _BACKEND_NAME: str = "LMStudio"
    _MODEL_KEY: str = ""
    _LOAD_CONFIG: dict = {}
    _PRELOAD_TIMEOUT: int = 60

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
                    model_key_lower = self._MODEL_KEY.lower()
                    for m in resp.json().get("data", []):
                        mid = m.get("id", "").lower()
                        if model_key_lower in mid or mid in model_key_lower:
                            self._model_id = m["id"]
                            logger.info(f"{self._BACKEND_NAME} модель найдена: {self._model_id}")
                            return self._model_id
            except Exception as e:
                logger.warning(f"Ошибка определения модели {self._BACKEND_NAME}: {e}")

            self._model_id = self._MODEL_KEY
            logger.info(f"{self._BACKEND_NAME} модель не найдена, используется fallback: {self._model_id}")
            return self._model_id

    def preload(self) -> None:
        """Предзагрузка модели. Non-fatal: при ошибке/таймауте логируем и продолжаем."""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

        start = time.time()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self._discover_model)
                future.result(timeout=self._PRELOAD_TIMEOUT)
            elapsed = time.time() - start
            logger.info(f"{self._BACKEND_NAME} модель предзагружена: {self._model_id} ({elapsed:.1f}с)")
        except FuturesTimeoutError:
            elapsed = time.time() - start
            logger.warning(f"{self._BACKEND_NAME} preload timeout ({elapsed:.1f}с), продолжаем без preload")
        except Exception as e:
            elapsed = time.time() - start
            logger.warning(f"{self._BACKEND_NAME} preload не удался ({elapsed:.1f}с, non-fatal): {e}")

    def _try_discover_and_load(self, failed_resp, load_config: dict) -> bool:
        """При model_not_found — найти модель через /v1/models и загрузить."""
        try:
            err = failed_resp.json().get("error", {})
            if err.get("type") != "model_not_found":
                return False
        except Exception:
            return False

        logger.info("Preload: model_not_found, пробуем auto-discovery через /v1/models...")
        try:
            resp = self._preload_session.get(f"{self.base_url}/v1/models", timeout=10)
            if resp.status_code != 200:
                return False

            model_key_lower = self._MODEL_KEY.lower()
            for m in resp.json().get("data", []):
                mid = m.get("id", "").lower()
                if model_key_lower in mid or mid in model_key_lower:
                    discovered_id = m["id"]
                    logger.info(f"Preload: найдена модель через discovery: {discovered_id}")
                    retry_config = {**load_config}
                    retry_resp = self._load_model_with_retry(discovered_id, retry_config)
                    if retry_resp and retry_resp.status_code == 200:
                        load_data = retry_resp.json()
                        lc = load_data.get("load_config", {})
                        logger.info(
                            f"Preload: модель загружена через discovery: "
                            f"context_length={lc.get('context_length', '?')}, "
                            f"время={load_data.get('load_time_seconds', '?')}с"
                        )
                        return True
                    else:
                        logger.warning(f"Preload: повторная загрузка {discovered_id} не удалась")
                        return False

            logger.warning("Preload: модель не найдена через /v1/models discovery")
        except Exception as e:
            logger.warning(f"Preload: auto-discovery ошибка: {e}")
        return False

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
        required_ctx = self._LOAD_CONFIG["context_length"]
        try:
            logger.info("Preload: GET /api/v1/models (timeout=10s)...")
            resp = self._preload_session.get(f"{self.base_url}/api/v1/models", timeout=10)
            if resp.status_code != 200:
                logger.warning(f"Preload: GET /api/v1/models → {resp.status_code}, пропускаем")
                return

            models = resp.json().get("models", [])
            actual_key = self._MODEL_KEY

            model_key_lower = self._MODEL_KEY.lower()
            for m in models:
                if model_key_lower in m.get("key", "").lower():
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
                    actual_key = m.get("key", self._MODEL_KEY)
                    break

            load_config = {**self._LOAD_CONFIG}
            load_resp = self._load_model_with_retry(actual_key, load_config)

            if load_resp and load_resp.status_code == 200:
                load_data = load_resp.json()
                lc = load_data.get("load_config", {})
                logger.info(
                    f"Preload: модель загружена: context_length={lc.get('context_length', '?')}, "
                    f"время={load_data.get('load_time_seconds', '?')}с"
                )
            elif load_resp:
                discovered = self._try_discover_and_load(load_resp, load_config)
                if not discovered:
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

            model_key_lower = self._MODEL_KEY.lower()
            for m in resp.json().get("models", []):
                if model_key_lower in m.get("key", "").lower():
                    for inst in m.get("loaded_instances", []):
                        self.session.post(
                            f"{self.base_url}/api/v1/models/unload",
                            json={"instance_id": inst["id"]}, timeout=30,
                        )
                        logger.info(f"{self._BACKEND_NAME} модель выгружена: {inst['id']}")
                    break
        except Exception as e:
            logger.warning(f"Ошибка выгрузки модели {self._BACKEND_NAME}: {e}")
