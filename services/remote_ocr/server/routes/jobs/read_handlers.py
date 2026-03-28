"""Обработчики чтения задач OCR"""
import json
import os
import uuid as _uuid
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, Query

from services.remote_ocr.server.logging_config import get_logger
from services.remote_ocr.server.routes.common import (
    get_file_icon,
    get_r2_storage,
    require_job,
)
from services.remote_ocr.server.storage import (
    get_job_file_by_type,
    job_to_dict,
    list_jobs,
    list_jobs_changed_since,
)

_logger = get_logger(__name__)


def _job_to_list_item(j) -> dict:
    """Сериализация Job в dict для списка задач."""
    return {
        "id": j.id,
        "status": j.status,
        "progress": j.progress,
        "document_name": j.document_name,
        "task_name": j.task_name,
        "document_id": j.document_id,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
        "error_message": j.error_message,
        "node_id": j.node_id,
        "status_message": j.status_message,
        "priority": j.priority,
    }


def list_jobs_handler(
    document_id: Optional[str] = None,
    since: Optional[str] = Query(None, description="ISO timestamp — вернуть только изменения после этого времени"),
) -> dict:
    """Получить список задач. При since — только изменённые."""

    if since:
        _logger.debug(
            f"Polling changes since={since}",
            extra={"event": "jobs_poll", "action": "poll"},
        )
        jobs = list_jobs_changed_since(since)
    else:
        _logger.info(
            "Запрос списка задач",
            extra={"event": "jobs_list_request", "action": "list", "document_id": document_id},
        )
        jobs = list_jobs(document_id)

    return {
        "jobs": [_job_to_list_item(j) for j in jobs],
        "server_time": datetime.utcnow().isoformat(),
    }


def get_job_handler(
    job_id: str,
) -> dict:
    """Получить информацию о задаче"""

    try:
        _uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Invalid job_id: {job_id}")

    job = require_job(job_id)

    return job_to_dict(job)


def get_job_details_handler(
    job_id: str,
) -> dict:
    """Получить детальную информацию о задаче"""

    job = require_job(job_id, with_files=True, with_settings=True)

    result = job_to_dict(job)

    blocks_file = get_job_file_by_type(job_id, "blocks")
    if blocks_file:
        try:
            r2 = get_r2_storage()
            blocks_text = r2.download_text(blocks_file.r2_key)
            if blocks_text:
                blocks = json.loads(blocks_text)

                text_count = sum(1 for b in blocks if b.get("block_type") == "text")
                image_count = sum(1 for b in blocks if b.get("block_type") == "image")
                stamp_count = sum(1 for b in blocks if b.get("block_type") == "stamp")

                result["block_stats"] = {
                    "total": len(blocks),
                    "text": text_count,
                    "image": image_count,
                    "stamp": stamp_count,
                }
        except Exception as e:
            _logger.warning(f"Failed to load blocks from R2: {e}")

    if job.settings:
        result["job_settings"] = {
            "text_model": job.settings.text_model,
            "image_model": job.settings.image_model,
            "stamp_model": job.settings.stamp_model,
        }
    else:
        result["job_settings"] = {}

    r2_public_url = os.getenv("R2_PUBLIC_URL")
    if r2_public_url and job.r2_prefix:
        base_url = r2_public_url.rstrip("/")
        result["r2_base_url"] = f"{base_url}/{job.r2_prefix}"

        result["r2_files"] = [
            {
                "name": f.file_name,
                "path": f.r2_key.replace(f"{job.r2_prefix}/", ""),
                "icon": get_file_icon(f.file_type),
            }
            for f in job.files
        ]
    else:
        result["r2_base_url"] = None
        result["r2_files"] = []

    return result


def download_result_handler(
    job_id: str,
) -> dict:
    """Получить ссылку на результат"""
    _logger.info(
        f"Запрос скачивания результата: {job_id}",
        extra={"event": "job_download_result", "action": "download", "job_id": job_id},
    )

    job = require_job(job_id)

    if job.status not in ("done", "partial"):
        raise HTTPException(
            status_code=400, detail=f"Job not ready, status: {job.status}"
        )

    result_file = get_job_file_by_type(job_id, "result_zip")
    if not result_file:
        raise HTTPException(status_code=404, detail="Result file not found")

    try:
        r2 = get_r2_storage()
        url = r2.generate_presigned_url(result_file.r2_key, expiration=3600)
        return {"download_url": url, "file_name": result_file.file_name}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to generate download URL: {e}"
        )
