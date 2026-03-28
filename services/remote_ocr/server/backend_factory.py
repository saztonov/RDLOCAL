"""
Фабрика OCR бэкендов для задач.

Создаёт тройку бэкендов (text, image, stamp) — все через LM Studio:
- text (TEXT) → ChandraBackend (chandra-ocr-2)
- image/stamp → QwenBackend (qwen3.5-27b)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

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


def create_job_backends(job) -> JobBackends:
    """
    Создать бэкенды для OCR задачи — все через LM Studio.

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
    )
    try:
        text_backend.preload()
    except Exception as e:
        logger.warning(f"Preload chandra text failed (non-fatal): {e}")

    # --- Image/Stamp backend → QwenBackend ---
    qwen_url = settings.qwen_base_url or settings.chandra_base_url
    image_backend = create_ocr_engine(
        "qwen",
        base_url=qwen_url,
        http_timeout=settings.qwen_http_timeout,
    )
    # Stamp использует тот же QwenBackend (разные промпты передаются в recognize)
    stamp_backend = image_backend

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
            "text_backend": "ChandraBackend",
            "image_backend": "QwenBackend",
            "stamp_backend": "QwenBackend (shared)",
            "qwen_url": qwen_url,
        },
    )

    return result
