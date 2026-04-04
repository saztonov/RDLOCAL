"""Обработчик переупорядочивания задач в очереди OCR"""
from __future__ import annotations

from typing import Optional

from fastapi import Form, HTTPException
from pydantic import BaseModel

from services.remote_ocr.server.logging_config import get_logger
from services.remote_ocr.server.routes.common import require_job
from services.remote_ocr.server.routes.jobs.update_handlers import (
    _get_block_count_for_job,
)
from services.remote_ocr.server.storage_jobs import (
    find_adjacent_queued_job,
    swap_job_priorities,
)
from services.remote_ocr.server.task_dispatch import dispatch_ocr_task

_logger = get_logger(__name__)


def _revoke_and_resubmit(job_id: str, old_celery_task_id: Optional[str],
                         new_priority: int) -> str:
    """Отменить старую задачу и переотправить с новым приоритетом.

    Returns:
        job_id.
    """
    # В embedded режиме cancel + resubmit
    from services.remote_ocr.server.embedded_job_manager_singleton import get_job_manager
    manager = get_job_manager()
    manager.cancel(job_id)

    task_id = dispatch_ocr_task(job_id, _get_block_count_for_job(job_id), new_priority)
    _logger.info(
        f"Resubmitted job {job_id[:8]} with priority={new_priority}, new task={task_id}"
    )
    return task_id


class ReorderRequest(BaseModel):
    direction: str


def reorder_job_handler(
    job_id: str,
    body: ReorderRequest,
) -> dict:
    """Переместить задачу вверх/вниз в очереди обработки."""

    direction = body.direction

    if direction not in ("up", "down"):
        raise HTTPException(
            status_code=400, detail=f"Invalid direction: {direction}"
        )

    job = require_job(job_id)

    if job.status != "queued":
        raise HTTPException(
            status_code=400,
            detail=f"Can only reorder queued jobs, current: {job.status}",
        )

    adjacent = find_adjacent_queued_job(job_id, direction)
    if adjacent is None:
        raise HTTPException(
            status_code=400,
            detail="No adjacent queued job to swap with",
        )

    _logger.info(
        f"Reorder: {job_id[:8]} ({job.priority}) "
        f"{'↑' if direction == 'up' else '↓'} "
        f"{adjacent.id[:8]} ({adjacent.priority})",
        extra={"event": "job_lifecycle", "action": "reorder", "job_id": job_id},
    )

    # Обмен приоритетов (swap_job_priorities разводит одинаковые priority)
    swap_job_priorities(job.id, job.priority, adjacent.id, adjacent.priority)

    # Определяем новые приоритеты после swap
    if job.priority == adjacent.priority:
        # При равных: target получил priority-1, adjacent получил priority+1
        new_target_priority = job.priority - 1 if direction == "up" else job.priority + 1
        new_adjacent_priority = adjacent.priority + 1 if direction == "up" else adjacent.priority - 1
    else:
        new_target_priority = adjacent.priority
        new_adjacent_priority = job.priority

    # Revoke + resubmit: сначала задачу с МЕНЬШИМ priority (она первая в очереди)
    jobs_to_resubmit = [
        (job.id, job.celery_task_id, new_target_priority),
        (adjacent.id, adjacent.celery_task_id, new_adjacent_priority),
    ]
    jobs_to_resubmit.sort(key=lambda x: x[2])

    for jid, old_tid, new_p in jobs_to_resubmit:
        _revoke_and_resubmit(jid, old_tid, new_p)

    return {
        "ok": True,
        "job_id": job_id,
        "swapped_with": adjacent.id,
        "direction": direction,
    }
