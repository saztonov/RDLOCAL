"""
Фабрика OCR бэкендов для Celery задач.

Создаёт тройку бэкендов (strip, image, stamp) на основе
настроек задачи и серверной конфигурации.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .logging_config import get_logger
from .rate_limiter import get_datalab_limiter
from .settings import settings

if TYPE_CHECKING:
    from rd_core.ocr.base import OCRBackend

logger = get_logger(__name__)


@dataclass
class JobBackends:
    """Тройка бэкендов для OCR задачи + опциональный text fallback."""

    strip: OCRBackend
    image: OCRBackend
    stamp: OCRBackend
    engine: str
    needs_lmstudio: bool
    text_fallback: Optional[OCRBackend] = field(default=None)

    def unload_all(self) -> None:
        """Выгрузить все LM Studio модели (дедупликация по identity)."""
        seen: set[int] = set()
        for backend in (self.strip, self.image, self.stamp, self.text_fallback):
            if backend is not None and hasattr(backend, "unload_model"):
                backend_id = id(backend)
                if backend_id not in seen:
                    seen.add(backend_id)
                    try:
                        backend.unload_model()
                    except Exception as e:
                        logger.warning(f"Ошибка выгрузки модели {type(backend).__name__}: {e}")


def create_job_backends(job) -> JobBackends:
    """
    Создать бэкенды для OCR задачи на основе job.engine и job.settings.

    Returns:
        JobBackends с тройкой (strip, image, stamp) и метаданными.
    """
    from rd_core.ocr import create_ocr_engine

    job_settings = job.settings
    text_model = (job_settings.text_model if job_settings else "") or ""
    table_model = (job_settings.table_model if job_settings else "") or ""
    image_model = (job_settings.image_model if job_settings else "") or ""
    stamp_model = (job_settings.stamp_model if job_settings else "") or ""

    engine = job.engine or "datalab"
    needs_lmstudio = False

    # --- Strip backend ---
    if engine == "chandra" and settings.chandra_base_url:
        strip_backend = create_ocr_engine(
            "chandra",
            base_url=settings.chandra_base_url,
            http_timeout=settings.chandra_http_timeout,
        )
        try:
            strip_backend.preload()
        except Exception as e:
            logger.warning(f"Preload chandra strip failed (non-fatal): {e}")
        needs_lmstudio = True
    elif engine == "datalab" and settings.datalab_api_key:
        datalab_limiter = get_datalab_limiter()
        strip_backend = create_ocr_engine(
            "datalab",
            api_key=settings.datalab_api_key,
            rate_limiter=datalab_limiter,
            poll_interval=settings.datalab_poll_interval,
            poll_max_attempts=settings.datalab_poll_max_attempts,
            max_retries=settings.datalab_max_retries,
            extras=settings.datalab_extras,
            quality_threshold=settings.datalab_quality_threshold,
        )
    elif settings.openrouter_api_key:
        strip_model = text_model or table_model or "qwen/qwen3-vl-30b-a3b-instruct"
        strip_backend = create_ocr_engine(
            "openrouter",
            api_key=settings.openrouter_api_key,
            model_name=strip_model,
            base_url=settings.openrouter_base_url,
        )
    else:
        strip_backend = create_ocr_engine("dummy")

    # --- Image backend ---
    if settings.openrouter_api_key:
        img_model = (
            image_model
            or text_model
            or table_model
            or "qwen/qwen3-vl-30b-a3b-instruct"
        )
        logger.info(f"IMAGE модель: {img_model}")
        image_backend = create_ocr_engine(
            "openrouter",
            api_key=settings.openrouter_api_key,
            model_name=img_model,
            base_url=settings.openrouter_base_url,
        )
    else:
        image_backend = create_ocr_engine("dummy")

    # --- Stamp backend ---
    if settings.openrouter_api_key:
        stmp_model = (
            stamp_model
            or image_model
            or text_model
            or table_model
            or "qwen/qwen3-vl-30b-a3b-instruct"
        )
        logger.info(f"STAMP модель: {stmp_model}")
        stamp_backend = create_ocr_engine(
            "openrouter",
            api_key=settings.openrouter_api_key,
            model_name=stmp_model,
            base_url=settings.openrouter_base_url,
        )
    else:
        stamp_backend = create_ocr_engine("dummy")

    # --- Text fallback backend (для suspicious_output retry) ---
    text_fallback = _create_text_fallback(engine, create_ocr_engine)

    result = JobBackends(
        strip=strip_backend,
        image=image_backend,
        stamp=stamp_backend,
        engine=engine,
        needs_lmstudio=needs_lmstudio,
        text_fallback=text_fallback,
    )

    logger.info(
        f"Бэкенды для задачи созданы: engine={engine}",
        extra={
            "event": "job_backends_created",
            "job_id": job.id,
            "engine": engine,
            "backend_type": type(strip_backend).__name__,
            "text_model": text_model or "(default)",
            "table_model": table_model or "(default)",
            "image_model": image_model or "(default)",
            "stamp_model": stamp_model or "(default)",
            "text_fallback": type(text_fallback).__name__ if text_fallback else None,
        },
    )

    return result


def _create_text_fallback(engine: str, create_ocr_engine) -> Optional[OCRBackend]:
    """Создать fallback бэкенд для TEXT блоков при suspicious output.

    Порядок: Datalab → OpenRouter → None.
    Не создаёт fallback того же типа, что и primary strip backend.
    """
    if engine == "chandra":
        if settings.datalab_api_key:
            datalab_limiter = get_datalab_limiter()
            logger.info("Text fallback: Datalab")
            return create_ocr_engine(
                "datalab",
                api_key=settings.datalab_api_key,
                rate_limiter=datalab_limiter,
                poll_interval=settings.datalab_poll_interval,
                poll_max_attempts=settings.datalab_poll_max_attempts,
                max_retries=settings.datalab_max_retries,
                extras=settings.datalab_extras,
                quality_threshold=settings.datalab_quality_threshold,
            )
        if settings.openrouter_api_key:
            logger.info("Text fallback: OpenRouter")
            return create_ocr_engine(
                "openrouter",
                api_key=settings.openrouter_api_key,
                model_name="qwen/qwen3-vl-30b-a3b-instruct",
                base_url=settings.openrouter_base_url,
            )
    elif engine == "datalab":
        if settings.openrouter_api_key:
            logger.info("Text fallback: OpenRouter")
            return create_ocr_engine(
                "openrouter",
                api_key=settings.openrouter_api_key,
                model_name="qwen/qwen3-vl-30b-a3b-instruct",
                base_url=settings.openrouter_base_url,
            )

    return None
