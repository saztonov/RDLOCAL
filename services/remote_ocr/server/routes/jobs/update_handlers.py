"""Обработчики обновления задач OCR"""
import json
from typing import Optional

from fastapi import File, Form, HTTPException, UploadFile

from services.remote_ocr.server.local_storage import (
    LOCAL_PREFIX,
    is_local_path,
    local_input_dir,
    local_output_dir,
)
from services.remote_ocr.server.logging_config import get_logger
from services.remote_ocr.server.queue_checker import check_queue_capacity
from services.remote_ocr.server.routes.common import (
    get_r2_sync_client,
    require_job,
)
from services.remote_ocr.server.storage import (
    pause_job,
    reset_job_for_restart,
    resume_job,
    save_job_settings,
    update_job_engine,
    update_job_task_name,
)
from services.remote_ocr.server.task_dispatch import dispatch_ocr_task
from services.remote_ocr.server.timeout_utils import (
    count_blocks_from_data,
    parse_blocks_json,
)

_logger = get_logger(__name__)


def _get_block_count_for_job(job_id: str) -> int:
    """Получить количество блоков для задачи.

    Источники: Supabase annotation (node-backed) → локальный blocks.json (standalone).
    """
    try:
        from services.remote_ocr.server.storage import get_job
        job = get_job(job_id)
        if not job:
            return 100

        # Node-backed: из Supabase annotation
        if job.node_id:
            from services.remote_ocr.server.node_storage.ocr_registry import _load_annotation_from_db
            ann = _load_annotation_from_db(job.node_id)
            if ann is not None:
                return count_blocks_from_data(ann)

        # Standalone: из локального файла
        if is_local_path(job.r2_prefix):
            blocks_path = local_input_dir(job_id) / "blocks.json"
            if blocks_path.exists():
                content = blocks_path.read_bytes()
                return count_blocks_from_data(parse_blocks_json(content))

        return 100
    except Exception as e:
        _logger.warning(f"Failed to get block count for job {job_id}: {e}")
        return 100


def update_job_handler(
    job_id: str,
    task_name: str = Form(...),
) -> dict:
    """Обновить название задачи"""
    _logger.info(
        f"Переименование задачи: {job_id}",
        extra={"event": "job_lifecycle", "action": "rename", "job_id": job_id, "task_name": task_name},
    )

    require_job(job_id)

    if not update_job_task_name(job_id, task_name):
        raise HTTPException(status_code=500, detail="Failed to update job")

    return {"ok": True, "job_id": job_id, "task_name": task_name}


async def restart_job_handler(
    job_id: str,
    blocks_file: Optional[UploadFile] = File(None),
) -> dict:
    """Перезапустить задачу. Опционально обновить блоки."""
    _logger.info(
        f"Перезапуск задачи: {job_id}",
        extra={"event": "job_lifecycle", "action": "restart", "job_id": job_id},
    )

    job = require_job(job_id)

    try:
        if is_local_path(job.r2_prefix):
            # Standalone: удаляем output директорию, пересоздаём пустую
            import shutil
            output_dir = local_output_dir(job_id)
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "crops").mkdir(exist_ok=True)
            _logger.info(f"Cleaned local output dir for job {job_id}")
        elif job.r2_prefix:
            # Node-backed: удаляем OCR результаты из R2 по суффиксу
            from services.remote_ocr.server.r2_keys import resolve_r2_prefix
            r2_prefix = resolve_r2_prefix(job)
            s3_client, bucket_name = get_r2_sync_client()
            doc_stem = __import__("pathlib").Path(job.document_name).stem

            # Удаляем результатные файлы по конвенции
            result_suffixes = [f"/{doc_stem}_ocr.html", f"/{doc_stem}_document.md"]
            keys_to_delete = [{"Key": f"{r2_prefix}{s}"} for s in result_suffixes]

            # Удаляем crops/
            paginator = s3_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket_name, Prefix=f"{r2_prefix}/crops/"):
                if "Contents" in page:
                    keys_to_delete.extend({"Key": obj["Key"]} for obj in page["Contents"])

            if keys_to_delete:
                for i in range(0, len(keys_to_delete), 1000):
                    batch = keys_to_delete[i : i + 1000]
                    s3_client.delete_objects(Bucket=bucket_name, Delete={"Objects": batch})
                _logger.info(f"Deleted {len(keys_to_delete)} result files from R2 for job {job_id}")
    except Exception as e:
        _logger.warning(f"Failed to delete result files: {e}")

    if blocks_file:
        try:
            blocks_json = (await blocks_file.read()).decode("utf-8")
            blocks_data = json.loads(blocks_json)
            blocks_bytes = json.dumps(blocks_data, ensure_ascii=False, indent=2).encode(
                "utf-8"
            )

            if is_local_path(job.r2_prefix):
                # Standalone: обновляем blocks на диске
                blocks_local = local_input_dir(job_id) / "blocks.json"
                blocks_local.write_bytes(blocks_bytes)
                _logger.info(f"Updated blocks locally for job {job_id}")
            else:
                s3_client, bucket_name = get_r2_sync_client()

                from services.remote_ocr.server.r2_keys import blocks_key as make_blocks_key, resolve_r2_prefix

                r2_prefix = resolve_r2_prefix(job)
                blocks_key = make_blocks_key(r2_prefix, job.document_name, is_node=bool(job.node_id))

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

    dispatch_ocr_task(job_id, _get_block_count_for_job(job_id), job.priority)

    return {"ok": True, "job_id": job_id, "status": "queued"}


