from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# NOTE: settings.py загружается раньше logging_config, поэтому используем стандартный logging
logger = logging.getLogger(__name__)

# Путь к конфиг-файлу: рядом с settings.py
_CONFIG_PATH = Path(__file__).parent / "config.yaml"
# Переопределение через ENV (для Docker: монтируем конфиг в другое место)
_config_override = os.getenv("OCR_CONFIG_PATH")
if _config_override:
    _CONFIG_PATH = Path(_config_override)


def _load_yaml_config() -> dict:
    """Загрузить YAML конфигурацию"""
    if not _CONFIG_PATH.exists():
        logger.error(f"Config file not found: {_CONFIG_PATH}")
        raise FileNotFoundError(
            f"OCR server config not found: {_CONFIG_PATH}. "
            f"Create config.yaml or set OCR_CONFIG_PATH env variable."
        )
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        logger.info(f"Config loaded from {_CONFIG_PATH} ({len(data)} keys)")
        return data
    except yaml.YAMLError as e:
        logger.error(f"Invalid YAML in {_CONFIG_PATH}: {e}")
        raise


# Lazy-загрузка: конфигурация загружается при первом обращении, а не при импорте
_yaml_config: Optional[dict] = None


def _get_yaml_config() -> dict:
    """Получить YAML конфигурацию (lazy loading)."""
    global _yaml_config
    if _yaml_config is None:
        _yaml_config = _load_yaml_config()
    return _yaml_config


def _get(config: dict, key: str, env_key: str, cast_fn=None):
    """Получить настройку: ENV > YAML. Без hardcoded defaults."""
    env_val = os.getenv(env_key)
    if env_val is not None:
        value = env_val
    elif key in config:
        value = config[key]
    else:
        raise KeyError(
            f"Setting '{key}' not found in config.yaml and env '{env_key}' not set"
        )

    if cast_fn:
        try:
            return cast_fn(value)
        except (ValueError, TypeError):
            raise ValueError(
                f"Cannot cast setting '{key}'={value!r} with {cast_fn.__name__}"
            )
    return value


def _cfg(yaml_key: str, env_key: str, cast_fn=None):
    """Shortcut: field(default_factory=...) для config.yaml + env override."""
    return field(default_factory=lambda yk=yaml_key, ek=env_key, cf=cast_fn: _get(_get_yaml_config(), yk, ek, cf))


def _env(env_key: str, default: str = ""):
    """Shortcut: field(default_factory=...) для env-only настроек."""
    return field(default_factory=lambda ek=env_key, d=default: os.getenv(ek, d))


