"""Управление lifecycle модели Chandra при параллельных Celery задачах.

Celery prefork = отдельные процессы. Каждый создаёт ChandraBackend
и вызывает unload_model() в finally. Redis reference counter координирует
выгрузку: модель выгружается только когда последняя задача завершится.
"""
from __future__ import annotations

import threading
from urllib.parse import urlparse

import redis

from .logging_config import get_logger
from .settings import settings

logger = get_logger(__name__)

CHANDRA_ACTIVE_KEY = "chandra:active_tasks"

_redis_pool: redis.ConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_redis_pool() -> redis.ConnectionPool:
    """Redis connection pool (паттерн из queue_checker.py)."""
    global _redis_pool
    if _redis_pool is None:
        with _pool_lock:
            if _redis_pool is None:
                parsed = urlparse(settings.redis_url)
                _redis_pool = redis.ConnectionPool(
                    host=parsed.hostname or "localhost",
                    port=parsed.port or 6379,
                    db=int(parsed.path.lstrip("/") or 0),
                    password=parsed.password,
                    decode_responses=True,
                    max_connections=10,
                )
    return _redis_pool


def _get_redis_client() -> redis.Redis:
    return redis.Redis(connection_pool=_get_redis_pool())


def acquire_chandra(job_id: str) -> int:
    """Зарегистрировать начало Chandra задачи. Возвращает новое значение счётчика."""
    try:
        client = _get_redis_client()
        count = client.incr(CHANDRA_ACTIVE_KEY)
        # TTL обновляется при каждом INCR — защита от зависших значений при crash
        client.expire(CHANDRA_ACTIVE_KEY, settings.task_hard_timeout)
        logger.info(
            f"Chandra acquire: job={job_id}, active_tasks={count}",
            extra={"event": "chandra_acquire", "job_id": job_id},
        )
        return count
    except Exception as e:
        logger.warning(f"Chandra acquire failed (fallback to 1): {e}")
        return 1


def release_chandra(job_id: str) -> int:
    """Снять регистрацию Chandra задачи. Возвращает оставшийся счётчик."""
    try:
        client = _get_redis_client()
        count = client.decr(CHANDRA_ACTIVE_KEY)
        if count < 0:
            client.set(CHANDRA_ACTIVE_KEY, 0)
            count = 0
            logger.warning(
                "Chandra counter went negative, reset to 0",
                extra={"event": "chandra_counter_reset", "job_id": job_id},
            )
        logger.info(
            f"Chandra release: job={job_id}, active_tasks={count}",
            extra={"event": "chandra_release", "job_id": job_id},
        )
        return count
    except Exception as e:
        logger.warning(f"Chandra release failed (fallback: will unload): {e}")
        return 0
