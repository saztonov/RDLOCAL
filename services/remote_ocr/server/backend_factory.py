"""
Фабрика OCR бэкендов для задач.

Создаёт тройку бэкендов (text, image, stamp) — все через LM Studio:
- text (TEXT) → ChandraBackend (chandra-ocr-2)
- image (IMAGE) → QwenBackend (qwen3.5-27b)
- stamp (STAMP) → QwenBackend (qwen3.5-9b)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from rd_core.pipeline.config_builders import (
    build_chandra_config,
    build_qwen_config,
    build_stamp_config,
)

from .logging_config import get_logger
from .settings import settings

if TYPE_CHECKING:
    from rd_core.ocr.base import OCRBackend

logger = get_logger(__name__)

# Допустимые значения engine
_VALID_ENGINES = {"lmstudio", "chandra"}


def _normalize_engine(engine: str) -> str:
    """Нормализовать engine: chandra → lmstudio (legacy alias)."""
    if engine in _VALID_ENGINES:
        return "lmstudio"
    raise ValueError(
        f"Unsupported engine: '{engine}'. "
        f"Only 'lmstudio' is supported (legacy alias: 'chandra')."
    )


@dataclass
class JobBackends:
    """Тройка бэкендов для OCR задачи."""

    text: OCRBackend
    image: OCRBackend
    stamp: OCRBackend
    engine: str
    needs_lmstudio: bool
    text_fallback: Optional[OCRBackend] = field(default=None)

    def unload_all(self) -> None:
        """Выгрузить все LM Studio модели (дедупликация по identity)."""
        seen: set[int] = set()
        for backend in (self.text, self.image, self.stamp):
            if backend is not None and hasattr(backend, "unload_model"):
                backend_id = id(backend)
                if backend_id not in seen:
                    seen.add(backend_id)
                    try:
                        backend.unload_model()
                    except Exception as e:
                        logger.warning(f"Ошибка выгрузки модели {type(backend).__name__}: {e}")


def _build_chandra_config() -> dict:
    """Собрать model_config для ChandraBackend из settings (config.yaml)."""
    return build_chandra_config(settings)


def _build_qwen_config() -> dict:
    """Собрать model_config для QwenBackend из settings (config.yaml)."""
    return build_qwen_config(settings)


def _build_stamp_config() -> dict:
    """Собрать model_config для Stamp QwenBackend из settings (config.yaml)."""
    return build_stamp_config(settings)


def create_job_backends(job) -> JobBackends:
    """
    Создать бэкенды для OCR задачи — все через LM Studio.

    Все настройки моделей (промпты, inference параметры, load config)
    берутся из config.yaml через settings.

    Returns:
        JobBackends с тройкой (text, image, stamp).

    Raises:
        ValueError: если engine не поддерживается.
    """
    from rd_core.ocr import create_ocr_engine

    raw_engine = job.engine or "lmstudio"
    engine = _normalize_engine(raw_engine)

    # --- Text backend (TEXT) → ChandraBackend ---
    text_backend = create_ocr_engine(
        "chandra",
        base_url=settings.chandra_base_url,
        http_timeout=settings.chandra_http_timeout,
        model_config=_build_chandra_config(),
    )
    try:
        text_backend.preload()
    except Exception as e:
        logger.warning(f"Preload chandra text failed (non-fatal): {e}")

    # --- Image backend (IMAGE) → QwenBackend (qwen3.5-27b) ---
    qwen_url = settings.qwen_base_url or settings.chandra_base_url
    image_backend = create_ocr_engine(
        "qwen",
        base_url=qwen_url,
        http_timeout=settings.qwen_http_timeout,
        model_config=_build_qwen_config(),
    )

    # --- Stamp backend (STAMP) → QwenBackend (qwen3.5-9b) ---
    stamp_backend = create_ocr_engine(
        "qwen",
        base_url=qwen_url,
        http_timeout=settings.stamp_http_timeout,
        model_config=_build_stamp_config(),
    )

    result = JobBackends(
        text=text_backend,
        image=image_backend,
        stamp=stamp_backend,
        engine=engine,
        needs_lmstudio=True,
        text_fallback=None,
    )

    logger.info(
        f"Бэкенды для задачи созданы: engine={engine} (LM Studio only)",
        extra={
            "event": "job_backends_created",
            "job_id": job.id,
            "engine": engine,
            "text_backend": f"ChandraBackend ({settings.chandra_model_key})",
            "image_backend": f"QwenBackend ({settings.qwen_model_key})",
            "stamp_backend": f"QwenBackend ({settings.stamp_model_key})",
            "qwen_url": qwen_url,
        },
    )

    return result
