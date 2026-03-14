"""Централизованная конфигурация логирования для OCR сервера.

Использование:
    from .logging_config import setup_logging, get_logger

    # В точке входа (main.py, celery_app.py)
    setup_logging()

    # В любом модуле
    logger = get_logger(__name__)
    logger.info("Message", extra={"job_id": "abc-123"})

Переменные окружения:
    LOG_LEVEL - уровень логирования (DEBUG, INFO, WARNING, ERROR). По умолчанию: INFO
    LOG_FORMAT - формат логов (json, text). По умолчанию: json
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """JSON formatter для structured logging (ELK/CloudWatch compatible)."""

    # Поля, которые добавляются в extra для контекста
    EXTRA_FIELDS = frozenset({
        # Идентификация задачи
        "job_id",
        "task_id",
        "task_name",
        # Клиентский контекст
        "client_id",
        "action",
        "document_id",
        "document_name",
        "node_id",
        # Блоки и кропы
        "block_id",
        "strip_id",
        "page_index",
        "block_type",
        "block_ids",
        "block_count",
        "coords_norm",
        "crop_width",
        "crop_height",
        # OCR бэкенды
        "engine",
        "backend",
        "backend_type",
        "model_name",
        "text_model",
        "table_model",
        "image_model",
        "stamp_model",
        "use_pdf_crop",
        "category_code",
        # OCR обработка
        "strip_count",
        "image_count",
        "total_blocks",
        "recognized_count",
        "response_length",
        "prompt_length",
        "strip_attempt",
        "max_concurrent",
        "phase",
        "checkpoint_count",
        # Производительность
        "duration_ms",
        "memory_mb",
        "concurrency",
        "prefetch",
        "max_tasks_per_child",
        # HTTP / сеть
        "event",
        "status",
        "status_code",
        "method",
        "path",
        "client_ip",
        # Хранилище
        "r2_prefix",
        "r2_key",
        "remote_key",
        "local_path",
        "file_size",
        "pdf_size",
        # Очередь
        "queue_size",
        "max_queue_size",
        # Ошибки
        "config",
        "exception_type",
        "exception_message",
        "retry_reason",
        "retry_count",
    })

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Добавляем extra поля
        for field in self.EXTRA_FIELDS:
            if hasattr(record, field):
                value = getattr(record, field)
                if value is not None:
                    log_data[field] = value

        # Exception info
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False, default=str)


class HumanReadableFormatter(logging.Formatter):
    """Читаемый форматтер для локальной разработки."""

    FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    def __init__(self) -> None:
        super().__init__(self.FORMAT, datefmt="%Y-%m-%d %H:%M:%S")


def get_log_level() -> int:
    """Получить уровень логирования из env."""
    level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, level_str, logging.INFO)


def get_log_format() -> str:
    """Получить формат логов из env: 'json' или 'text'."""
    return os.getenv("LOG_FORMAT", "json").lower()


_logging_initialized = False


def setup_logging() -> None:
    """Настроить логирование для всего приложения.

    Вызывать один раз при старте приложения (main.py, celery_app.py).
    Безопасно вызывать повторно - инициализация произойдёт только один раз.
    """
    global _logging_initialized
    if _logging_initialized:
        return

    log_level = get_log_level()
    log_format = get_log_format()

    # Выбираем форматтер
    if log_format == "json":
        formatter: logging.Formatter = JSONFormatter()
    else:
        formatter = HumanReadableFormatter()

    # Настраиваем root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Очищаем существующие handlers (избегаем дублирования)
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Уровни для сторонних библиотек (уменьшаем шум)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiobotocore").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("celery").setLevel(logging.INFO)

    _logging_initialized = True


def get_logger(name: str) -> logging.Logger:
    """Получить логгер с заданным именем.

    Args:
        name: Имя логгера (обычно __name__)

    Returns:
        Настроенный логгер
    """
    return logging.getLogger(name)


class LogContext:
    """Context manager для добавления контекста к логам.

    Пример:
        with LogContext(logger, job_id="abc-123", task_id="task-456"):
            logger.info("Processing started")  # Автоматически добавит job_id и task_id
    """

    def __init__(self, logger: logging.Logger, **context: Any) -> None:
        self.logger = logger
        self.context = context
        self._old_factory: Any = None

    def __enter__(self) -> "LogContext":
        self._old_factory = logging.getLogRecordFactory()
        context = self.context

        def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = self._old_factory(*args, **kwargs)
            for key, value in context.items():
                setattr(record, key, value)
            return record

        logging.setLogRecordFactory(record_factory)
        return self

    def __exit__(self, *args: Any) -> None:
        if self._old_factory is not None:
            logging.setLogRecordFactory(self._old_factory)