def start_job_handler(
    job_id: str,
    engine: str = Form("lmstudio"),
    text_model: str = Form(""),
    image_model: str = Form(""),
    stamp_model: str = Form(""),
) -> dict:
    """Запустить черновик на распознавание"""

    if engine not in ("lmstudio", "chandra"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported engine '{engine}'. Only 'lmstudio' is supported (legacy alias: 'chandra').",
        )

    _logger.info(
        f"Запуск черновика: {job_id}",
        extra={
            "event": "job_lifecycle", "action": "start", "job_id": job_id,
            "engine": engine, "text_model": text_model or None, "image_model": image_model or None,
        },
    )

    job = require_job(job_id)

    if job.status != "draft":
        raise HTTPException(
            status_code=400, detail=f"Job is not a draft, status: {job.status}"
        )

    save_job_settings(job_id, text_model, image_model, stamp_model)
    update_job_engine(job_id, engine)

    can_accept, queue_size, max_size = check_queue_capacity()
    if not can_accept:
        raise HTTPException(
            status_code=503, detail=f"Queue full ({queue_size}/{max_size})"
        )

    dispatch_ocr_task(job_id, _get_block_count_for_job(job_id), job.priority)

    return {"ok": True, "job_id": job_id, "status": "queued"}


def pause_job_handler(
    job_id: str,
) -> dict:
    """Поставить задачу на паузу"""
    _logger.info(
        f"Пауза задачи: {job_id}",
        extra={"event": "job_lifecycle", "action": "pause", "job_id": job_id},
    )

    job = require_job(job_id)

    if job.status not in ("queued", "processing"):
        raise HTTPException(
            status_code=400, detail=f"Cannot pause job in status: {job.status}"
        )

    if not pause_job(job_id):
        raise HTTPException(status_code=500, detail="Failed to pause job")

    return {"ok": True, "job_id": job_id, "status": "paused"}


def resume_job_handler(
    job_id: str,
) -> dict:
    """Возобновить задачу с паузы"""
    _logger.info(
        f"Возобновление задачи: {job_id}",
        extra={"event": "job_lifecycle", "action": "resume", "job_id": job_id},
    )

    job = require_job(job_id)

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

    dispatch_ocr_task(job_id, _get_block_count_for_job(job_id), job.priority)

    return {"ok": True, "job_id": job_id, "status": "queued"}


def cancel_job_handler(
    job_id: str,
) -> dict:
    """Отменить задачу (установить статус cancelled + revoke Celery task)"""
    from services.remote_ocr.server.celery_app import celery_app
    from services.remote_ocr.server.storage import invalidate_pause_cache, set_pause_cache, update_job_status

    _logger.info(
        f"Отмена задачи: {job_id}",
        extra={"event": "job_lifecycle", "action": "cancel", "job_id": job_id},
    )

    job = require_job(job_id)

    if job.status not in ("queued", "processing", "paused"):
        raise HTTPException(
            status_code=400, detail=f"Cannot cancel job in status: {job.status}"
        )

    was_processing = job.status == "processing"

    update_job_status(job_id, "cancelled", status_message="Отменено пользователем")
    invalidate_pause_cache(job_id)
    # Мгновенный сигнал worker-у через Redis (не ждать TTL кеша)
    set_pause_cache(job_id, True)

    # Revoke Celery task
    if job.celery_task_id:
        try:
            if was_processing:
                # SIGUSR1 бросит SoftTimeLimitExceeded в worker (graceful stop)
                celery_app.control.revoke(
                    job.celery_task_id, terminate=True, signal="SIGUSR1"
                )
            else:
                # queued/paused — просто убрать из очереди
                celery_app.control.revoke(job.celery_task_id, terminate=False)
            _logger.info(
                f"Revoked celery task {job.celery_task_id} for job {job_id[:8]}",
                extra={"event": "job_cancel_revoke", "job_id": job_id},
            )
        except Exception as e:
            _logger.warning(f"Failed to revoke task {job.celery_task_id}: {e}")

    # LM Studio locks и execution lock освобождаются в worker cleanup (finally)
    # или zombie detector — НЕ здесь, чтобы не сбить счётчик при duplicate delivery

    return {"ok": True, "job_id": job_id, "status": "cancelled"}
