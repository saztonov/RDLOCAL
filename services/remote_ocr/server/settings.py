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
    api_key: str = _env("REMOTE_OCR_API_KEY")
    openrouter_api_key: str = _env("OPENROUTER_API_KEY")
    openrouter_base_url: str = _env("OPENROUTER_BASE_URL", "https://openrouter.ai")
    datalab_api_key: str = _env("DATALAB_API_KEY")
    chandra_base_url: str = _env("CHANDRA_BASE_URL")
    redis_url: str = _env("REDIS_URL", "redis://redis:6379/0")
    supabase_url: str = _env("SUPABASE_URL")
    supabase_key: str = _env("SUPABASE_KEY")

    # ===== CELERY WORKER =====
    max_concurrent_jobs: int = _cfg("max_concurrent_jobs", "MAX_CONCURRENT_JOBS", int)
    worker_prefetch: int = _cfg("worker_prefetch", "WORKER_PREFETCH", int)
    worker_max_tasks: int = _cfg("worker_max_tasks", "WORKER_MAX_TASKS", int)
    task_soft_timeout: int = _cfg("task_soft_timeout", "TASK_SOFT_TIMEOUT", int)
    task_hard_timeout: int = _cfg("task_hard_timeout", "TASK_HARD_TIMEOUT", int)
    task_max_retries: int = _cfg("task_max_retries", "TASK_MAX_RETRIES", int)
    task_retry_delay: int = _cfg("task_retry_delay", "TASK_RETRY_DELAY", int)

    # ===== ЗАЩИТА ОТ ЗАЦИКЛИВАНИЯ =====
    job_max_runtime_hours: int = _cfg("job_max_runtime_hours", "JOB_MAX_RUNTIME_HOURS", int)
    job_max_retries: int = _cfg("job_max_retries", "JOB_MAX_RETRIES", int)

    # ===== OCR THREADING =====
    max_global_ocr_requests: int = _cfg("max_global_ocr_requests", "MAX_GLOBAL_OCR_REQUESTS", int)
    ocr_threads_per_job: int = _cfg("ocr_threads_per_job", "OCR_THREADS_PER_JOB", int)
    ocr_request_timeout: int = _cfg("ocr_request_timeout", "OCR_REQUEST_TIMEOUT", int)

    # ===== DATALAB API =====
    datalab_max_rpm: int = _cfg("datalab_max_rpm", "DATALAB_MAX_RPM", int)
    datalab_max_concurrent: int = _cfg("datalab_max_concurrent", "DATALAB_MAX_CONCURRENT", int)
    datalab_poll_interval: int = _cfg("datalab_poll_interval", "DATALAB_POLL_INTERVAL", int)
    datalab_poll_max_attempts: int = _cfg("datalab_poll_max_attempts", "DATALAB_POLL_MAX_ATTEMPTS", int)
    datalab_max_retries: int = _cfg("datalab_max_retries", "DATALAB_MAX_RETRIES", int)
    datalab_extras: str = _cfg("datalab_extras", "DATALAB_EXTRAS")
    datalab_quality_threshold: float = _cfg("datalab_quality_threshold", "DATALAB_QUALITY_THRESHOLD", float)

    # ===== CHANDRA (LM Studio) =====
    chandra_max_concurrent: int = _cfg("chandra_max_concurrent", "CHANDRA_MAX_CONCURRENT", int)
    chandra_retry_delay: int = _cfg("chandra_retry_delay", "CHANDRA_RETRY_DELAY", int)

    # ===== QWEN (LM Studio) =====
    qwen_base_url: str = field(
        default_factory=lambda: os.getenv("QWEN_BASE_URL") or os.getenv("CHANDRA_BASE_URL", "")
    )
    qwen_max_concurrent: int = _cfg("qwen_max_concurrent", "QWEN_MAX_CONCURRENT", int)
    qwen_retry_delay: int = _cfg("qwen_retry_delay", "QWEN_RETRY_DELAY", int)

    # ===== ВЕРИФИКАЦИЯ БЛОКОВ =====
    max_retry_blocks: int = _cfg("max_retry_blocks", "MAX_RETRY_BLOCKS", int)
    verification_timeout_minutes: int = _cfg("verification_timeout_minutes", "VERIFICATION_TIMEOUT_MINUTES", int)

    # ===== НАСТРОЙКИ OCR =====
    crop_png_compress: int = _cfg("crop_png_compress", "CROP_PNG_COMPRESS", int)
    max_ocr_batch_size: int = _cfg("max_ocr_batch_size", "MAX_OCR_BATCH_SIZE", int)
    pdf_render_dpi: int = _cfg("pdf_render_dpi", "PDF_RENDER_DPI", int)
    max_strip_height: int = _cfg("max_strip_height", "MAX_STRIP_HEIGHT", int)

    # ===== ДИНАМИЧЕСКИЙ ТАЙМАУТ =====
    dynamic_timeout_base: int = _cfg("dynamic_timeout_base", "DYNAMIC_TIMEOUT_BASE", int)
    seconds_per_block: int = _cfg("seconds_per_block", "SECONDS_PER_BLOCK", int)
    min_task_timeout: int = _cfg("min_task_timeout", "MIN_TASK_TIMEOUT", int)
    max_task_timeout: int = _cfg("max_task_timeout", "MAX_TASK_TIMEOUT", int)

    # ===== ОЧЕРЕДЬ =====
    poll_interval: float = _cfg("poll_interval", "POLL_INTERVAL", float)
    poll_max_interval: float = _cfg("poll_max_interval", "POLL_MAX_INTERVAL", float)
    max_queue_size: int = _cfg("max_queue_size", "MAX_QUEUE_SIZE", int)
    default_task_priority: int = _cfg("default_task_priority", "DEFAULT_TASK_PRIORITY", int)

    # ===== МОДЕЛИ ПО УМОЛЧАНИЮ =====
    default_engine: str = _cfg("default_engine", "DEFAULT_ENGINE")
    default_image_model: str = _cfg("default_image_model", "DEFAULT_IMAGE_MODEL")
    default_stamp_model: str = _cfg("default_stamp_model", "DEFAULT_STAMP_MODEL")

    # ===== ПРОМПТЫ ДЛЯ OCR =====
    openrouter_image_system_prompt: str = _cfg("openrouter_image_system_prompt", "OPENROUTER_IMAGE_SYSTEM_PROMPT")
    openrouter_image_user_prompt: str = _cfg("openrouter_image_user_prompt", "OPENROUTER_IMAGE_USER_PROMPT")
    openrouter_stamp_system_prompt: str = _cfg("openrouter_stamp_system_prompt", "OPENROUTER_STAMP_SYSTEM_PROMPT")
    openrouter_stamp_user_prompt: str = _cfg("openrouter_stamp_user_prompt", "OPENROUTER_STAMP_USER_PROMPT")
    qwen_text_system_prompt: str = _cfg("qwen_text_system_prompt", "QWEN_TEXT_SYSTEM_PROMPT")
    qwen_text_user_prompt: str = _cfg("qwen_text_user_prompt", "QWEN_TEXT_USER_PROMPT")
    qwen_stamp_system_prompt: str = _cfg("qwen_stamp_system_prompt", "QWEN_STAMP_SYSTEM_PROMPT")
    qwen_stamp_user_prompt: str = _cfg("qwen_stamp_user_prompt", "QWEN_STAMP_USER_PROMPT")


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
