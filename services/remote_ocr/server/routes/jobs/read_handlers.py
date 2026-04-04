"""Обработчики чтения задач OCR"""
import json
import uuid as _uuid
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, Query

from services.remote_ocr.server.local_storage import is_local_path, local_input_dir
from services.remote_ocr.server.logging_config import get_logger
from services.remote_ocr.server.node_storage.ocr_registry import _load_annotation_from_db
from services.remote_ocr.server.routes.common import (
    require_job,
)
from services.remote_ocr.server.storage import (
    job_to_dict,
    list_jobs,
    list_jobs_changed_since,
)

_logger = get_logger(__name__)


def _extract_blocks_list(data: dict | list) -> list:
    """Извлечь плоский список блоков из annotation data.

    Поддерживает два формата:
    - Плоский список блоков (legacy)
    - Document-структура с pages[].blocks[]
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "pages" in data:
        blocks: list = []
        for page in data.get("pages", []):
            blocks.extend(page.get("blocks", []))
        return blocks
    return []


def _compute_block_stats(blocks: list) -> dict:
    """Подсчитать статистику блоков по типам."""
    text_count = sum(1 for b in blocks if b.get("block_type") == "text")
    image_count = sum(1 for b in blocks if b.get("block_type") == "image")
    stamp_count = sum(1 for b in blocks if b.get("block_type") == "stamp")
    return {
        "total": len(blocks),
        "text": text_count,
        "image": image_count,
        "stamp": stamp_count,
    }


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

    job = require_job(job_id, with_settings=True)

    result = job_to_dict(job)

    # Загрузка block_stats: Supabase annotation (node-backed) → локальный файл (standalone)
    block_stats_loaded = False
    if job.node_id:
        try:
            ann_data = _load_annotation_from_db(job.node_id)
            if ann_data is not None:
                blocks = _extract_blocks_list(ann_data)
                result["block_stats"] = _compute_block_stats(blocks)
                block_stats_loaded = True
        except Exception as e:
            _logger.warning(f"Failed to load blocks from Supabase for node {job.node_id}: {e}")

    if not block_stats_loaded and is_local_path(job.r2_prefix):
        try:
            blocks_path = local_input_dir(job_id) / "blocks.json"
            if blocks_path.exists():
                blocks_data = json.loads(blocks_path.read_text(encoding="utf-8"))
                blocks = _extract_blocks_list(blocks_data)
                result["block_stats"] = _compute_block_stats(blocks)
        except Exception as e:
            _logger.warning(f"Failed to load blocks from disk: {e}")

    if job.settings:
        result["job_settings"] = {
            "text_model": job.settings.text_model,
            "image_model": job.settings.image_model,
            "stamp_model": job.settings.stamp_model,
        }
    else:
        result["job_settings"] = {}

    result["r2_base_url"] = None
    result["r2_files"] = []

    return result


def download_result_handler(
    job_id: str,
) -> dict:
    """Получить ссылку на результат (deprecated — результаты доступны через node tree)."""
    raise HTTPException(
        status_code=410,
        detail="Result download via this endpoint is deprecated. Use node tree to access results.",
    )
