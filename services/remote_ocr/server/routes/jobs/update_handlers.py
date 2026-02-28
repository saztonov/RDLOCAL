"""Обработчики обновления задач OCR"""
import json
from typing import Optional

from fastapi import File, Form, Header, HTTPException, UploadFile

from services.remote_ocr.server.logging_config import get_logger
from services.remote_ocr.server.queue_checker import check_queue_capacity
from services.remote_ocr.server.routes.common import (
    check_api_key,
    get_r2_sync_client,
)
from services.remote_ocr.server.storage import (
    delete_job_files,
    get_job,
    get_job_files,
    get_node_pdf_r2_key,
    pause_job,
    reset_job_for_restart,
    resume_job,
    save_job_settings,
    update_job_engine,
    update_job_task_name,
)
from services.remote_ocr.server.storage_jobs import save_celery_task_id
from services.remote_ocr.server.tasks import run_ocr_task
from services.remote_ocr.server.timeout_utils import (
    calculate_dynamic_timeout,
    count_blocks_from_data,
    parse_blocks_json,
)

_logger = get_logger(__name__)


def _get_block_count_for_job(job_id: str) -> int:
    """Получить количество блоков для задачи из R2.

    Args:
        job_id: ID задачи

    Returns:
        Количество блоков (100 по умолчанию если не удалось получить)
    """
    try:
        files = get_job_files(job_id)
        blocks_file = next((f for f in files if f.file_type == "blocks"), None)

        if not blocks_file:
            _logger.warning(f"No blocks file for job {job_id}, using default timeout")
            return 100

        s3_client, bucket_name = get_r2_sync_client()
        response = s3_client.get_object(Bucket=bucket_name, Key=blocks_file.r2_key)
        content = response["Body"].read()
        blocks_data = parse_blocks_json(content)

        return count_blocks_from_data(blocks_data)
    except Exception as e:
        _logger.warning(f"Failed to get block count for job {job_id}: {e}")
        return 100


