"""Local OCR FastAPI сервер — без Celery/Redis.

Реализует подмножество API remote-сервера, достаточное для работы
Использует multiprocessing.Process для OCR.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Task Manager (singleton) ─────────────────────────────────────────

_task_manager = None


def _get_task_manager():
    global _task_manager
    if _task_manager is None:
        from services.local_ocr.task_runner import LocalTaskManager
        _task_manager = LocalTaskManager(max_workers=1)
    return _task_manager


# ── Lifespan ─────────────────────────────────────────────────────────

_poll_task: Optional[asyncio.Task] = None


async def _poll_loop():
    """Фоновый polling progress из worker-процессов."""
    from services.remote_ocr.server.storage import update_job_status

    manager = _get_task_manager()
    while True:
        try:
            messages = manager.poll()
            for msg in messages:
                job_id = msg.get("job_id", "")
                msg_type = msg.get("type", "")
                if msg_type == "progress":
                    progress = msg.get("progress", 0)
                    status_message = msg.get("message", "")
                    update_job_status(
                        job_id, "processing",
                        progress=progress,
                        status_message=status_message,
                    )
                elif msg_type == "error":
                    error_msg = msg.get("message", "Unknown error")
                    update_job_status(
                        job_id, "error",
                        error_message=error_msg,
                        status_message="❌ Ошибка обработки",
                    )
                # "done" — finalize() уже обновил статус в Supabase
        except Exception:
            logger.exception("Poll loop error")
        await asyncio.sleep(0.5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown."""
    from services.remote_ocr.server.storage_client import init_db

    logger.info("Local OCR Service starting...")
    init_db()

    global _poll_task
    _poll_task = asyncio.create_task(_poll_loop())

    yield

    # Shutdown
    if _poll_task:
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass

    manager = _get_task_manager()
    manager.shutdown()
    logger.info("Local OCR Service stopped")


# ── FastAPI App ──────────────────────────────────────────────────────

app = FastAPI(title="Local OCR Service", lifespan=lifespan)


# ── Health ───────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True, "service": "local-ocr"}


# ── Jobs: List ───────────────────────────────────────────────────────

@app.get("/jobs")
def list_jobs_endpoint(
    document_id: Optional[str] = None,
    since: Optional[str] = Query(None),
):
    """Список задач (с delta polling через ?since=)."""
    from services.remote_ocr.server.storage import list_jobs, list_jobs_changed_since

    if since:
        jobs = list_jobs_changed_since(since)
    else:
        jobs = list_jobs(document_id)

    return {
        "jobs": [_job_to_list_item(j) for j in jobs],
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


def _job_to_list_item(j) -> dict:
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


# ── Jobs: Create (node-backed) ──────────────────────────────────────

class CreateNodeJobRequest(BaseModel):
    document_id: str
    document_name: str
    client_id: str
    task_name: str = ""
    engine: str = "lmstudio"
    text_model: str = ""
    image_model: str = ""
    stamp_model: str = ""
    node_id: str
    is_correction_mode: str = "false"


@app.post("/jobs/node")
def create_node_job(body: CreateNodeJobRequest):
    """Создать OCR-задачу для node-backed документа."""
    from services.remote_ocr.server.node_storage.ocr_registry import _load_annotation_from_db
    from services.remote_ocr.server.r2_keys import blocks_key as make_blocks_key
    from services.remote_ocr.server.routes.common import get_r2_sync_client
    from services.remote_ocr.server.storage import (
        add_job_file,
        create_job,
        delete_job,
        get_node_pdf_r2_key,
        save_job_settings,
    )
    from services.remote_ocr.server.timeout_utils import count_blocks_from_data

    if body.engine not in ("lmstudio", "chandra"):
        raise HTTPException(status_code=400, detail=f"Unsupported engine: {body.engine}")

    # Загрузка блоков из Supabase
    blocks_data = _load_annotation_from_db(body.node_id)
    if blocks_data is None:
        raise HTTPException(
            status_code=400,
            detail=f"No annotation found in Supabase for node {body.node_id}",
        )

    # PDF в R2
    pdf_r2_key = get_node_pdf_r2_key(body.node_id)
    if not pdf_r2_key:
        raise HTTPException(
            status_code=400,
            detail=f"No PDF registered for node {body.node_id}",
        )

    try:
        s3_check, bucket_check = get_r2_sync_client()
        s3_check.head_object(Bucket=bucket_check, Key=pdf_r2_key)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=f"PDF not found in R2 for node {body.node_id}",
        )

    r2_prefix = str(PurePosixPath(pdf_r2_key).parent)

    # Создание job в Supabase
    job = create_job(
        document_id=body.document_id,
        document_name=body.document_name,
        task_name=body.task_name,
        engine=body.engine,
        r2_prefix=r2_prefix,
        client_id=body.client_id,
        status="queued",
        node_id=body.node_id,
    )

    is_correction = body.is_correction_mode.lower() == "true"
    save_job_settings(
        job.id, body.text_model, body.image_model, body.stamp_model, is_correction,
    )

    # Upload blocks to R2
    try:
        s3_client, bucket_name = get_r2_sync_client()
        blocks_bytes = json.dumps(blocks_data, ensure_ascii=False, indent=2).encode("utf-8")
        actual_blocks_key = make_blocks_key(r2_prefix, body.document_name, is_node=True)
        s3_client.put_object(
            Bucket=bucket_name,
            Key=actual_blocks_key,
            Body=blocks_bytes,
            ContentType="application/json",
        )
        add_job_file(job.id, "pdf", pdf_r2_key, body.document_name, 0)
        add_job_file(job.id, "blocks", actual_blocks_key, PurePosixPath(actual_blocks_key).name, len(blocks_bytes))
    except Exception as e:
        logger.error(f"R2 upload failed: {e}")
        delete_job(job.id)
        raise HTTPException(status_code=500, detail=f"Failed to upload files to R2: {e}")

    # Запуск OCR в отдельном процессе (вместо Celery dispatch)
    manager = _get_task_manager()
    manager.submit(job.id)

    logger.info(f"Job created: {job.id} for node {body.node_id}")

    return {
        "id": job.id,
        "status": job.status,
        "progress": job.progress,
        "document_id": job.document_id,
        "document_name": job.document_name,
        "task_name": job.task_name,
    }


