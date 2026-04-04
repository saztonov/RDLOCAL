"""LM Studio v1 REST API клиент для OCR через reverse proxy.

Единственная точка входа для OCR-запросов к LM Studio.
Подключение исключительно через HTTPS reverse proxy (LMSTUDIO_BASE_URL).
Токен авторизации берётся из env (LMSTUDIO_API_KEY).
"""
import base64
import io
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import httpx

from rd_core.ocr.http_utils import create_http_client, get_lmstudio_api_key

logger = logging.getLogger(__name__)

# Коды, при которых нужен retry с exponential backoff
TRANSIENT_CODES = {429, 500, 502, 503, 504}
# Коды, при которых retry бесполезен
AUTH_ERROR_CODES = {401, 403}

# Дефолтный system prompt для технической документации
DEFAULT_OCR_SYSTEM_PROMPT = (
    "Ты — система OCR для технической документации. Правила:\n"
    "- Извлекай текст максимально дословно\n"
    "- Не исправляй термины без необходимости\n"
    "- Сохраняй числа, марки, индексы, обозначения, единицы измерения\n"
    "- Если часть текста нечитабельна — помечай [НЕЧИТАЕМО]\n"
    "- Если видишь таблицу — сохраняй структуру строк и столбцов в текстовом виде\n"
    "- Если видишь схему/чертёж — отдельно выделяй технические обозначения\n"
    "- Не выдумывай отсутствующие данные"
)

DEFAULT_OCR_USER_PROMPT = "Распознай весь текст на этом изображении. Сохрани структуру и форматирование."

# Жёсткая инструкция для повторного запроса при невалидном JSON
STRICT_JSON_PROMPT = "Верни только валидный JSON без комментариев и без markdown."


class LMStudioAuthError(Exception):
    """Ошибка авторизации (401/403)."""


class LMStudioError(Exception):
    """Общая ошибка LM Studio API."""


@dataclass
class OcrResult:
    """Результат OCR-запроса."""
    provider: str = "lmstudio"
    base_url: str = ""
    model: str = ""
    file_name: str = ""
    page: Optional[int] = None
    mime_type: str = ""
    raw_text: str = ""
    parsed: Optional[dict] = None
    warnings: list[str] = field(default_factory=list)
    duration_ms: float = 0