@dataclass
class Settings:
    """Настройки remote OCR сервера (из config.yaml + .env)"""

    # ===== СЕКРЕТЫ (только .env) =====
    data_dir: str = _env("REMOTE_OCR_DATA_DIR", "/data")
    chandra_base_url: str = _env("CHANDRA_BASE_URL")
    qwen_base_url: str = _env("QWEN_BASE_URL")
    redis_url: str = _env("REDIS_URL", "redis://redis:6379/0")
    supabase_url: str = _env("SUPABASE_URL")
    supabase_key: str = _env("SUPABASE_KEY")

    # ===== ЗАДАЧИ =====
    max_concurrent_jobs: int = _cfg("max_concurrent_jobs", "MAX_CONCURRENT_JOBS", int)
    # Legacy (Celery) — будут удалены при полном переходе на локальный OCR
    worker_prefetch: int = _cfg("worker_prefetch", "WORKER_PREFETCH", int)
    worker_max_tasks: int = _cfg("worker_max_tasks", "WORKER_MAX_TASKS", int)
    task_soft_timeout: int = _cfg("task_soft_timeout", "TASK_SOFT_TIMEOUT", int)
    task_hard_timeout: int = _cfg("task_hard_timeout", "TASK_HARD_TIMEOUT", int)
    task_max_retries: int = _cfg("task_max_retries", "TASK_MAX_RETRIES", int)
    task_retry_delay: int = _cfg("task_retry_delay", "TASK_RETRY_DELAY", int)

    # ===== ЗАЩИТА ОТ ЗАЦИКЛИВАНИЯ =====
    job_max_runtime_hours: int = _cfg("job_max_runtime_hours", "JOB_MAX_RUNTIME_HOURS", int)
    job_max_runtime_hours_lmstudio: int = _cfg("job_max_runtime_hours_lmstudio", "JOB_MAX_RUNTIME_HOURS_LMSTUDIO", int)
    job_max_retries: int = _cfg("job_max_retries", "JOB_MAX_RETRIES", int)

    # ===== OCR THREADING =====
    max_global_ocr_requests: int = _cfg("max_global_ocr_requests", "MAX_GLOBAL_OCR_REQUESTS", int)
    ocr_threads_per_job: int = _cfg("ocr_threads_per_job", "OCR_THREADS_PER_JOB", int)
    ocr_request_timeout: int = _cfg("ocr_request_timeout", "OCR_REQUEST_TIMEOUT", int)

    # ===== CHANDRA (LM Studio — TEXT/TABLE) =====
    chandra_max_concurrent: int = _cfg("chandra_max_concurrent", "CHANDRA_MAX_CONCURRENT", int)
    chandra_retry_delay: int = _cfg("chandra_retry_delay", "CHANDRA_RETRY_DELAY", int)
    chandra_http_timeout: int = _cfg("chandra_http_timeout", "CHANDRA_HTTP_TIMEOUT", int)
    chandra_model_key: str = _cfg("chandra_model_key", "CHANDRA_MODEL_KEY")
    chandra_context_length: int = _cfg("chandra_context_length", "CHANDRA_CONTEXT_LENGTH", int)
    chandra_flash_attention: bool = _cfg("chandra_flash_attention", "CHANDRA_FLASH_ATTENTION", bool)
    chandra_eval_batch_size: int = _cfg("chandra_eval_batch_size", "CHANDRA_EVAL_BATCH_SIZE", int)
    chandra_offload_kv_cache: bool = _cfg("chandra_offload_kv_cache", "CHANDRA_OFFLOAD_KV_CACHE", bool)
    chandra_max_image_size: int = _cfg("chandra_max_image_size", "CHANDRA_MAX_IMAGE_SIZE", int)
    chandra_preload_timeout: int = _cfg("chandra_preload_timeout", "CHANDRA_PRELOAD_TIMEOUT", int)
    chandra_max_retries: int = _cfg("chandra_max_retries", "CHANDRA_MAX_RETRIES", int)
    chandra_retry_delays: list = _cfg("chandra_retry_delays", "CHANDRA_RETRY_DELAYS")
    chandra_max_tokens: int = _cfg("chandra_max_tokens", "CHANDRA_MAX_TOKENS", int)
    chandra_temperature: float = _cfg("chandra_temperature", "CHANDRA_TEMPERATURE", float)
    chandra_top_p: float = _cfg("chandra_top_p", "CHANDRA_TOP_P", float)
    chandra_top_k: int = _cfg("chandra_top_k", "CHANDRA_TOP_K", int)
    chandra_repetition_penalty: float = _cfg("chandra_repetition_penalty", "CHANDRA_REPETITION_PENALTY", float)
    chandra_min_p: float = _cfg("chandra_min_p", "CHANDRA_MIN_P", float)
    chandra_system_prompt: str = _cfg("chandra_system_prompt", "CHANDRA_SYSTEM_PROMPT")
    chandra_user_prompt: str = _cfg("chandra_user_prompt", "CHANDRA_USER_PROMPT")

    # ===== QWEN (LM Studio — IMAGE/STAMP) =====
    qwen_max_concurrent: int = _cfg("qwen_max_concurrent", "QWEN_MAX_CONCURRENT", int)
    qwen_http_timeout: int = _cfg("qwen_http_timeout", "QWEN_HTTP_TIMEOUT", int)
    qwen_model_key: str = _cfg("qwen_model_key", "QWEN_MODEL_KEY")
    qwen_context_length: int = _cfg("qwen_context_length", "QWEN_CONTEXT_LENGTH", int)
    qwen_flash_attention: bool = _cfg("qwen_flash_attention", "QWEN_FLASH_ATTENTION", bool)
    qwen_eval_batch_size: int = _cfg("qwen_eval_batch_size", "QWEN_EVAL_BATCH_SIZE", int)
    qwen_offload_kv_cache: bool = _cfg("qwen_offload_kv_cache", "QWEN_OFFLOAD_KV_CACHE", bool)
    qwen_max_image_size: int = _cfg("qwen_max_image_size", "QWEN_MAX_IMAGE_SIZE", int)
    qwen_preload_timeout: int = _cfg("qwen_preload_timeout", "QWEN_PRELOAD_TIMEOUT", int)
    qwen_max_retries: int = _cfg("qwen_max_retries", "QWEN_MAX_RETRIES", int)
    qwen_retry_delays: list = _cfg("qwen_retry_delays", "QWEN_RETRY_DELAYS")
    qwen_max_tokens: int = _cfg("qwen_max_tokens", "QWEN_MAX_TOKENS", int)
    qwen_temperature: float = _cfg("qwen_temperature", "QWEN_TEMPERATURE", float)
    qwen_top_p: float = _cfg("qwen_top_p", "QWEN_TOP_P", float)
    qwen_top_k: int = _cfg("qwen_top_k", "QWEN_TOP_K", int)
    qwen_repetition_penalty: float = _cfg("qwen_repetition_penalty", "QWEN_REPETITION_PENALTY", float)
    qwen_min_p: float = _cfg("qwen_min_p", "QWEN_MIN_P", float)
    qwen_default_system_prompt: str = _cfg("qwen_default_system_prompt", "QWEN_DEFAULT_SYSTEM_PROMPT")
    qwen_default_user_prompt: str = _cfg("qwen_default_user_prompt", "QWEN_DEFAULT_USER_PROMPT")

    # ===== ВЕРИФИКАЦИЯ БЛОКОВ =====
    max_retry_blocks: int = _cfg("max_retry_blocks", "MAX_RETRY_BLOCKS", int)
    verification_timeout_minutes: int = _cfg("verification_timeout_minutes", "VERIFICATION_TIMEOUT_MINUTES", int)

    # ===== НАСТРОЙКИ OCR =====
    crop_png_compress: int = _cfg("crop_png_compress", "CROP_PNG_COMPRESS", int)
    pdf_render_dpi: int = _cfg("pdf_render_dpi", "PDF_RENDER_DPI", int)

    # ===== ДИНАМИЧЕСКИЙ ТАЙМАУТ =====
    dynamic_timeout_base: int = _cfg("dynamic_timeout_base", "DYNAMIC_TIMEOUT_BASE", int)
    seconds_per_block: int = _cfg("seconds_per_block", "SECONDS_PER_BLOCK", int)
    min_task_timeout: int = _cfg("min_task_timeout", "MIN_TASK_TIMEOUT", int)
    max_task_timeout: int = _cfg("max_task_timeout", "MAX_TASK_TIMEOUT", int)

    # ===== LEGACY (Celery queue) — будут удалены при полном переходе на локальный OCR =====
    poll_interval: float = _cfg("poll_interval", "POLL_INTERVAL", float)
    poll_max_interval: float = _cfg("poll_max_interval", "POLL_MAX_INTERVAL", float)
    max_queue_size: int = _cfg("max_queue_size", "MAX_QUEUE_SIZE", int)
    default_task_priority: int = _cfg("default_task_priority", "DEFAULT_TASK_PRIORITY", int)

    # ===== МОДЕЛИ ПО УМОЛЧАНИЮ =====
    default_engine: str = _cfg("default_engine", "DEFAULT_ENGINE")
    default_text_model: str = _cfg("default_text_model", "DEFAULT_TEXT_MODEL")
    default_image_model: str = _cfg("default_image_model", "DEFAULT_IMAGE_MODEL")
    default_stamp_model: str = _cfg("default_stamp_model", "DEFAULT_STAMP_MODEL")

    # ===== ПРОМПТЫ ДЛЯ OCR (IMAGE/STAMP) =====
    image_system_prompt: str = _cfg("image_system_prompt", "IMAGE_SYSTEM_PROMPT")
    image_user_prompt: str = _cfg("image_user_prompt", "IMAGE_USER_PROMPT")
    stamp_system_prompt: str = _cfg("stamp_system_prompt", "STAMP_SYSTEM_PROMPT")
    stamp_user_prompt: str = _cfg("stamp_user_prompt", "STAMP_USER_PROMPT")


# Lazy singleton: Settings создаётся при первом обращении
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Получить singleton Settings (lazy initialization)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


class _SettingsProxy:
    """Прозрачный proxy для обратной совместимости с `from .settings import settings`."""

    def __getattr__(self, name: str):
        return getattr(get_settings(), name)

    def __repr__(self) -> str:
        return repr(get_settings())


settings = _SettingsProxy()
