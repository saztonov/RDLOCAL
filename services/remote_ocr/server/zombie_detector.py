"""Фоновый детектор зомби-задач — embedded режим.

В embedded режиме (без Celery) зомби возникают когда:
- OCR процесс убит hard timeout
- Процесс упал с segfault
- Контейнер перезапущен (задачи остались processing в Supabase)

Детектор проверяет: если задача в статусе "processing" и не обновлялась
дольше threshold — это зомби. Очищает статус и освобождает locks.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone

from .execution_lock import force_release_execution_lock
from .logging_config import get_logger
from .lmstudio_lifecycle import release_chandra
from .settings import settings
from .storage import list_jobs, update_job_status

logger = get_logger(__name__)

# Интервал проверки (секунды)
ZOMBIE_CHECK_INTERVAL = 300  # 5 минут

# Запас времени сверх динамического threshold (секунды)
_ZOMBIE_GRACE_SECONDS = 900  # 15 минут

# In-process suspects tracking (заменяет Redis SET)
_suspects_lock = threading.Lock()
_suspects: dict[str, float] = {}  # job_id -> timestamp первого обнаружения


def _get_zombie_threshold(job) -> float:
    """Динамический threshold в зависимости от engine."""
    engine = getattr(job, "engine", None)
    if engine == "chandra":
        max_hours = getattr(settings, "job_max_runtime_hours_lmstudio", 6)
        return max_hours * 3600 + _ZOMBIE_GRACE_SECONDS
    max_timeout = getattr(settings, "max_task_timeout", 14400)
    return max_timeout + _ZOMBIE_GRACE_SECONDS


def _is_confirmed_suspect(job_id: str) -> bool:
    """Double-confirm: первый раз — добавляем, второй — подтверждаем."""
    import time
    with _suspects_lock:
        if job_id in _suspects:
            # Подтверждённый если прошло >= 5 мин с первого обнаружения
            if time.time() - _suspects[job_id] >= 300:
                return True
            return False
        _suspects[job_id] = time.time()
        return False


def _clear_suspect(job_id: str) -> None:
    """Убрать из suspects."""
    with _suspects_lock:
        _suspects.pop(job_id, None)


def _detect_and_cleanup_zombies() -> int:
    """Найти и очистить зомби-задачи."""
    try:
        jobs = list_jobs()
    except Exception as exc:
        logger.warning(f"Zombie detector: ошибка получения списка задач: {exc}")
        return 0

    processing_jobs = [j for j in jobs if j.status == "processing"]
    if not processing_jobs:
        return 0

    # В embedded режиме проверяем через job manager
    from .embedded_job_manager_singleton import get_job_manager
    manager = get_job_manager()
    active_job_ids = set(manager._active.keys())

    now = datetime.now(timezone.utc)
    cleaned = 0

    for job in processing_jobs:
        try:
            updated = datetime.fromisoformat(job.updated_at.replace("Z", "+00:00"))
            age_seconds = (now - updated).total_seconds()
        except (ValueError, TypeError, AttributeError):
            continue

        # Если задача активна в job manager — не зомби
        if job.id in active_job_ids:
            _clear_suspect(job.id)
            continue

        # Задачи нет в активных — проверяем threshold
        threshold = _get_zombie_threshold(job)
        if age_seconds < threshold:
            # Но если задачи нет в manager вообще и прошло > 10 мин — suspect
            if age_seconds > 600 and not _is_confirmed_suspect(job.id):
                logger.info(
                    f"Zombie detector: {job.id[:8]} — suspect (не найден в manager, "
                    f"ждём подтверждения)",
                    extra={"event": "zombie_suspect", "job_id": job.id},
                )
            continue

        # Double-confirm
        if not _is_confirmed_suspect(job.id):
            continue

        # Это зомби — очищаем
        logger.warning(
            f"Zombie detector: задача {job.id[:8]} — зомби "
            f"(updated {int(age_seconds)}s ago, threshold={int(threshold)}s)",
            extra={
                "event": "zombie_detected",
                "job_id": job.id,
                "age_seconds": int(age_seconds),
            },
        )

        try:
            update_job_status(
                job.id, "error",
                error_message="Задача прервана (процесс завершился неожиданно)",
                status_message="❌ Process killed (timeout/crash)",
            )
        except Exception as exc:
            logger.warning(f"Zombie detector: ошибка обновления статуса {job.id[:8]}: {exc}")
            continue

        force_release_execution_lock(job.id)

        engine = getattr(job, "engine", None)
        if engine == "chandra":
            try:
                release_chandra(job.id)
            except Exception as exc:
                logger.warning(f"Zombie detector: ошибка release LM Studio {job.id[:8]}: {exc}")

        _clear_suspect(job.id)
        cleaned += 1

    return cleaned


async def zombie_detector_loop() -> None:
    """Фоновый async loop для периодической проверки зомби-задач."""
    logger.info(
        f"Zombie detector запущен (interval={ZOMBIE_CHECK_INTERVAL}s, dynamic threshold)"
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
            await asyncio.sleep(60)
