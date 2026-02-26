"""Управление lifecycle моделей LM Studio при параллельных Celery задачах.

Celery prefork = отдельные процессы. Каждый создаёт Backend
и вызывает unload_model() в finally. Redis reference counter координирует
выгрузку: модель выгружается только когда последняя задача завершится.

Поддерживает несколько движков (chandra, qwen и т.д.) через параметрический ключ.
"""
from __future__ import annotations

import threading
from urllib.parse import urlparse

import redis

from .logging_config import get_logger
from .settings import settings

logger = get_logger(__name__)

_redis_pool: redis.ConnectionPool | None = None
_pool_lock = threading.Lock()


def _active_key(engine: str) -> str:
    """Redis key для счётчика активных задач данного движка."""
    return f"lmstudio:{engine}:active_tasks"


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


# ── Универсальные функции ───────────────────────────────────────────

def acquire_lmstudio(engine: str, job_id: str) -> int:
    """Зарегистрировать начало задачи для LM Studio движка. Возвращает счётчик."""
    try:
        client = _get_redis_client()
        key = _active_key(engine)
        count = client.incr(key)
        # TTL обновляется при каждом INCR — защита от зависших значений при crash
        client.expire(key, settings.task_hard_timeout)
        logger.info(
            f"{engine} acquire: job={job_id}, active_tasks={count}",
            extra={"event": f"{engine}_acquire", "job_id": job_id},
        )
        return count
    except Exception as e:
        logger.warning(f"{engine} acquire failed (fallback to 1): {e}")
        return 1


def release_lmstudio(engine: str, job_id: str) -> int:
    """Снять регистрацию задачи для LM Studio движка. Возвращает оставшийся счётчик."""
    try:
        client = _get_redis_client()
        key = _active_key(engine)
        count = client.decr(key)
        if count < 0:
            client.set(key, 0)
            count = 0
            logger.warning(
                f"{engine} counter went negative, reset to 0",
                extra={"event": f"{engine}_counter_reset", "job_id": job_id},
            )
        logger.info(
            f"{engine} release: job={job_id}, active_tasks={count}",
            extra={"event": f"{engine}_release", "job_id": job_id},
        )
        return count
    except Exception as e:
        logger.warning(f"{engine} release failed (fallback: will unload): {e}")
        return 0


# ── Обратная совместимость (Chandra) ────────────────────────────────

def acquire_chandra(job_id: str) -> int:
    """Обратная совместимость: acquire для Chandra."""
    return acquire_lmstudio("chandra", job_id)


def release_chandra(job_id: str) -> int:
    """Обратная совместимость: release для Chandra."""
    return release_lmstudio("chandra", job_id)