# ── Jobs: Get ────────────────────────────────────────────────────────

@app.get("/jobs/{job_id}")
def get_job_endpoint(job_id: str):
    """Получить информацию о задаче."""
    from services.remote_ocr.server.storage import get_job, job_to_dict

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_to_dict(job)


@app.get("/jobs/{job_id}/details")
def get_job_details_endpoint(job_id: str):
    """Получить детальную информацию о задаче."""
    from services.remote_ocr.server.storage import get_job, job_to_dict

    job = get_job(job_id, with_files=True, with_settings=True)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_to_dict(job)


# ── Jobs: Cancel ─────────────────────────────────────────────────────

@app.post("/jobs/{job_id}/cancel")
def cancel_job_endpoint(job_id: str):
    """Отменить задачу."""
    from services.remote_ocr.server.storage import get_job, update_job_status

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in ("queued", "processing"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job in status: {job.status}",
        )

    update_job_status(job_id, "cancelled", status_message="Отменено пользователем")

    # Signal process to stop
    manager = _get_task_manager()
    manager.cancel(job_id)

    return {"ok": True, "job_id": job_id, "status": "cancelled"}


# ── Jobs: Delete ─────────────────────────────────────────────────────

@app.delete("/jobs/{job_id}")
def delete_job_endpoint(job_id: str):
    """Удалить задачу."""
    from services.remote_ocr.server.storage import delete_job, get_job

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Node-backed jobs: не удаляем файлы из R2
    if not job.node_id and job.r2_prefix:
        try:
            from services.remote_ocr.server.routes.common import get_r2_sync_client
            s3_client, bucket_name = get_r2_sync_client()
            prefix = job.r2_prefix if job.r2_prefix.endswith("/") else f"{job.r2_prefix}/"
            paginator = s3_client.get_paginator("list_objects_v2")
            files_to_delete = []
            for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
                if "Contents" in page:
                    for obj in page["Contents"]:
                        files_to_delete.append({"Key": obj["Key"]})
            if files_to_delete:
                for i in range(0, len(files_to_delete), 1000):
                    batch = files_to_delete[i:i + 1000]
                    s3_client.delete_objects(Bucket=bucket_name, Delete={"Objects": batch})
        except Exception as e:
            logger.warning(f"Failed to delete R2 files: {e}")

    if not delete_job(job_id):
        raise HTTPException(status_code=500, detail="Failed to delete job")

    return {"ok": True, "deleted_job_id": job_id}
