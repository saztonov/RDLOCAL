"""
Фабрика OCR бэкендов для Celery задач.

Создаёт тройку бэкендов (strip, image, stamp) на основе
настроек задачи и серверной конфигурации.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .logging_config import get_logger
from .rate_limiter import get_datalab_limiter
from .settings import settings

if TYPE_CHECKING:
    from rd_core.ocr.base import OCRBackend

logger = get_logger(__name__)


@dataclass
class JobBackends:
    """Тройка бэкендов для OCR задачи."""

    strip: OCRBackend
    image: OCRBackend
    stamp: OCRBackend
    engine: str
    needs_lmstudio: bool


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

    engine = job.engine or "openrouter"
    needs_lmstudio = False

    # --- Strip backend ---
    if engine == "chandra" and settings.chandra_base_url:
        strip_backend = create_ocr_engine(
            "chandra",
            base_url=settings.chandra_base_url,
        )
        strip_backend.preload()
        needs_lmstudio = True
    elif engine == "qwen" and settings.qwen_base_url:
        strip_backend = create_ocr_engine(
            "qwen",
            base_url=settings.qwen_base_url,
            mode="text",
        )
        strip_backend.preload()
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
    if engine == "qwen" and settings.qwen_base_url:
        logger.info("STAMP модель: Qwen (LM Studio, mode=stamp)")
        stamp_backend = create_ocr_engine(
            "qwen",
            base_url=settings.qwen_base_url,
            mode="stamp",
        )
    elif settings.openrouter_api_key:
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

    result = JobBackends(
        strip=strip_backend,
        image=image_backend,
        stamp=stamp_backend,
        engine=engine,
        needs_lmstudio=needs_lmstudio,
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
        },
    )

    return result
