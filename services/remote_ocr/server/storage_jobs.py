"""CRUD операции для задач OCR — in-memory кеширование (без Redis)."""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from typing import Any, List, Optional

from .logging_config import get_logger
from .storage_client import get_client
from .storage_models import Job

logger = get_logger(__name__)

# In-memory TTL кеш (заменяет Redis)
_cache_lock = threading.Lock()
_jobs_cache: dict[str, tuple[float, list]] = {}  # key -> (expire_at, data)
_pause_cache: dict[str, tuple[float, bool]] = {}  # job_id -> (expire_at, is_paused)

JOBS_CACHE_TTL = 5  # секунд
PAUSE_CACHE_TTL = 15  # секунд


def _invalidate_jobs_cache() -> None:
    """Инвалидирует весь кеш list_jobs."""
    with _cache_lock:
        _jobs_cache.clear()


def _set_pause_cache(job_id: str, is_paused: bool) -> None:
    """Set pause status in cache."""
    with _cache_lock:
        _pause_cache[job_id] = (time.time() + PAUSE_CACHE_TTL, is_paused)


def _get_pause_cache(job_id: str) -> Optional[bool]:
    """Get pause status from cache. Returns None if not cached/expired."""
    with _cache_lock:
        entry = _pause_cache.get(job_id)
        if entry and entry[0] > time.time():
            return entry[1]
        _pause_cache.pop(job_id, None)
    return None


def _invalidate_pause_cache(job_id: str) -> None:
    """Invalidate pause cache for a job."""
    with _cache_lock:
        _pause_cache.pop(job_id, None)


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
    job_id: str, with_settings: bool = False, **_kwargs
) -> Optional[Job]:
    """Получить задачу по ID.

    Args:
        with_settings: загрузить настройки из phase_data.
        **_kwargs: игнорируемые аргументы для обратной совместимости (with_files).
    """
    client = get_client()
    result = client.table("jobs").select("*").eq("id", job_id).execute()

    if not result.data:
        return None

    job = _row_to_job(result.data[0])

    if with_settings:
        # Settings хранятся inline в phase_data — парсим без дополнительного запроса
        phase_data = result.data[0].get("phase_data") or {}
        s = phase_data.get("settings")
        if s:
            from .storage_models import JobSettings
            job.settings = JobSettings(
                job_id=job_id,
                text_model=s.get("text_model", ""),
                image_model=s.get("image_model", ""),
                stamp_model=s.get("stamp_model", ""),
                is_correction_mode=s.get("is_correction_mode", False),
            )

    return job


def list_jobs(document_id: Optional[str] = None) -> List[Job]:
    """Получить список задач (с in-memory кешированием)."""
    cache_key = f"doc:{document_id}" if document_id else "all"

    # Проверяем кеш
    with _cache_lock:
        entry = _jobs_cache.get(cache_key)
        if entry and entry[0] > time.time():
            return [_row_to_job(row) for row in entry[1]]

    # Запрос к БД
    client = get_client()
    query = client.table("jobs").select("*")

    if document_id:
        query = query.eq("document_id", document_id)

    result = query.order("priority", desc=False).order("created_at", desc=True).execute()

    # Сохраняем в кеш
    with _cache_lock:
        _jobs_cache[cache_key] = (time.time() + JOBS_CACHE_TTL, result.data)

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
    """Инвалидировать кеш паузы для задачи (public API)."""
    _invalidate_pause_cache(job_id)


def set_pause_cache(job_id: str, is_paused: bool) -> None:
    """Установить статус паузы в кеш (public API)."""
    _set_pause_cache(job_id, is_paused)


def is_job_paused(job_id: str) -> bool:
    """Проверить, поставлена ли задача на паузу (с in-memory кешированием)."""
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