def update_job_handler(
    job_id: str,
    task_name: str = Form(...),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """Обновить название задачи"""
    check_api_key(x_api_key)

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if not update_job_task_name(job_id, task_name):
        raise HTTPException(status_code=500, detail="Failed to update job")

    return {"ok": True, "job_id": job_id, "task_name": task_name}


async def restart_job_handler(
    job_id: str,
    blocks_file: Optional[UploadFile] = File(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """Перезапустить задачу. Опционально обновить блоки."""
    check_api_key(x_api_key)

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    result_files = get_job_files(job_id)
    result_types = ["result_md", "result_zip", "crop"]

    try:
        s3_client, bucket_name = get_r2_sync_client()

        keys_to_delete = [f.r2_key for f in result_files if f.file_type in result_types]

        if keys_to_delete:
            for i in range(0, len(keys_to_delete), 1000):
                batch = keys_to_delete[i : i + 1000]
                delete_dict = {"Objects": [{"Key": key} for key in batch]}
                s3_client.delete_objects(Bucket=bucket_name, Delete=delete_dict)
            _logger.info(
                f"Deleted {len(keys_to_delete)} result files from R2 for job {job_id}"
            )

        delete_job_files(job_id, result_types)
    except Exception as e:
        _logger.warning(f"Failed to delete result files from R2: {e}")

    if blocks_file:
        try:
            blocks_json = (await blocks_file.read()).decode("utf-8")
            blocks_data = json.loads(blocks_json)
            blocks_bytes = json.dumps(blocks_data, ensure_ascii=False, indent=2).encode(
                "utf-8"
            )

            s3_client, bucket_name = get_r2_sync_client()

            if job.node_id:
                pdf_r2_key = get_node_pdf_r2_key(job.node_id)
                if pdf_r2_key:
                    from pathlib import PurePosixPath

                    r2_prefix = str(PurePosixPath(pdf_r2_key).parent)
                    doc_stem = PurePosixPath(job.document_name).stem
                    blocks_key = f"{r2_prefix}/{doc_stem}_annotation.json"
                else:
                    blocks_key = f"{job.r2_prefix}/annotation.json"
            else:
                blocks_key = f"{job.r2_prefix}/annotation.json"

            s3_client.put_object(
                Bucket=bucket_name,
                Key=blocks_key,
                Body=blocks_bytes,
                ContentType="application/json",
            )
            _logger.info(f"Updated blocks for job {job_id}: {blocks_key}")
        except Exception as e:
            _logger.error(f"Failed to update blocks: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid blocks: {e}")

    if not reset_job_for_restart(job_id):
        raise HTTPException(status_code=500, detail="Failed to reset job")

    can_accept, queue_size, max_size = check_queue_capacity()
    if not can_accept:
        raise HTTPException(
            status_code=503, detail=f"Queue full ({queue_size}/{max_size})"
        )

    # Рассчитываем динамический таймаут
    block_count = _get_block_count_for_job(job_id)
    soft_timeout, hard_timeout = calculate_dynamic_timeout(block_count)

    celery_result = run_ocr_task.apply_async(
        args=[job_id],
        priority=max(0, min(10, job.priority)),
        soft_time_limit=soft_timeout,
        time_limit=hard_timeout,
    )
    save_celery_task_id(job_id, celery_result.id)

    return {"ok": True, "job_id": job_id, "status": "queued"}


def start_job_handler(
    job_id: str,
    engine: str = Form("openrouter"),
    text_model: str = Form(""),
    table_model: str = Form(""),
    image_model: str = Form(""),
    stamp_model: str = Form(""),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """Запустить черновик на распознавание"""
    check_api_key(x_api_key)

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "draft":
        raise HTTPException(
            status_code=400, detail=f"Job is not a draft, status: {job.status}"
        )

    save_job_settings(job_id, text_model, table_model, image_model, stamp_model)
    update_job_engine(job_id, engine)

    can_accept, queue_size, max_size = check_queue_capacity()
    if not can_accept:
        raise HTTPException(
            status_code=503, detail=f"Queue full ({queue_size}/{max_size})"
        )

    # Рассчитываем динамический таймаут
    block_count = _get_block_count_for_job(job_id)
    soft_timeout, hard_timeout = calculate_dynamic_timeout(block_count)

    celery_result = run_ocr_task.apply_async(
        args=[job_id],
        priority=max(0, min(10, job.priority)),
        soft_time_limit=soft_timeout,
        time_limit=hard_timeout,
    )
    save_celery_task_id(job_id, celery_result.id)

    return {"ok": True, "job_id": job_id, "status": "queued"}


def pause_job_handler(
    job_id: str,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """Поставить задачу на паузу"""
    check_api_key(x_api_key)

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in ("queued", "processing"):
        raise HTTPException(
            status_code=400, detail=f"Cannot pause job in status: {job.status}"
        )

    if not pause_job(job_id):
        raise HTTPException(status_code=500, detail="Failed to pause job")

    return {"ok": True, "job_id": job_id, "status": "paused"}


def resume_job_handler(
    job_id: str,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """Возобновить задачу с паузы"""
    check_api_key(x_api_key)

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "paused":
        raise HTTPException(
            status_code=400, detail=f"Job is not paused, status: {job.status}"
        )

    if not resume_job(job_id):
        raise HTTPException(status_code=500, detail="Failed to resume job")

    can_accept, queue_size, max_size = check_queue_capacity()
    if not can_accept:
        raise HTTPException(
            status_code=503, detail=f"Queue full ({queue_size}/{max_size})"
        )

    # Рассчитываем динамический таймаут
    block_count = _get_block_count_for_job(job_id)
    soft_timeout, hard_timeout = calculate_dynamic_timeout(block_count)

    celery_result = run_ocr_task.apply_async(
        args=[job_id],
        priority=max(0, min(10, job.priority)),
        soft_time_limit=soft_timeout,
        time_limit=hard_timeout,
    )
    save_celery_task_id(job_id, celery_result.id)

    return {"ok": True, "job_id": job_id, "status": "queued"}


def cancel_job_handler(
    job_id: str,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """Отменить задачу (установить статус cancelled)"""
    from services.remote_ocr.server.storage import invalidate_pause_cache, update_job_status

    check_api_key(x_api_key)

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in ("queued", "processing", "paused"):
        raise HTTPException(
            status_code=400, detail=f"Cannot cancel job in status: {job.status}"
        )

    update_job_status(job_id, "cancelled", status_message="Отменено пользователем")
    invalidate_pause_cache(job_id)

    return {"ok": True, "job_id": job_id, "status": "cancelled"}
