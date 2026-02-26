"""Qwen OCR Backend (LM Studio / OpenAI-compatible API)

Два режима работы:
  mode="text"  — распознавание TEXT/TABLE блоков строительной документации
  mode="stamp" — распознавание штампов (основных надписей)
"""
import logging
import os
import threading
from typing import Optional

import requests
from PIL import Image

from rd_core.ocr.chandra import (
    ALLOWED_ATTRIBUTES,
    ALLOWED_TAGS,
    needs_model_reload,
)
from rd_core.ocr.http_utils import create_retry_session
from rd_core.ocr.utils import image_to_base64

logger = logging.getLogger(__name__)

# ── Модель и конфиг загрузки ────────────────────────────────────────
QWEN_MODEL_KEY = os.getenv("QWEN_MODEL_KEY", "qwen/qwen3.5-35b-a3b/")
QWEN_LOAD_CONFIG = {
    "context_length": 32768,
    "flash_attention": True,
    "eval_batch_size": 512,
}

# ── Промпты: TEXT / TABLE ───────────────────────────────────────────
QWEN_TEXT_SYSTEM = (
    "Ты — специализированная OCR-система для распознавания российской "
    "строительной документации: ГОСТ, СНиП, СП, ТУ, рабочие чертежи, стадия П. "
    "Твоя задача — максимально точно распознать содержимое переданного блока. "
    "Сохраняй все размеры, единицы измерения, номера ссылок и структуру таблиц "
    "с абсолютной точностью. Выводи результат в чистом HTML."
)

QWEN_TEXT_PROMPT = (
    "Внимательно проанализируй структуру переданного блока "
    "из строительного чертежа или спецификации.\n\n"
    "Это фрагмент технической документации (рабочая документация / стадия П). "
    "Блок может содержать:\n"
    "— текстовые параграфы с техническими требованиями\n"
    "— таблицы спецификаций с размерами, материалами, количествами\n"
    "— примечания и ссылки на нормативные документы (ГОСТ, СНиП, СП)\n"
    "— математические формулы, индексы, степени\n\n"
    "Максимально точно распознай весь текст, сохраняя оригинальную структуру.\n\n"
    "Правила вывода HTML:\n"
    f"* Теги: [{ALLOWED_TAGS}], атрибуты: [{ALLOWED_ATTRIBUTES}]\n"
    "* Таблицы: colspan/rowspan для точной структуры\n"
    "* Математика: <math>...</math> (KaTeX-совместимый LaTeX)\n"
    "* Текст: <p>...</p>, <br> только при необходимости\n"
    "* Порядок чтения — корректный и естественный\n"
    "* Не добавляй ничего от себя — только то, что видишь"
)

# ── Промпты: STAMP ─────────────────────────────────────────────────
QWEN_STAMP_SYSTEM = (
    "Ты — специалист по чтению штампов (основных надписей) из российской "
    "строительной документации. Ты работаешь с рабочей документацией и стадией П. "
    "Штамп содержит метаинформацию: организация, проект, стадия, лист, подписи. "
    "Извлекай ВСЮ информацию с максимальной точностью."
)

QWEN_STAMP_PROMPT = (
    "Это штамп (основная надпись) из строительного чертежа.\n\n"
    "Внимательно проанализируй структуру штампа и извлеки ВСЮ информацию:\n"
    "1. Организация — название проектной организации\n"
    "2. Наименование проекта — полное название объекта\n"
    "3. Шифр проекта — код документации\n"
    "4. Наименование документа — название листа/раздела\n"
    "5. Стадия — П (проектная) или Р (рабочая)\n"
    "6. Номер листа / Всего листов\n"
    "7. Масштаб, Формат\n"
    "8. Подписи — ФИО, должности, даты\n"
    "9. Изменения — номер, подпись, дата\n\n"
    "Выводи как HTML-таблицу, точно воспроизводящую структуру штампа.\n"
    f"Теги: [{ALLOWED_TAGS}], атрибуты: [{ALLOWED_ATTRIBUTES}]\n"
    "Используй colspan/rowspan для ячеек штампа.\n"
    "Не добавляй ничего от себя — только то, что видишь."
)


