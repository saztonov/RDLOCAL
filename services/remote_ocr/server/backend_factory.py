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
    return {
        "model_key": settings.chandra_model_key,
        "context_length": settings.chandra_context_length,
        "flash_attention": settings.chandra_flash_attention,
        "eval_batch_size": settings.chandra_eval_batch_size,
        "offload_kv_cache": settings.chandra_offload_kv_cache,
        "max_image_size": settings.chandra_max_image_size,
        "preload_timeout": settings.chandra_preload_timeout,
        "max_retries": settings.chandra_max_retries,
        "retry_delays": settings.chandra_retry_delays,
        "system_prompt": settings.chandra_system_prompt,
        "user_prompt": settings.chandra_user_prompt,
        "max_tokens": settings.chandra_max_tokens,
        "temperature": settings.chandra_temperature,
        "top_p": settings.chandra_top_p,
        "top_k": settings.chandra_top_k,
        "repetition_penalty": settings.chandra_repetition_penalty,
        "min_p": settings.chandra_min_p,
        "length_retry_attempts": settings.chandra_length_retry_attempts,
        "length_retry_max_tokens": settings.chandra_length_retry_max_tokens,
        "response_format": {"type": "json_object"},
    }


def _build_qwen_config() -> dict:
    """Собрать model_config для QwenBackend из settings (config.yaml)."""
    return {
        "model_key": settings.qwen_model_key,
        "context_length": settings.qwen_context_length,
        "flash_attention": settings.qwen_flash_attention,
        "eval_batch_size": settings.qwen_eval_batch_size,
        "offload_kv_cache": settings.qwen_offload_kv_cache,
        "max_image_size": settings.qwen_max_image_size,
        "preload_timeout": settings.qwen_preload_timeout,
        "max_retries": settings.qwen_max_retries,
        "retry_delays": settings.qwen_retry_delays,
        "default_system_prompt": settings.qwen_default_system_prompt,
        "default_user_prompt": settings.qwen_default_user_prompt,
        "max_tokens": settings.qwen_max_tokens,
        "temperature": settings.qwen_temperature,
        "top_p": settings.qwen_top_p,
        "top_k": settings.qwen_top_k,
        "repetition_penalty": settings.qwen_repetition_penalty,
        "min_p": settings.qwen_min_p,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "image_ocr_output",
                "schema": {
                    "type": "object",
                    "properties": {
                        "fragment_type": {"type": ["string", "null"]},
                        "location": {"type": ["object", "null"]},
                        "content_summary": {"type": ["string", "null"]},
                        "detailed_description": {"type": ["string", "null"]},
                        "verification_recommendations": {"type": ["string", "null"]},
                        "key_entities": {"type": ["array", "null"]},
                    },
                    "required": ["fragment_type", "content_summary", "detailed_description"],
                },
            },
        },
    }


def _build_stamp_config() -> dict:
    """Собрать model_config для Stamp QwenBackend из settings (config.yaml)."""
    return {
        "model_key": settings.stamp_model_key,
        "context_length": settings.stamp_context_length,
        "flash_attention": settings.stamp_flash_attention,
        "eval_batch_size": settings.stamp_eval_batch_size,
        "offload_kv_cache": settings.stamp_offload_kv_cache,
        "max_image_size": settings.stamp_max_image_size,
        "preload_timeout": settings.stamp_preload_timeout,
        "max_retries": settings.stamp_max_retries,
        "retry_delays": settings.stamp_retry_delays,
        "default_system_prompt": settings.stamp_system_prompt,
        "default_user_prompt": settings.stamp_user_prompt,
        "max_tokens": settings.stamp_max_tokens,
        "temperature": settings.stamp_temperature,
        "top_p": settings.stamp_top_p,
        "top_k": settings.stamp_top_k,
        "repetition_penalty": settings.stamp_repetition_penalty,
        "min_p": settings.stamp_min_p,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "stamp_output",
                "schema": {
                    "type": "object",
                    "properties": {
                        "document_code": {"type": ["string", "null"]},
                        "project_name": {"type": ["string", "null"]},
                        "sheet_name": {"type": ["string", "null"]},
                        "stage": {"type": ["string", "null"]},
                        "sheet_number": {"type": ["string", "null"]},
                        "total_sheets": {"type": ["string", "null"]},
                        "organization": {"type": ["string", "null"]},
                        "signatures": {"type": "array"},
                        "revisions": {"type": "array"},
                    },
                    "required": ["document_code", "project_name", "sheet_name"],
                },
            },
        },
    }


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
