"""Redis execution lock — защита от параллельной обработки одного job.

При duplicate delivery (visibility_timeout, requeue после SIGKILL) одна и та же
задача может быть доставлена дважды и исполняться параллельно в разных воркерах.
Execution lock предотвращает это: только первый worker получает lock, второй
завершается как "duplicate" до bootstrap/download/OCR.

Ключ: ocr:executing:{job_id}
Значение: celery_task_id (для валидации владельца)
TTL: max_task_timeout + запас (автоочистка при крашах)
"""
from __future__ import annotations

from .logging_config import get_logger
from .settings import settings

logger = get_logger(__name__)

_LOCK_PREFIX = "ocr:executing:"
# TTL = max возможный hard_timeout + запас 30 минут
_LOCK_TTL = settings.max_task_timeout + 600 + 1800


def _get_redis_client():
    """Переиспользуем Redis pool из lmstudio_lifecycle."""
    from .lmstudio_lifecycle import _get_redis_client as _get_client
    return _get_client()


def acquire_execution_lock(job_id: str, celery_task_id: str) -> bool:
    """Попытка захватить execution lock для job.

    Returns:
        True если lock захвачен (мы единственный исполнитель).
        False если lock уже существует (duplicate delivery).
    """
    key = f"{_LOCK_PREFIX}{job_id}"
    try:
        client = _get_redis_client()
        acquired = client.set(key, celery_task_id, nx=True, ex=_LOCK_TTL)
        if acquired:
            logger.info(
                f"Execution lock acquired: job={job_id[:8]}, task={celery_task_id[:8]}",
                extra={"event": "execution_lock_acquired", "job_id": job_id},
            )
            return True
        else:
            existing = client.get(key)
            logger.warning(
                f"Execution lock DENIED: job={job_id[:8]}, "
                f"task={celery_task_id[:8]}, held_by={existing}",
                extra={"event": "execution_lock_denied", "job_id": job_id},
            )
            return False
    except Exception as exc:
        # При ошибке Redis — разрешаем выполнение (fail open)
        logger.warning(f"Execution lock acquire failed (allowing): {exc}")
        return True


def release_execution_lock(job_id: str, celery_task_id: str) -> None:
    """Освободить execution lock (только если это наш lock).

    Безопасно: если lock принадлежит другому task_id — не удаляем.
    """
    if not celery_task_id:
        return
    key = f"{_LOCK_PREFIX}{job_id}"
    try:
        client = _get_redis_client()
        existing = client.get(key)
        if existing == celery_task_id:
            client.delete(key)
            logger.info(
                f"Execution lock released: job={job_id[:8]}",
                extra={"event": "execution_lock_released", "job_id": job_id},
            )
        elif existing:
            logger.debug(
                f"Execution lock NOT released: job={job_id[:8]}, "
                f"held_by={existing}, our_task={celery_task_id[:8]}"
            )
    except Exception as exc:
        logger.warning(f"Execution lock release failed: {exc}")


def force_release_execution_lock(job_id: str) -> None:
    """Принудительно удалить execution lock (для zombie detector)."""
    key = f"{_LOCK_PREFIX}{job_id}"
    try:
        client = _get_redis_client()
        deleted = client.delete(key)
        if deleted:
            logger.info(
                f"Execution lock force-released: job={job_id[:8]}",
                extra={"event": "execution_lock_force_released", "job_id": job_id},
            )
    except Exception as exc:
        logger.warning(f"Execution lock force-release failed: {exc}")
