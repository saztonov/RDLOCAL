"""Управление lifecycle моделей LM Studio при параллельных Celery задачах.

Delayed unload: вместо немедленной выгрузки при remaining==0,
модель остаётся загруженной на UNLOAD_GRACE_SECONDS. Если за это время
придёт новая задача — выгрузка отменяется (acquire удаляет pending ключ).

Celery prefork = отдельные процессы. Каждый создаёт Backend
и вызывает unload_model() в finally. Redis SET координирует
выгрузку: модель выгружается только когда последняя задача завершится.

Используется Redis SET (SADD/SREM/SCARD) вместо INCR/DECR:
- SET не может уйти в минус
- Не допускает дублей (повторный acquire одного job_id — no-op)
- release без acquire — безопасный no-op (SREM несуществующего элемента)
- TTL 24h как страховка от крашей

Поддерживает движок chandra через параметрический ключ.
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

# TTL для SET-ключа — страховка от крашей (24 часа)
_SAFETY_TTL = 86400

# Grace period перед выгрузкой модели (секунды)
UNLOAD_GRACE_SECONDS = 120


def _active_key(engine: str) -> str:
    """Redis key для множества активных задач данного движка."""
    return f"lmstudio:{engine}:active_jobs"


def _pending_unload_key(engine: str) -> str:
    """Redis key для отложенной выгрузки."""
    return f"lmstudio:{engine}:pending_unload"


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
        client.sadd(key, job_id)
        # TTL обновляется при каждом acquire — страховка от крашей
        client.expire(key, _SAFETY_TTL)
        # Отменяем pending unload — новая задача пришла
        client.delete(_pending_unload_key(engine))
        count = client.scard(key)
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
        client.srem(key, job_id)
        count = client.scard(key)
        if count > 0:
            # Обновляем TTL пока есть активные задачи
            client.expire(key, _SAFETY_TTL)
        logger.info(
            f"{engine} release: job={job_id}, active_tasks={count}",
            extra={"event": f"{engine}_release", "job_id": job_id},
        )
        return count
    except Exception as e:
        logger.warning(f"{engine} release failed (fallback: will unload): {e}")
        return 0


def schedule_pending_unload(engine: str) -> None:
    """Запланировать отложенную выгрузку модели (если нет активных задач).

    Сохраняет timestamp, когда выгрузка была запланирована.
    Background loop через UNLOAD_GRACE_SECONDS проверит и выгрузит.
    """
    import time

    try:
        client = _get_redis_client()
        count = client.scard(_active_key(engine))
        if count == 0:
            client.set(
                _pending_unload_key(engine),
                str(time.time()),
                ex=UNLOAD_GRACE_SECONDS + 60,  # +60s запас чтобы loop успел проверить
            )
            logger.info(
                f"{engine}: pending unload запланирован (grace={UNLOAD_GRACE_SECONDS}s)",
                extra={"event": f"{engine}_pending_unload"},
            )
    except Exception as e:
        logger.warning(f"{engine} schedule_pending_unload failed: {e}")


def check_and_unload_models() -> None:
    """Проверить pending unloads и выгрузить модели если grace period истёк.

    Вызывается из background loop (каждые 30 сек).
    """
    import time

    for engine in ("chandra", "qwen"):
        try:
            client = _get_redis_client()
            pending_ts = client.get(_pending_unload_key(engine))
            if pending_ts is None:
                continue

            elapsed = time.time() - float(pending_ts)
            if elapsed < UNLOAD_GRACE_SECONDS:
                continue  # Grace period ещё не истёк

            # Проверяем что нет новых активных задач
            count = client.scard(_active_key(engine))
            if count > 0:
                # Новая задача пришла, удаляем pending
                client.delete(_pending_unload_key(engine))
                continue

            # Grace period истёк, нет активных — выгружаем
            client.delete(_pending_unload_key(engine))
            _do_unload_model(engine)

        except Exception as e:
            logger.warning(f"check_and_unload_models({engine}): {e}")


def _do_unload_model(engine: str) -> None:
    """Выполнить выгрузку модели LM Studio."""
    from .settings import settings

    base_url = None
    if engine == "chandra":
        base_url = getattr(settings, "chandra_base_url", None)
    elif engine == "qwen":
        base_url = getattr(settings, "qwen_base_url", None) or getattr(settings, "chandra_base_url", None)

    if not base_url:
        return

    try:
        import requests

        resp = requests.get(f"{base_url}/api/v1/models", timeout=10)
        if resp.status_code != 200:
            return

        # Определяем точный model key для matching
        if engine == "chandra":
            from rd_core.ocr._chandra_common import CHANDRA_MODEL_KEY
            model_key_lower = CHANDRA_MODEL_KEY.lower()
        elif engine == "qwen":
            from rd_core.ocr._qwen_common import QWEN_MODEL_KEY
            model_key_lower = QWEN_MODEL_KEY.lower()
        else:
            model_key_lower = engine

        for m in resp.json().get("models", []):
            if model_key_lower in m.get("key", "").lower():
                for inst in m.get("loaded_instances", []):
                    requests.post(
                        f"{base_url}/api/v1/models/unload",
                        json={"instance_id": inst["id"]},
                        timeout=30,
                    )
                    logger.info(
                        f"{engine}: модель выгружена после grace period: {inst['id']}",
                        extra={"event": f"{engine}_delayed_unload"},
                    )
                break
    except Exception as e:
        logger.warning(f"_do_unload_model({engine}): {e}")


# ── Обратная совместимость (Chandra) ────────────────────────────────

def acquire_chandra(job_id: str) -> int:
    """Обратная совместимость: acquire для Chandra."""
    return acquire_lmstudio("chandra", job_id)


def release_chandra(job_id: str) -> int:
    """Обратная совместимость: release для Chandra."""
    return release_lmstudio("chandra", job_id)


def acquire_qwen(job_id: str) -> int:
    """Обратная совместимость: acquire для Qwen."""
    return acquire_lmstudio("qwen", job_id)


def release_qwen(job_id: str) -> int:
    """Обратная совместимость: release для Qwen."""
    return release_lmstudio("qwen", job_id)
