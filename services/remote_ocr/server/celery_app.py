"""Конфигурация Celery для очереди OCR задач"""
from __future__ import annotations

from celery import Celery

from .logging_config import setup_logging
from .settings import settings

# Инициализация логирования для воркера
setup_logging()

# Импорт signals для регистрации handlers (важно: после setup_logging)
from . import celery_signals  # noqa: F401, E402

celery_app = Celery("ocr_worker", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # ===== WORKER =====
    # Concurrency: количество параллельных задач
    worker_concurrency=settings.max_concurrent_jobs,
    # Prefetch: сколько задач брать заранее
    worker_prefetch_multiplier=settings.worker_prefetch,
    # Перезапуск воркера после N задач (защита от утечек памяти)
    worker_max_tasks_per_child=settings.worker_max_tasks,
    # ===== BROKER =====
    # visibility_timeout: время, после которого Redis переподаёт не-ACK задачу.
    # Дефолт = 3600s (1 час), но OCR задачи могут работать до 4+ часов.
    # При visibility_timeout < task_time → duplicate delivery (задача исполняется
    # параллельно двумя воркерами). Ставим 24 часа для полной безопасности.
    broker_transport_options={
        "visibility_timeout": 86400,
    },
    # ===== ЗАДАЧИ =====
    # Подтверждение после выполнения (не терять при падении)
    task_acks_late=True,
    # НЕ переподавать задачу при потере worker (hard timeout SIGKILL).
    # При True: worker killed → task re-queued → duplicate delivery → зацикливание.
    # При False: worker killed → task lost → zombie_detector переводит в "error".
    # Ручной restart через UI — надёжнее автоматического зацикливания.
    task_reject_on_worker_lost=False,
    # Soft/hard time limits
    task_soft_time_limit=settings.task_soft_timeout,
    task_time_limit=settings.task_hard_timeout,
    # Retry
    task_default_retry_delay=settings.task_retry_delay,
    # ===== РЕЗУЛЬТАТЫ =====
    # Результаты храним 1 час
    result_expires=3600,
    # ===== ОЧЕРЕДЬ =====
    # Приоритет (требует Redis)
    task_default_priority=settings.default_task_priority,
    task_queue_max_priority=10,
    # Регистрация задач
    imports=["services.remote_ocr.server.tasks"],
)