class LMStudioClient:
    """Клиент LM Studio v1 REST API через reverse proxy.

    Конфигурация через env:
        LMSTUDIO_BASE_URL    — URL reverse proxy (default: http://localhost:1234)
        LMSTUDIO_API_KEY     — Bearer token
        LMSTUDIO_TIMEOUT_MS  — таймаут OCR-запросов в мс (default: 300000)
        LMSTUDIO_MAX_RETRIES — макс. количество retry (default: 3)
        LMSTUDIO_VISION_MODEL — ID vision-модели (опционально, автовыбор)
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_ms: Optional[int] = None,
        max_retries: Optional[int] = None,
    ):
        self.base_url = (
            base_url
            or os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234")
        ).rstrip("/")
        self._api_key = api_key or get_lmstudio_api_key()
        self._timeout_s = (
            timeout_ms or int(os.getenv("LMSTUDIO_TIMEOUT_MS", "300000"))
        ) / 1000.0
        self._max_retries = (
            max_retries
            if max_retries is not None
            else int(os.getenv("LMSTUDIO_MAX_RETRIES", "3"))
        )
        self._client = create_http_client(
            api_key=self._api_key,
            timeout=self._timeout_s,
        )
        self._vision_model: Optional[str] = None
        logger.info(
            f"LMStudioClient: base_url={self.base_url}, "
            f"timeout={self._timeout_s}s, max_retries={self._max_retries}"
        )

    # ── Health & Models ──────────────────────────────────────────────

    def health_check(self) -> bool:
        """Проверка доступности LM Studio через GET /v1/models."""
        try:
            resp = self._client.get(f"{self.base_url}/v1/models", timeout=15)
            ok = resp.status_code == 200
            if ok:
                logger.debug("LMStudio health check: OK")
            else:
                logger.warning(f"LMStudio health check: status={resp.status_code}")
            return ok
        except Exception as e:
            logger.warning(f"LMStudio health check failed: {e}")
            return False

    def list_models(self) -> list[dict]:
        """GET /v1/models — список загруженных моделей.

        Returns:
            Список моделей из response.data[]
        """
        resp = self._client.get(f"{self.base_url}/v1/models", timeout=15)
        resp.raise_for_status()
        return resp.json().get("data", [])

    def select_vision_model(self, preferred: Optional[str] = None) -> Optional[str]:
        """Выбор vision-модели.

        Приоритет:
            1. preferred аргумент
            2. LMSTUDIO_VISION_MODEL из env
            3. Автовыбор: первая модель с type=="llm" и capabilities.vision==True
            4. Первая доступная модель (fallback)

        Returns:
            ID модели или None если моделей нет
        """
        # 1. Явно указана
        explicit = preferred or os.getenv("LMSTUDIO_VISION_MODEL", "").strip()
        if explicit:
            logger.info(f"LMStudio: используется указанная модель: {explicit}")
            return explicit

        # 2. Автовыбор
        try:
            models = self.list_models()
        except Exception as e:
            logger.warning(f"LMStudio: не удалось получить список моделей: {e}")
            return None

        if not models:
            logger.warning("LMStudio: нет загруженных моделей")
            return None

        # Ищем vision-модель
        for m in models:
            m_type = m.get("type", "")
            caps = m.get("capabilities", {})
            if m_type == "llm" and caps.get("vision", False):
                model_id = m["id"]
                logger.info(f"LMStudio: автовыбор vision-модели: {model_id}")
                return model_id

        # Fallback — первая модель
        fallback = models[0]["id"]
        logger.info(f"LMStudio: vision-модель не найдена, fallback: {fallback}")
        return fallback

    # ── OCR Image ────────────────────────────────────────────────────

    def ocr_image(
        self,
        image_buffer: bytes,
        mime_type: str,
        *,
        file_name: str = "",
        system_prompt: Optional[str] = None,
        user_prompt: Optional[str] = None,
        model: Optional[str] = None,
    ) -> OcrResult:
        """OCR одного изображения через POST /v1/chat/completions.

        Args:
            image_buffer: байты изображения (PNG, JPEG, etc.)
            mime_type: MIME-тип (image/png, image/jpeg, etc.)
            file_name: имя файла для логирования
            system_prompt: system prompt (default: технич. документация)
            user_prompt: user prompt (default: распознавание текста)
            model: ID модели (default: автовыбор vision-модели)

        Returns:
            OcrResult с результатом распознавания
        """
        start = time.monotonic()

        # Выбор модели
        model_id = model or self._get_vision_model()
        if not model_id:
            return OcrResult(
                provider="lmstudio",
                base_url=self.base_url,
                file_name=file_name,
                mime_type=mime_type,
                warnings=["Нет доступной модели"],
                duration_ms=(time.monotonic() - start) * 1000,
            )

        # Base64 data URL
        b64 = base64.b64encode(image_buffer).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64}"

        # Payload (OpenAI-compatible)
        sys_prompt = system_prompt or DEFAULT_OCR_SYSTEM_PROMPT
        usr_prompt = user_prompt or DEFAULT_OCR_USER_PROMPT

        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": usr_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                    ],
                },
            ],
            "max_tokens": 12384,
            "temperature": 0.1,
        }

        # Запрос с retry
        raw_text, warnings = self._request_with_retry(
            payload, file_name=file_name, model_id=model_id
        )

        duration_ms = (time.monotonic() - start) * 1000

        # Попытка парсинга JSON
        parsed = None
        if raw_text:
            parsed = self._try_parse_json(raw_text)

        return OcrResult(
            provider="lmstudio",
            base_url=self.base_url,
            model=model_id,
            file_name=file_name,
            mime_type=mime_type,
            raw_text=raw_text,
            parsed=parsed,
            warnings=warnings,
            duration_ms=duration_ms,
        )

    # ── OCR PDF ──────────────────────────────────────────────────────

    def ocr_pdf(
        self,
        pdf_path: str,
        *,
        system_prompt: Optional[str] = None,
        user_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_parallel: int = 1,
    ) -> list[OcrResult]:
        """OCR PDF постранично.

        Args:
            pdf_path: путь к PDF файлу
            system_prompt: system prompt для OCR
            user_prompt: user prompt для OCR
            model: ID модели
            max_parallel: макс. параллелизм (1-2, default 1)

        Returns:
            Список OcrResult по страницам
        """
        import fitz
        from rd_core.pdf_utils import render_page_to_image

        max_parallel = min(max(max_parallel, 1), 2)
        file_name = os.path.basename(pdf_path)
        model_id = model or self._get_vision_model()

        doc = fitz.open(pdf_path)
        page_count = len(doc)
        logger.info(f"LMStudio OCR PDF: {file_name}, {page_count} страниц, parallel={max_parallel}")

        results: list[OcrResult] = []

        def _ocr_page(page_idx: int) -> OcrResult:
            img = render_page_to_image(doc, page_idx)
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            result = self.ocr_image(
                image_buffer=buf.getvalue(),
                mime_type="image/png",
                file_name=f"{file_name}#p{page_idx + 1}",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=model_id,
            )
            result.page = page_idx + 1
            return result

        if max_parallel <= 1:
            for i in range(page_count):
                results.append(_ocr_page(i))
        else:
            with ThreadPoolExecutor(max_workers=max_parallel) as pool:
                futures = {pool.submit(_ocr_page, i): i for i in range(page_count)}
                page_results = {}
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        page_results[idx] = future.result()
                    except Exception as e:
                        page_results[idx] = OcrResult(
                            provider="lmstudio",
                            base_url=self.base_url,
                            model=model_id or "",
                            file_name=f"{file_name}#p{idx + 1}",
                            page=idx + 1,
                            mime_type="image/png",
                            warnings=[f"Ошибка OCR страницы {idx + 1}: {e}"],
                        )
                results = [page_results[i] for i in range(page_count)]

        doc.close()
        logger.info(
            f"LMStudio OCR PDF завершён: {file_name}, "
            f"{sum(1 for r in results if r.raw_text)} из {page_count} страниц распознаны"
        )
        return results

    # ── Internal ─────────────────────────────────────────────────────

    def _get_vision_model(self) -> Optional[str]:
        """Кэшированный выбор vision-модели."""
        if not self._vision_model:
            self._vision_model = self.select_vision_model()
        return self._vision_model

    def _request_with_retry(
        self,
        payload: dict,
        *,
        file_name: str = "",
        model_id: str = "",
    ) -> tuple[str, list[str]]:
        """POST /v1/chat/completions с retry и exponential backoff.

        Returns:
            (raw_text, warnings)
        """
        endpoint = f"{self.base_url}/v1/chat/completions"
        warnings: list[str] = []
        last_error: Optional[Exception] = None

        for attempt in range(self._max_retries + 1):
            retry_label = f"[attempt {attempt + 1}/{self._max_retries + 1}]"

            try:
                start = time.monotonic()
                resp = self._client.post(
                    endpoint,
                    json=payload,
                    timeout=self._timeout_s,
                )
                duration = time.monotonic() - start

                logger.info(
                    f"LMStudio OCR {retry_label}: "
                    f"endpoint={endpoint}, model={model_id}, "
                    f"file={file_name}, status={resp.status_code}, "
                    f"duration={duration:.1f}s"
                )

                # Auth errors — не retry
                if resp.status_code in AUTH_ERROR_CODES:
                    raise LMStudioAuthError(
                        f"Ошибка авторизации: {resp.status_code} — "
                        f"проверьте LMSTUDIO_API_KEY"
                    )

                # Transient errors — retry
                if resp.status_code in TRANSIENT_CODES:
                    msg = f"Транзитная ошибка {resp.status_code}"
                    warnings.append(f"{msg} {retry_label}")
                    last_error = LMStudioError(msg)
                    if attempt < self._max_retries:
                        delay = 2 ** (attempt + 1)
                        logger.warning(f"{msg}, retry через {delay}s...")
                        time.sleep(delay)
                        continue
                    break

                resp.raise_for_status()

                # Парсим ответ
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    warnings.append("Пустой ответ от модели (нет choices)")
                    return "", warnings

                content = choices[0].get("message", {}).get("content", "")

                # Проверка finish_reason
                finish = choices[0].get("finish_reason", "")
                if finish == "length":
                    warnings.append("Ответ обрезан (finish_reason=length)")

                return content, warnings

            except LMStudioAuthError:
                raise
            except httpx.TimeoutException as e:
                msg = f"Таймаут ({self._timeout_s}s)"
                warnings.append(f"{msg} {retry_label}")
                last_error = e
                logger.warning(f"LMStudio OCR {retry_label}: {msg}")
                if attempt < self._max_retries:
                    delay = 2 ** (attempt + 1)
                    time.sleep(delay)
                    continue
            except httpx.ConnectError as e:
                msg = f"Ошибка подключения: {e}"
                warnings.append(f"{msg} {retry_label}")
                last_error = e
                logger.warning(f"LMStudio OCR {retry_label}: {msg}")
                if attempt < self._max_retries:
                    delay = 2 ** (attempt + 1)
                    time.sleep(delay)
                    continue
            except LMStudioError:
                raise
            except Exception as e:
                msg = f"Неожиданная ошибка: {e}"
                warnings.append(msg)
                last_error = e
                logger.error(f"LMStudio OCR {retry_label}: {msg}")
                break

        if last_error:
            warnings.append(f"Все попытки исчерпаны: {last_error}")
        return "", warnings

    @staticmethod
    def _try_parse_json(text: str) -> Optional[dict]:
        """Попытка парсинга JSON из ответа модели."""
        text = text.strip()
        # Убираем markdown code blocks если есть
        if text.startswith("```"):
            lines = text.split("\n")
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None

    def close(self) -> None:
        """Закрыть HTTP client."""
        if hasattr(self, "_client") and self._client:
            self._client.close()
            logger.debug("LMStudioClient: HTTP client закрыт")
