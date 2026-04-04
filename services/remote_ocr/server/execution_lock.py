"""In-process execution lock — защита от параллельной обработки одного job.

Заменяет Redis-based lock на threading-based.
В embedded режиме (один процесс) этот lock предотвращает
повторный submit того же job_id.
"""
from __future__ import annotations

import threading

from .logging_config import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_executing: dict[str, str] = {}  # job_id -> task_id


def acquire_execution_lock(job_id: str, celery_task_id: str) -> bool:
    """Попытка захватить execution lock для job.

    Returns:
        True если lock захвачен.
        False если lock уже существует (duplicate).
    """
    with _lock:
        if job_id in _executing:
            existing = _executing[job_id]
            logger.warning(
                f"Execution lock DENIED: job={job_id[:8]}, "
                f"task={celery_task_id[:8]}, held_by={existing}",
                extra={"event": "execution_lock_denied", "job_id": job_id},
            )
            return False
        _executing[job_id] = celery_task_id
    logger.info(
        f"Execution lock acquired: job={job_id[:8]}, task={celery_task_id[:8]}",
        extra={"event": "execution_lock_acquired", "job_id": job_id},
    )
    return True


def release_execution_lock(job_id: str, celery_task_id: str) -> None:
    """Освободить execution lock (только если это наш lock)."""
    if not celery_task_id:
        return
    with _lock:
        existing = _executing.get(job_id)
        if existing == celery_task_id:
            del _executing[job_id]
            logger.info(
                f"Execution lock released: job={job_id[:8]}",
                extra={"event": "execution_lock_released", "job_id": job_id},
            )
        elif existing:
            logger.debug(
                f"Execution lock NOT released: job={job_id[:8]}, "
                f"held_by={existing}, our_task={celery_task_id[:8]}"
            )


def force_release_execution_lock(job_id: str) -> None:
    """Принудительно удалить execution lock (для zombie detector)."""
    with _lock:
        if job_id in _executing:
            del _executing[job_id]
            logger.info(
                f"Execution lock force-released: job={job_id[:8]}",
                extra={"event": "execution_lock_force_released", "job_id": job_id},
            )
