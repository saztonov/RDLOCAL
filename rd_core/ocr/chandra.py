"""Chandra OCR Backend (LM Studio / OpenAI-compatible API)"""
import logging
import os
import threading
from typing import Optional

import requests
from PIL import Image

from rd_core.ocr.http_utils import create_retry_session
from rd_core.ocr.utils import image_to_base64

logger = logging.getLogger(__name__)

# Промпт из официального репо Chandra (ocr_test.py)
ALLOWED_TAGS = "p, h1, h2, h3, h4, h5, h6, table, thead, tbody, tr, th, td, ul, ol, li, br, sub, sup, div, span, img, math, mi, mo, mn, msup, msub, mfrac, msqrt, mrow, mover, munder, munderover, mtable, mtr, mtd, mtext, mspace, input"
ALLOWED_ATTRIBUTES = "colspan, rowspan, alt, type, checked, value, data-bbox, data-label"

CHANDRA_DEFAULT_PROMPT = f"""OCR this image to HTML.

Only use these tags [{ALLOWED_TAGS}], and these attributes [{ALLOWED_ATTRIBUTES}].

Guidelines:
* Inline math: Surround math with <math>...</math> tags. Math expressions should be rendered in KaTeX-compatible LaTeX. Use display for block math.
* Tables: Use colspan and rowspan attributes to match table structure.
* Formatting: Maintain consistent formatting with the image, including spacing, indentation, subscripts/superscripts, and special characters.
* Images: Include a description of any images in the alt attribute of an <img> tag. Do not fill out the src property.
* Forms: Mark checkboxes and radio buttons properly.
* Text: join lines together properly into paragraphs using <p>...</p> tags. Use <br> tags for line breaks within paragraphs, but only when absolutely necessary to maintain meaning.
* Use the simplest possible HTML structure that accurately represents the content of the block.
* Make sure the text is accurate and easy for a human to read and interpret. Reading order should be correct and natural."""

CHANDRA_DEFAULT_SYSTEM = (
    "You are a specialist OCR system for Russian construction documentation "
    "(GOST, SNiP, SP, TU). You process technical specifications, working drawings, "
    "and Stage P documents. Preserve all dimensions, units of measurement, "
    "reference numbers, and table structures with absolute accuracy. "
    "Output clean HTML."
)

# LM Studio native API: конфигурация загрузки модели
# Manual load через API не имеет TTL (модель остаётся в памяти)
CHANDRA_MODEL_KEY = os.getenv("CHANDRA_MODEL_KEY", "chandra-OCR-GGUF")
CHANDRA_LOAD_CONFIG = {
    "context_length": 32864,
    "flash_attention": True,
    "eval_batch_size": 512,
    "offload_kv_cache_to_gpu": True,
}


def needs_model_reload(loaded_instances: list, required_context: int) -> tuple:
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


