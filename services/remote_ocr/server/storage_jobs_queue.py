"""Операции очередности и retry для задач OCR"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .logging_config import get_logger
from .storage_client import get_client
from .storage_models import Job

logger = get_logger(__name__)


def _invalidate_jobs_cache() -> None:
    """Инвалидирует весь кеш list_jobs (импорт из storage_jobs)"""
    from .storage_jobs import _invalidate_jobs_cache as _invalidate
    _invalidate()


def _row_to_job(row: dict) -> Job:
    """Конвертация строки БД в Job (импорт из storage_jobs)"""
    from .storage_jobs import _row_to_job as _convert
    return _convert(row)


def _next_queue_priority() -> int:
    """Получить следующий priority для новой задачи в очереди.

    Новая задача встаёт в конец очереди (наибольший priority).
    """
    try:
        client = get_client()
        result = (
            client.table("jobs")
            .select("priority")
            .eq("status", "queued")
            .order("priority", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]["priority"] + 1
    except Exception as e:
        logger.warning(f"Failed to get next queue priority: {e}")
    return 0


def increment_retry_count(job_id: str) -> int:
    """Увеличить счётчик попыток выполнения задачи.

    Возвращает новое значение retry_count.
    Используется для защиты от бесконечного зацикливания при таймаутах Celery.
    """
    now = datetime.utcnow().isoformat()
    client = get_client()

    # Получаем текущее значение
    result = client.table("jobs").select("retry_count").eq("id", job_id).execute()
    if not result.data:
        return 0

    current_count = result.data[0].get("retry_count", 0) or 0
    new_count = current_count + 1

    # Обновляем
    client.table("jobs").update({
        "retry_count": new_count,
        "updated_at": now
    }).eq("id", job_id).execute()

    logger.info(f"Job {job_id}: retry_count увеличен до {new_count}")
    _invalidate_jobs_cache()

    return new_count


def set_job_started_at(job_id: str) -> None:
    """Установить время начала обработки задачи.

    Вызывается только при первом запуске задачи (когда started_at ещё не установлен).
    """
    now = datetime.utcnow().isoformat()
    client = get_client()

    client.table("jobs").update({
        "started_at": now,
        "updated_at": now
    }).eq("id", job_id).execute()

    logger.info(f"Job {job_id}: started_at установлен в {now}")
    _invalidate_jobs_cache()


def reset_job_retry_count(job_id: str) -> None:
    """Сбросить счётчик попыток и время начала (при ручном рестарте задачи).
    """
    now = datetime.utcnow().isoformat()
    client = get_client()

    client.table("jobs").update({
        "retry_count": 0,
        "started_at": None,
        "updated_at": now
    }).eq("id", job_id).execute()

    logger.info(f"Job {job_id}: retry_count и started_at сброшены")
    _invalidate_jobs_cache()


def save_celery_task_id(job_id: str, celery_task_id: str) -> None:
    """Сохранить ID Celery задачи для revoke при reorder."""
    now = datetime.utcnow().isoformat()
    client = get_client()
    client.table("jobs").update({
        "celery_task_id": celery_task_id,
        "updated_at": now,
    }).eq("id", job_id).execute()


def find_adjacent_queued_job(job_id: str, direction: str) -> Optional[Job]:
    """Найти соседнюю queued-задачу для swap.

    Очередь сортируется по priority ASC, created_at ASC.
    direction="up" → задача с меньшим priority (или раньше создана при равном).
    direction="down" → задача с большим priority (или позже создана при равном).
    """
    client = get_client()
    result = (
        client.table("jobs")
        .select("*")
        .eq("status", "queued")
        .order("priority", desc=False)
        .order("created_at", desc=False)
        .execute()
    )

    queued_jobs = [_row_to_job(row) for row in result.data]

    current_idx = None
    for i, j in enumerate(queued_jobs):
        if j.id == job_id:
            current_idx = i
            break

    if current_idx is None:
        return None

    if direction == "up" and current_idx > 0:
        return queued_jobs[current_idx - 1]
    elif direction == "down" and current_idx < len(queued_jobs) - 1:
        return queued_jobs[current_idx + 1]

    return None


def swap_job_priorities(
    job_a_id: str, priority_a: int, job_b_id: str, priority_b: int
) -> None:
    """Обменять priority двух задач.

    Если priority совпадают, разводим: первая получает меньший,
    вторая — больший (чтобы гарантировать различие).
    """
    now = datetime.utcnow().isoformat()
    client = get_client()

    if priority_a == priority_b:
        new_a = priority_a - 1
        new_b = priority_b + 1
    else:
        new_a = priority_b
        new_b = priority_a

    client.table("jobs").update({
        "priority": new_a, "updated_at": now,
    }).eq("id", job_a_id).execute()

    client.table("jobs").update({
        "priority": new_b, "updated_at": now,
    }).eq("id", job_b_id).execute()

    _invalidate_jobs_cache()
    logger.info(
        f"Swapped priorities: {job_a_id[:8]}→{new_a}, {job_b_id[:8]}→{new_b}"
    )
