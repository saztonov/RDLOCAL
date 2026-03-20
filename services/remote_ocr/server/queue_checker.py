"""Проверка размера очереди Redis для backpressure"""
from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import redis

from .settings import settings

# Thread-safe connection pool для Redis
_redis_pool: redis.ConnectionPool | None = None
_pool_lock = threading.Lock()

# Кэш Celery inspect (дорогая операция, кэшируем на 30 сек)
_inspect_cache: dict = {"count": 0, "ts": 0.0}
_INSPECT_CACHE_TTL = 30.0


def _get_redis_pool() -> redis.ConnectionPool:
    """Получить Redis connection pool (thread-safe singleton)"""
    global _redis_pool
    if _redis_pool is None:
        with _pool_lock:
            if _redis_pool is None:  # double-check
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
    """Получить Redis клиент с connection pooling"""
    return redis.Redis(connection_pool=_get_redis_pool())


def get_queue_size() -> int:
    """Получить текущий размер очереди Celery"""
    try:
        client = _get_redis_client()
        # Celery default queue name
        return client.llen("celery")
    except Exception:
        return 0


def get_active_count() -> int:
    """Получить количество активных задач в Celery workers (с кэшем)."""
    now = time.time()
    if now - _inspect_cache["ts"] < _INSPECT_CACHE_TTL:
        return _inspect_cache["count"]

    try:
        from .celery_app import celery_app

        inspect = celery_app.control.inspect(timeout=3.0)
        active = inspect.active() or {}
        count = sum(len(tasks) for tasks in active.values())
        _inspect_cache.update(count=count, ts=now)
        return count
    except Exception:
        return _inspect_cache["count"]  # fallback: предыдущее значение


def is_queue_full() -> bool:
    """Проверить, переполнена ли очередь"""
    if settings.max_queue_size <= 0:
        return False  # Без лимита
    return get_queue_size() + get_active_count() >= settings.max_queue_size


def check_queue_capacity() -> tuple[bool, int, int]:
    """Проверить ёмкость очереди (очередь + активные задачи).

    Returns:
        (can_accept, current_total_load, max_size)
    """
    current = get_queue_size() + get_active_count()
    max_size = settings.max_queue_size
    can_accept = max_size <= 0 or current < max_size
    return can_accept, current, max_size