class QwenBackend:
    """OCR через Qwen модель (LM Studio, OpenAI-compatible API)

    Args:
        base_url: URL LM Studio (по умолчанию QWEN_BASE_URL или CHANDRA_BASE_URL)
        mode: "text" — для TEXT/TABLE блоков, "stamp" — для штампов
    """

    def __init__(self, base_url: Optional[str] = None, mode: str = "text"):
        self.mode = mode
        self.base_url = (
            base_url
            or os.getenv("QWEN_BASE_URL")
            or os.getenv("CHANDRA_BASE_URL", "")
        )
        self._model_id: Optional[str] = None
        self._model_lock = threading.Lock()

        # HTTP Basic Auth для ngrok-туннеля (общий с Chandra)
        auth_user = os.getenv("NGROK_AUTH_USER")
        auth_pass = os.getenv("NGROK_AUTH_PASS")
        self._auth = (auth_user, auth_pass) if auth_user and auth_pass else None

        self.session = create_retry_session(auth=self._auth)

        logger.info(
            f"QwenBackend инициализирован (base_url: {self.base_url}, mode: {self.mode})"
        )

    # ── Промпты по режиму ──────────────────────────────────────────
    def _get_prompts(self) -> tuple:
        """Возвращает (system_prompt, user_prompt) по текущему mode."""
        if self.mode == "stamp":
            return QWEN_STAMP_SYSTEM, QWEN_STAMP_PROMPT
        return QWEN_TEXT_SYSTEM, QWEN_TEXT_PROMPT

    # ── LM Studio: обнаружение и управление моделью ────────────────
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
                        if "qwen" in m.get("id", "").lower():
                            self._model_id = m["id"]
                            logger.info(f"Qwen модель найдена: {self._model_id}")
                            return self._model_id
            except Exception as e:
                logger.warning(f"Ошибка определения модели Qwen: {e}")

            self._model_id = QWEN_MODEL_KEY
            logger.info(
                f"Qwen модель не найдена в /v1/models, используется fallback: {self._model_id}"
            )
            return self._model_id

    def preload(self) -> None:
        """Предзагрузка модели (вызвать ДО параллельных запросов)."""
        self._discover_model()
        logger.info(f"Qwen модель предзагружена: {self._model_id}")

    def _ensure_model_loaded(self) -> None:
        """
        Проверяет загружена ли модель через LM Studio native API.
        Если нет или context_length не совпадает — выгружает и загружает.
        """
        required_ctx = QWEN_LOAD_CONFIG["context_length"]
        try:
            resp = self.session.get(
                f"{self.base_url}/api/v1/models",
                timeout=10,
            )
            if resp.status_code != 200:
                logger.debug("LM Studio native API недоступен, пропускаем preload")
                return

            models = resp.json().get("models", [])

            target_model = None
            for m in models:
                if "qwen" in m.get("key", "").lower():
                    target_model = m
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

            if target_model is None:
                logger.warning("Qwen модель не найдена в LM Studio, пробуем загрузить по ключу")

            actual_key = (
                target_model.get("key", QWEN_MODEL_KEY)
                if target_model
                else QWEN_MODEL_KEY
            )
            logger.info(
                f"Загружаем модель {actual_key} (context_length={required_ctx})"
            )
            load_resp = self.session.post(
                f"{self.base_url}/api/v1/models/load",
                json={
                    "model": actual_key,
                    "echo_load_config": True,
                    **QWEN_LOAD_CONFIG,
                },
                timeout=120,
            )

            if load_resp.status_code == 200:
                load_data = load_resp.json()
                actual_ctx = (
                    load_data.get("load_config", {}).get("context_length", "?")
                )
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
                if "qwen" in m.get("key", "").lower():
                    for inst in m.get("loaded_instances", []):
                        self.session.post(
                            f"{self.base_url}/api/v1/models/unload",
                            json={"instance_id": inst["id"]},
                            timeout=30,
                        )
                        logger.info(f"Qwen модель выгружена: {inst['id']}")
                    break
        except Exception as e:
            logger.debug(f"Ошибка выгрузки модели Qwen: {e}")

    def supports_pdf_input(self) -> bool:
        """Qwen не поддерживает прямой ввод PDF"""
        return False

    # ── OCR распознавание ──────────────────────────────────────────
    def recognize(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        """Распознать текст через Qwen (LM Studio API)"""
        if image is None:
            return "[Ошибка: Qwen требует изображение]"

        try:
            model_id = self._discover_model()
            img_b64 = image_to_base64(image)

            system_prompt, user_prompt = self._get_prompts()

            # Если передан prompt dict — используем system из него (контекст задачи)
            if prompt and isinstance(prompt, dict):
                system_prompt = prompt.get("system", "") or system_prompt

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
                    f"Qwen API error: {response.status_code} - {error_detail}"
                )
                return f"[Ошибка Qwen API: {response.status_code}]"

            result = response.json()

            if "choices" not in result or not result["choices"]:
                err_msg = result.get("error", result)
                logger.error(f"Qwen: 'choices' missing: {err_msg}")
                return f"[Ошибка Qwen: некорректный ответ ({err_msg})]"

            text = result["choices"][0]["message"]["content"].strip()
            if not text:
                logger.warning("Qwen OCR: получен пустой ответ от модели")
                return "[Ошибка Qwen: пустой ответ модели]"
            logger.debug(f"Qwen OCR ({self.mode}): распознано {len(text)} символов")
            return text

        except requests.exceptions.Timeout:
            logger.error("Qwen OCR: превышен таймаут")
            return "[Ошибка: превышен таймаут запроса к Qwen]"
        except Exception as e:
            logger.error(f"Ошибка Qwen OCR: {e}", exc_info=True)
            return f"[Ошибка Qwen OCR: {e}]"