class ChandraBackend:
    """OCR через Chandra модель (LM Studio, OpenAI-compatible API)"""

    DEFAULT_BASE_URL = "https://louvred-madie-gigglier.ngrok-free.dev"

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or os.getenv("CHANDRA_BASE_URL", self.DEFAULT_BASE_URL)
        self._model_id: Optional[str] = None
        self._model_lock = threading.Lock()

        # HTTP Basic Auth для ngrok-туннеля
        auth_user = os.getenv("NGROK_AUTH_USER")
        auth_pass = os.getenv("NGROK_AUTH_PASS")
        self._auth = (auth_user, auth_pass) if auth_user and auth_pass else None

        self.session = create_retry_session(auth=self._auth)

        logger.info(f"ChandraBackend инициализирован (base_url: {self.base_url})")

    def _discover_model(self) -> str:
        """Авто-определение модели через /v1/models + preload через native API"""
        if self._model_id:
            return self._model_id

        with self._model_lock:
            if self._model_id:
                return self._model_id

            self._ensure_model_loaded()

            try:
                resp = self.session.get(
                    f"{self.base_url}/v1/models",
                    timeout=30,
                )
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
        """Предзагрузка модели (вызвать ДО параллельных запросов)."""
        self._discover_model()
        logger.info(f"Chandra модель предзагружена: {self._model_id}")

    def _ensure_model_loaded(self) -> None:
        """
        Проверяет загружена ли модель через LM Studio native API.
        Если нет или context_length не совпадает — выгружает и загружает с правильным конфигом.
        При недоступности native API — тихо пропускает (fallback на JIT).
        """
        required_ctx = CHANDRA_LOAD_CONFIG["context_length"]
        try:
            resp = self.session.get(
                f"{self.base_url}/api/v1/models",
                timeout=10,
            )
            if resp.status_code != 200:
                logger.debug("LM Studio native API недоступен, пропускаем preload")
                return

            models = resp.json().get("models", [])

            for m in models:
                if "chandra" in m.get("key", "").lower():
                    loaded = m.get("loaded_instances", [])
                    needs_reload, reason = needs_model_reload(loaded, required_ctx)

                    if not needs_reload:
                        logger.debug(f"Модель {m['key']}: {reason}")
                        return

                    logger.info(f"Модель {m['key']}: {reason}, выполняем reload")
                    for inst in loaded:
                        try:
                            self.session.post(
                                f"{self.base_url}/api/v1/models/unload",
                                json={"instance_id": inst["id"]},
                                timeout=30,
                            )
                            logger.debug(f"Выгружен инстанс: {inst['id']}")
                        except Exception as e:
                            logger.warning(f"Ошибка выгрузки {inst.get('id')}: {e}")
                    break

            logger.info(
                f"Загружаем модель {CHANDRA_MODEL_KEY} "
                f"(context_length={required_ctx})"
            )
            load_resp = self.session.post(
                f"{self.base_url}/api/v1/models/load",
                json={"model": CHANDRA_MODEL_KEY, "echo_load_config": True, **CHANDRA_LOAD_CONFIG},
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
                logger.warning(
                    f"Ошибка загрузки: {load_resp.status_code} - {load_resp.text[:300]}"
                )

        except Exception as e:
            logger.debug(f"Native API preload недоступен: {e}")

    def unload_model(self) -> None:
        """Выгрузить модель из LM Studio (освобождает VRAM)."""
        if not self._model_id:
            return
        try:
            resp = self.session.get(
                f"{self.base_url}/api/v1/models",
                timeout=10,
            )
            if resp.status_code != 200:
                return

            models = resp.json().get("models", [])
            for m in models:
                if "chandra" in m.get("key", "").lower():
                    for inst in m.get("loaded_instances", []):
                        self.session.post(
                            f"{self.base_url}/api/v1/models/unload",
                            json={"instance_id": inst["id"]},
                            timeout=30,
                        )
                        logger.info(f"Модель выгружена: {inst['id']}")
                    break
        except Exception as e:
            logger.debug(f"Ошибка выгрузки модели: {e}")

    def supports_pdf_input(self) -> bool:
        """Chandra не поддерживает прямой ввод PDF"""
        return False

    def recognize(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        """Распознать текст через Chandra (LM Studio API)"""
        if image is None:
            return "[Ошибка: Chandra требует изображение]"

        try:
            model_id = self._discover_model()
            img_b64 = image_to_base64(image)

            # Chandra всегда использует свой специализированный HTML промпт
            # System prompt берём из переданного dict (контекст задачи)
            if prompt and isinstance(prompt, dict):
                system_prompt = prompt.get("system", "") or CHANDRA_DEFAULT_SYSTEM
            else:
                system_prompt = CHANDRA_DEFAULT_SYSTEM
            user_prompt = CHANDRA_DEFAULT_PROMPT

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img_b64}"
                            },
                        },
                        {
                            "type": "text",
                            "text": user_prompt,
                        },
                    ],
                }
            )

            payload = {
                "model": model_id,
                "messages": messages,
                "max_tokens": 12384,
                "temperature": 0,
                "top_p": 0.1,
            }

            response = self.session.post(
                f"{self.base_url}/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=300,
            )

            if response.status_code != 200:
                error_detail = response.text[:500] if response.text else "No details"
                logger.error(
                    f"Chandra API error: {response.status_code} - {error_detail}"
                )
                return f"[Ошибка Chandra API: {response.status_code}]"

            result = response.json()

            if "choices" not in result or not result["choices"]:
                err_msg = result.get("error", result)
                logger.error(f"Chandra: 'choices' missing: {err_msg}")
                return f"[Ошибка Chandra: некорректный ответ ({err_msg})]"

            text = result["choices"][0]["message"]["content"].strip()
            logger.debug(f"Chandra OCR: распознано {len(text)} символов")
            return text

        except requests.exceptions.Timeout:
            logger.error("Chandra OCR: превышен таймаут")
            return "[Ошибка: превышен таймаут запроса к Chandra]"
        except Exception as e:
            logger.error(f"Ошибка Chandra OCR: {e}", exc_info=True)
            return f"[Ошибка Chandra OCR: {e}]"
