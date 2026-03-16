"""CRUD операции для задач OCR"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, List, Optional

from .logging_config import get_logger
from .queue_checker import _get_redis_client
from .storage_client import get_client
from .storage_models import Job

logger = get_logger(__name__)

# Redis кеш для list_jobs() - TTL 5 секунд
JOBS_CACHE_TTL = 5
JOBS_CACHE_PREFIX = "jobs:list:"

# Redis кеш для is_job_paused() - TTL 15 секунд
PAUSE_CACHE_TTL = 15
PAUSE_CACHE_PREFIX = "job:paused:"


def _get_jobs_cache_key(document_id: Optional[str]) -> str:
    """Формирует ключ кеша для list_jobs"""
    if document_id:
        return f"{JOBS_CACHE_PREFIX}doc:{document_id}"
    return f"{JOBS_CACHE_PREFIX}all"


def _invalidate_jobs_cache() -> None:
    """Инвалидирует весь кеш list_jobs"""
    try:
        client = _get_redis_client()
        keys = client.keys(f"{JOBS_CACHE_PREFIX}*")
        if keys:
            client.delete(*keys)
            logger.debug(f"Invalidated {len(keys)} jobs cache keys")
    except Exception as e:
        logger.warning(f"Failed to invalidate jobs cache: {e}")


def _get_pause_cache_key(job_id: str) -> str:
    """Get Redis key for pause status cache"""
    return f"{PAUSE_CACHE_PREFIX}{job_id}"


def _set_pause_cache(job_id: str, is_paused: bool) -> None:
    """Set pause status in Redis cache"""
    try:
        client = _get_redis_client()
        key = _get_pause_cache_key(job_id)
        client.setex(key, PAUSE_CACHE_TTL, "1" if is_paused else "0")
    except Exception as e:
        logger.debug(f"Failed to set pause cache: {e}")


def _get_pause_cache(job_id: str) -> Optional[bool]:
    """Get pause status from Redis cache. Returns None if not cached."""
    try:
        client = _get_redis_client()
        key = _get_pause_cache_key(job_id)
        cached = client.get(key)
        if cached is not None:
            return cached == "1"
    except Exception as e:
        logger.debug(f"Failed to get pause cache: {e}")
    return None


def _invalidate_pause_cache(job_id: str) -> None:
    """Invalidate pause cache for a job"""
    try:
        client = _get_redis_client()
        key = _get_pause_cache_key(job_id)
        client.delete(key)
    except Exception as e:
        logger.debug(f"Failed to invalidate pause cache: {e}")


def create_job(
    document_id: str,
    document_name: str,
    task_name: str,
    engine: str,
    r2_prefix: str,
    client_id: str,
    status: str = "queued",
    node_id: Optional[str] = None,
) -> Job:
    """Создать новую задачу"""
    from .storage_jobs_queue import _next_queue_priority

    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    priority = _next_queue_priority() if status == "queued" else 0

    job = Job(
        id=job_id,
        document_id=document_id,
        document_name=document_name,
        task_name=task_name,
        status=status,
        progress=0.0,
        created_at=now,
        updated_at=now,
        error_message=None,
        engine=engine,
        r2_prefix=r2_prefix,
        client_id=client_id,
        node_id=node_id,
        priority=priority,
    )

    client = get_client()
    insert_data = {
        "id": job.id,
        "document_id": job.document_id,
        "document_name": job.document_name,
        "task_name": job.task_name,
        "status": job.status,
        "progress": job.progress,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "error_message": job.error_message,
        "engine": job.engine,
        "r2_prefix": job.r2_prefix,
        "client_id": job.client_id,
        "priority": job.priority,
    }
    if node_id:
        insert_data["node_id"] = node_id

    client.table("jobs").insert(insert_data).execute()

    _invalidate_jobs_cache()
    return job


def get_job(
    job_id: str, with_files: bool = False, with_settings: bool = False
) -> Optional[Job]:
    """Получить задачу по ID"""
    from .storage_files import get_job_files
    from .storage_settings import get_job_settings

    client = get_client()
    result = client.table("jobs").select("*").eq("id", job_id).execute()

    if not result.data:
        return None

    job = _row_to_job(result.data[0])

    if with_files:
        job.files = get_job_files(job_id)
    if with_settings:
        job.settings = get_job_settings(job_id)

    return job


def list_jobs(document_id: Optional[str] = None) -> List[Job]:
    """Получить список задач (с Redis кешированием)"""
    cache_key = _get_jobs_cache_key(document_id)

    # Проверяем кеш
    try:
        redis_client = _get_redis_client()
        cached = redis_client.get(cache_key)
        if cached:
            jobs_data = json.loads(cached)
            return [_row_to_job(row) for row in jobs_data]
    except Exception as e:
        logger.debug(f"Cache miss or error: {e}")

    # Запрос к БД
    client = get_client()
    query = client.table("jobs").select("*")

    if document_id:
        query = query.eq("document_id", document_id)

    result = query.order("priority", desc=False).order("created_at", desc=True).execute()

    # Сохраняем в кеш
    try:
        redis_client = _get_redis_client()
        redis_client.setex(cache_key, JOBS_CACHE_TTL, json.dumps(result.data))
    except Exception as e:
        logger.debug(f"Failed to cache jobs: {e}")

    return [_row_to_job(row) for row in result.data]


def list_jobs_changed_since(since: str) -> List[Job]:
    """Получить задачи, изменённые после указанного времени (ISO timestamp)"""
    client = get_client()
    result = (
        client.table("jobs")
        .select("*")
        .gt("updated_at", since)
        .order("updated_at", desc=True)
        .execute()
    )
    return [_row_to_job(row) for row in result.data]


def update_job_status(
    job_id: str,
    status: str,
    progress: Optional[float] = None,
    error_message: Optional[str] = None,
    r2_prefix: Optional[str] = None,
    status_message: Optional[str] = None,
) -> None:
    """Обновить статус задачи"""
    now = datetime.utcnow().isoformat()

    updates: dict[str, Any] = {"status": status, "updated_at": now}

    if progress is not None:
        updates["progress"] = progress
    if error_message is not None:
        updates["error_message"] = error_message
    if r2_prefix is not None:
        updates["r2_prefix"] = r2_prefix
    if status_message is not None:
        updates["status_message"] = status_message

    client = get_client()
    client.table("jobs").update(updates).eq("id", job_id).execute()
    _invalidate_jobs_cache()


def update_job_engine(job_id: str, engine: str) -> None:
    """Обновить engine задачи и перевести в queued"""
    now = datetime.utcnow().isoformat()
    client = get_client()
    client.table("jobs").update(
        {"engine": engine, "status": "queued", "updated_at": now}
    ).eq("id", job_id).execute()


def update_job_task_name(job_id: str, task_name: str) -> bool:
    """Обновить название задачи"""
    now = datetime.utcnow().isoformat()
    client = get_client()
    result = (
        client.table("jobs")
        .update({"task_name": task_name, "updated_at": now})
        .eq("id", job_id)
        .execute()
    )
    return len(result.data) > 0


def delete_job(job_id: str) -> bool:
    """Удалить задачу из БД (каскадно удалит files и settings)"""
    client = get_client()
    result = client.table("jobs").delete().eq("id", job_id).execute()
    _invalidate_jobs_cache()
    return len(result.data) > 0


def reset_job_for_restart(job_id: str) -> bool:
    """Сбросить задачу для повторного запуска.

    Также сбрасывает retry_count и started_at для корректного
    отслеживания лимитов при повторном запуске.
    """
    now = datetime.utcnow().isoformat()
    client = get_client()
    result = (
        client.table("jobs")
        .update(
            {
                "status": "queued",
                "progress": 0,
                "error_message": None,
                "updated_at": now,
                "retry_count": 0,
                "started_at": None,
            }
        )
        .eq("id", job_id)
        .execute()
    )
    _invalidate_jobs_cache()
    return len(result.data) > 0


def pause_job(job_id: str) -> bool:
    """Поставить задачу на паузу"""
    now = datetime.utcnow().isoformat()
    client = get_client()

    result = (
        client.table("jobs")
        .update({"status": "paused", "updated_at": now})
        .eq("id", job_id)
        .eq("status", "queued")
        .execute()
    )

    if result.data:
        _invalidate_jobs_cache()
        _set_pause_cache(job_id, True)
        return True

    result = (
        client.table("jobs")
        .update({"status": "paused", "updated_at": now})
        .eq("id", job_id)
        .eq("status", "processing")
        .execute()
    )

    if result.data:
        _invalidate_jobs_cache()
        _set_pause_cache(job_id, True)
    return len(result.data) > 0


def resume_job(job_id: str) -> bool:
    """Возобновить задачу"""
    now = datetime.utcnow().isoformat()
    client = get_client()
    result = (
        client.table("jobs")
        .update({"status": "queued", "updated_at": now})
        .eq("id", job_id)
        .eq("status", "paused")
        .execute()
    )
    if result.data:
        _invalidate_jobs_cache()
        _set_pause_cache(job_id, False)
    return len(result.data) > 0


def invalidate_pause_cache(job_id: str) -> None:
    """Инвалидировать Redis-кеш паузы для задачи (public API)"""
    _invalidate_pause_cache(job_id)


def is_job_paused(job_id: str) -> bool:
    """Проверить, поставлена ли задача на паузу (с Redis кешированием)"""
    # Check cache first
    cached = _get_pause_cache(job_id)
    if cached is not None:
        return cached

    # Cache miss - query DB
    try:
        job = get_job(job_id)
    except Exception as exc:
        logger.warning(f"is_job_paused: ошибка запроса к БД для {job_id}: {exc}")
        return False
    is_paused = job.status in ("paused", "cancelled") if job else False

    # Update cache
    _set_pause_cache(job_id, is_paused)

    return is_paused


def _row_to_job(row: dict) -> Job:
    return Job(
        id=row["id"],
        document_id=row["document_id"],
        document_name=row["document_name"],
        task_name=row.get("task_name", ""),
        status=row["status"],
        progress=row["progress"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        error_message=row.get("error_message"),
        engine=row.get("engine", ""),
        r2_prefix=row.get("r2_prefix", ""),
        client_id=row.get("client_id", ""),
        node_id=row.get("node_id"),
        status_message=row.get("status_message"),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        block_stats=row.get("block_stats"),
        phase_data=row.get("phase_data"),
        retry_count=row.get("retry_count", 0),
        priority=row.get("priority", 0),
        celery_task_id=row.get("celery_task_id"),
    )


# Re-export queue-операций для обратной совместимости
from .storage_jobs_queue import (  # noqa: F401
    find_adjacent_queued_job,
    increment_retry_count,
    reset_job_retry_count,
    save_celery_task_id,
    set_job_started_at,
    swap_job_priorities,
)
