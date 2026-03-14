from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

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


# Загружаем конфигурацию один раз при импорте модуля
_yaml_config = _load_yaml_config()


@dataclass
class Settings:
    """Настройки remote OCR сервера (из config.yaml + .env)"""

    # ===== СЕКРЕТЫ (только .env) =====
    data_dir: str = field(
        default_factory=lambda: os.getenv("REMOTE_OCR_DATA_DIR", "/data")
    )
    api_key: str = field(default_factory=lambda: os.getenv("REMOTE_OCR_API_KEY", ""))
    openrouter_api_key: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_API_KEY", "")
    )
    openrouter_base_url: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai")
    )
    datalab_api_key: str = field(
        default_factory=lambda: os.getenv("DATALAB_API_KEY", "")
    )
    chandra_base_url: str = field(
        default_factory=lambda: os.getenv("CHANDRA_BASE_URL", "")
    )
    redis_url: str = field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://redis:6379/0")
    )
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_key: str = field(default_factory=lambda: os.getenv("SUPABASE_KEY", ""))

    # ===== CELERY WORKER (config.yaml + env override) =====
    max_concurrent_jobs: int = field(
        default_factory=lambda: _get(
            _yaml_config, "max_concurrent_jobs", "MAX_CONCURRENT_JOBS", int
        )
    )
    worker_prefetch: int = field(
        default_factory=lambda: _get(
            _yaml_config, "worker_prefetch", "WORKER_PREFETCH", int
        )
    )
    worker_max_tasks: int = field(
        default_factory=lambda: _get(
            _yaml_config, "worker_max_tasks", "WORKER_MAX_TASKS", int
        )
    )
    task_soft_timeout: int = field(
        default_factory=lambda: _get(
            _yaml_config, "task_soft_timeout", "TASK_SOFT_TIMEOUT", int
        )
    )
    task_hard_timeout: int = field(
        default_factory=lambda: _get(
            _yaml_config, "task_hard_timeout", "TASK_HARD_TIMEOUT", int
        )
    )
    task_max_retries: int = field(
        default_factory=lambda: _get(
            _yaml_config, "task_max_retries", "TASK_MAX_RETRIES", int
        )
    )
    task_retry_delay: int = field(
        default_factory=lambda: _get(
            _yaml_config, "task_retry_delay", "TASK_RETRY_DELAY", int
        )
    )

    # ===== ЗАЩИТА ОТ ЗАЦИКЛИВАНИЯ =====
    job_max_runtime_hours: int = field(
        default_factory=lambda: _get(
            _yaml_config, "job_max_runtime_hours", "JOB_MAX_RUNTIME_HOURS", int
        )
    )
    job_max_retries: int = field(
        default_factory=lambda: _get(
            _yaml_config, "job_max_retries", "JOB_MAX_RETRIES", int
        )
    )

    # ===== OCR THREADING =====
    max_global_ocr_requests: int = field(
        default_factory=lambda: _get(
            _yaml_config, "max_global_ocr_requests", "MAX_GLOBAL_OCR_REQUESTS", int
        )
    )
    ocr_threads_per_job: int = field(
        default_factory=lambda: _get(
            _yaml_config, "ocr_threads_per_job", "OCR_THREADS_PER_JOB", int
        )
    )
    ocr_request_timeout: int = field(
        default_factory=lambda: _get(
            _yaml_config, "ocr_request_timeout", "OCR_REQUEST_TIMEOUT", int
        )
    )

    # ===== DATALAB API =====
    datalab_max_rpm: int = field(
        default_factory=lambda: _get(
            _yaml_config, "datalab_max_rpm", "DATALAB_MAX_RPM", int
        )
    )
    datalab_max_concurrent: int = field(
        default_factory=lambda: _get(
            _yaml_config, "datalab_max_concurrent", "DATALAB_MAX_CONCURRENT", int
        )
    )
    datalab_poll_interval: int = field(
        default_factory=lambda: _get(
            _yaml_config, "datalab_poll_interval", "DATALAB_POLL_INTERVAL", int
        )
    )
    datalab_poll_max_attempts: int = field(
        default_factory=lambda: _get(
            _yaml_config, "datalab_poll_max_attempts", "DATALAB_POLL_MAX_ATTEMPTS", int
        )
    )
    datalab_max_retries: int = field(
        default_factory=lambda: _get(
            _yaml_config, "datalab_max_retries", "DATALAB_MAX_RETRIES", int
        )
    )
    datalab_extras: str = field(
        default_factory=lambda: _get(
            _yaml_config, "datalab_extras", "DATALAB_EXTRAS"
        )
    )
    datalab_quality_threshold: float = field(
        default_factory=lambda: _get(
            _yaml_config, "datalab_quality_threshold", "DATALAB_QUALITY_THRESHOLD", float
        )
    )

    # ===== CHANDRA (LM Studio) =====
    chandra_max_concurrent: int = field(
        default_factory=lambda: _get(
            _yaml_config, "chandra_max_concurrent", "CHANDRA_MAX_CONCURRENT", int
        )
    )
    chandra_retry_delay: int = field(
        default_factory=lambda: _get(
            _yaml_config, "chandra_retry_delay", "CHANDRA_RETRY_DELAY", int
        )
    )

    # ===== QWEN (LM Studio) =====
    qwen_base_url: str = field(
        default_factory=lambda: os.getenv("QWEN_BASE_URL")
        or os.getenv("CHANDRA_BASE_URL", "")
    )
    qwen_max_concurrent: int = field(
        default_factory=lambda: _get(
            _yaml_config, "qwen_max_concurrent", "QWEN_MAX_CONCURRENT", int
        )
    )
    qwen_retry_delay: int = field(
        default_factory=lambda: _get(
            _yaml_config, "qwen_retry_delay", "QWEN_RETRY_DELAY", int
        )
    )

    # ===== ВЕРИФИКАЦИЯ БЛОКОВ =====
    max_retry_blocks: int = field(
        default_factory=lambda: _get(
            _yaml_config, "max_retry_blocks", "MAX_RETRY_BLOCKS", int
        )
    )
    verification_timeout_minutes: int = field(
        default_factory=lambda: _get(
            _yaml_config, "verification_timeout_minutes", "VERIFICATION_TIMEOUT_MINUTES", int
        )
    )

    # ===== НАСТРОЙКИ OCR =====
    crop_png_compress: int = field(
        default_factory=lambda: _get(
            _yaml_config, "crop_png_compress", "CROP_PNG_COMPRESS", int
        )
    )
    max_ocr_batch_size: int = field(
        default_factory=lambda: _get(
            _yaml_config, "max_ocr_batch_size", "MAX_OCR_BATCH_SIZE", int
        )
    )
    pdf_render_dpi: int = field(
        default_factory=lambda: _get(
            _yaml_config, "pdf_render_dpi", "PDF_RENDER_DPI", int
        )
    )
    max_strip_height: int = field(
        default_factory=lambda: _get(
            _yaml_config, "max_strip_height", "MAX_STRIP_HEIGHT", int
        )
    )

    # ===== ДИНАМИЧЕСКИЙ ТАЙМАУТ =====
    dynamic_timeout_base: int = field(
        default_factory=lambda: _get(
            _yaml_config, "dynamic_timeout_base", "DYNAMIC_TIMEOUT_BASE", int
        )
    )
    seconds_per_block: int = field(
        default_factory=lambda: _get(
            _yaml_config, "seconds_per_block", "SECONDS_PER_BLOCK", int
        )
    )
    min_task_timeout: int = field(
        default_factory=lambda: _get(
            _yaml_config, "min_task_timeout", "MIN_TASK_TIMEOUT", int
        )
    )
    max_task_timeout: int = field(
        default_factory=lambda: _get(
            _yaml_config, "max_task_timeout", "MAX_TASK_TIMEOUT", int
        )
    )

    # ===== ОЧЕРЕДЬ =====
    poll_interval: float = field(
        default_factory=lambda: _get(
            _yaml_config, "poll_interval", "POLL_INTERVAL", float
        )
    )
    poll_max_interval: float = field(
        default_factory=lambda: _get(
            _yaml_config, "poll_max_interval", "POLL_MAX_INTERVAL", float
        )
    )
    max_queue_size: int = field(
        default_factory=lambda: _get(
            _yaml_config, "max_queue_size", "MAX_QUEUE_SIZE", int
        )
    )
    default_task_priority: int = field(
        default_factory=lambda: _get(
            _yaml_config, "default_task_priority", "DEFAULT_TASK_PRIORITY", int
        )
    )

    # ===== МОДЕЛИ ПО УМОЛЧАНИЮ (config.yaml + env override) =====
    default_engine: str = field(
        default_factory=lambda: _get(
            _yaml_config, "default_engine", "DEFAULT_ENGINE"
        )
    )
    default_image_model: str = field(
        default_factory=lambda: _get(
            _yaml_config, "default_image_model", "DEFAULT_IMAGE_MODEL"
        )
    )
    default_stamp_model: str = field(
        default_factory=lambda: _get(
            _yaml_config, "default_stamp_model", "DEFAULT_STAMP_MODEL"
        )
    )


settings = Settings()
