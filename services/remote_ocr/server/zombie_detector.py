"""Фоновый детектор зомби-задач после Hard timeout.

После Hard timeout (SIGKILL) Celery worker убит, cleanup не выполняется:
- Задача остаётся в статусе "processing" в БД навсегда
- Redis locks (lmstudio:active_jobs) не освобождены
- Пользователь не может отменить (worker мёртв)

Этот модуль запускает фоновый async loop в FastAPI lifespan,
который периодически находит и очищает такие зомби-задачи.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse

import redis

from .celery_app import celery_app
from .execution_lock import force_release_execution_lock
from .logging_config import get_logger
from .lmstudio_lifecycle import release_chandra, release_lmstudio
from .settings import settings
from .storage import get_job, list_jobs, update_job_status

logger = get_logger(__name__)

# Интервал проверки (секунды)
ZOMBIE_CHECK_INTERVAL = 300  # 5 минут

# Запас времени сверх динамического threshold (секунды)
_ZOMBIE_GRACE_SECONDS = 900  # 15 минут

# Fast-path: короткий threshold когда Celery inspect подтверждает отсутствие задачи
_FAST_PATH_THRESHOLD = 600  # 10 минут

# Redis key для double-confirm при недоступном Celery inspect
_SUSPECTS_KEY = "zombie:suspects"
_SUSPECTS_TTL = 1800  # 30 минут TTL для SET


def _get_zombie_threshold(job) -> float:
    """Динамический threshold в зависимости от engine задачи.

    LM Studio задачи (chandra/qwen) могут работать часами —
    фиксированный 15-минутный порог вызывает ложные срабатывания.
    """
    engine = getattr(job, "engine", None)
    if engine in ("chandra", "qwen"):
        max_hours = getattr(settings, "job_max_runtime_hours_lmstudio", 6)
        return max_hours * 3600 + _ZOMBIE_GRACE_SECONDS
    max_timeout = getattr(settings, "max_task_timeout", 14400)
    return max_timeout + _ZOMBIE_GRACE_SECONDS


# ── Redis для double-confirm ──────────────────────────────────────────

_redis_pool: redis.ConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_redis_client() -> redis.Redis:
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
                    max_connections=5,
                )
    return redis.Redis(connection_pool=_redis_pool)


def _is_confirmed_suspect(job_id: str) -> bool:
    """Double-confirm: при первом обнаружении добавляем в suspects,
    при втором (через >=5 мин) — считаем зомби.
    """
    try:
        client = _get_redis_client()
        already = client.sismember(_SUSPECTS_KEY, job_id)
        if already:
            return True  # Второй раз видим — подтверждённый зомби
        # Первый раз — добавляем в suspects, ждём следующей проверки
        client.sadd(_SUSPECTS_KEY, job_id)
        client.expire(_SUSPECTS_KEY, _SUSPECTS_TTL)
        return False
    except Exception:
        return False  # При ошибке Redis — не рискуем


def _clear_suspect(job_id: str) -> None:
    """Убрать из suspects (задача найдена в Celery active)."""
    try:
        client = _get_redis_client()
        client.srem(_SUSPECTS_KEY, job_id)
    except Exception:
        pass


def _detect_and_cleanup_zombies() -> int:
    """Найти и очистить зомби-задачи.

    Returns:
        Количество очищенных зомби-задач.
    """
    try:
        jobs = list_jobs()
    except Exception as exc:
        logger.warning(f"Zombie detector: ошибка получения списка задач: {exc}")
        return 0

    processing_jobs = [j for j in jobs if j.status == "processing"]
    if not processing_jobs:
        return 0

    # Получаем список активных Celery задач
    active_task_ids: set[str] | None = set()
    try:
        inspect = celery_app.control.inspect(timeout=5.0)
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        for worker_tasks in active.values():
            for task in worker_tasks:
                active_task_ids.add(task.get("id", ""))
        for worker_tasks in reserved.values():
            for task in worker_tasks:
                active_task_ids.add(task.get("id", ""))
    except Exception as exc:
        logger.warning(f"Zombie detector: ошибка inspect Celery: {exc}")
        # Если не удалось получить список — используем double-confirm
        active_task_ids = None

    now = datetime.now(timezone.utc)
    cleaned = 0

    for job in processing_jobs:
        # Проверка по времени обновления
        try:
            updated = datetime.fromisoformat(job.updated_at.replace("Z", "+00:00"))
            age_seconds = (now - updated).total_seconds()
        except (ValueError, TypeError, AttributeError):
            continue

        # Проверка наличия в Celery (если доступно)
        if active_task_ids is not None:
            if job.celery_task_id in active_task_ids:
                _clear_suspect(job.id)
                continue  # Задача ещё активна в Celery — не зомби
            # Fast-path: задачи нет в Celery active/reserved →
            # достаточно короткого threshold (10 мин)
            if age_seconds < _FAST_PATH_THRESHOLD:
                continue
        else:
            # Celery inspect недоступен — используем длинный threshold
            threshold = _get_zombie_threshold(job)
            if age_seconds < threshold:
                continue
            # Требуем double-confirm
            if not _is_confirmed_suspect(job.id):
                logger.info(
                    f"Zombie detector: {job.id[:8]} — suspect (первое обнаружение, "
                    f"ждём подтверждения в следующем цикле)",
                    extra={"event": "zombie_suspect", "job_id": job.id},
                )
                continue

        # Это зомби — очищаем
        logger.warning(
            f"Zombie detector: задача {job.id[:8]} — зомби "
            f"(updated {int(age_seconds)}s ago, threshold={int(threshold)}s, "
            f"celery_task={job.celery_task_id})",
            extra={
                "event": "zombie_detected",
                "job_id": job.id,
                "age_seconds": int(age_seconds),
            },
        )

        try:
            update_job_status(
                job.id, "error",
                error_message="Задача прервана (worker killed by hard timeout)",
                status_message="❌ Worker killed (hard timeout)",
            )
        except Exception as exc:
            logger.warning(f"Zombie detector: ошибка обновления статуса {job.id[:8]}: {exc}")
            continue

        # Освобождение execution lock
        force_release_execution_lock(job.id)

        # Освобождение LM Studio locks
        engine = getattr(job, "engine", None)
        if engine:
            try:
                if engine == "chandra":
                    release_chandra(job.id)
                elif engine == "qwen":
                    release_lmstudio("qwen", job.id)
            except Exception as exc:
                logger.warning(f"Zombie detector: ошибка release LM Studio {job.id[:8]}: {exc}")

        _clear_suspect(job.id)
        cleaned += 1

    return cleaned


async def zombie_detector_loop() -> None:
    """Фоновый async loop для периодической проверки зомби-задач."""
    logger.info(
        f"Zombie detector запущен (interval={ZOMBIE_CHECK_INTERVAL}s, "
        f"dynamic threshold)"
    )

    while True:
        try:
            await asyncio.sleep(ZOMBIE_CHECK_INTERVAL)
            cleaned = await asyncio.to_thread(_detect_and_cleanup_zombies)
            if cleaned > 0:
                logger.info(f"Zombie detector: очищено {cleaned} зомби-задач")
        except asyncio.CancelledError:
            logger.info("Zombie detector остановлен")
            raise
        except Exception as exc:
            logger.error(f"Zombie detector: неожиданная ошибка: {exc}", exc_info=True)
            await asyncio.sleep(60)  # Подождать перед retry
