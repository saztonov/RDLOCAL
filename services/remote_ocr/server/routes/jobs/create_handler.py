"""Обработчик создания задачи OCR"""
import json
import uuid
from typing import Optional

from fastapi import File, Form, Header, HTTPException, UploadFile

from services.remote_ocr.server.logging_config import get_logger
from services.remote_ocr.server.queue_checker import check_queue_capacity
from services.remote_ocr.server.r2_keys import blocks_key as make_blocks_key
from services.remote_ocr.server.r2_keys import pdf_key as make_pdf_key
from services.remote_ocr.server.routes.common import (
    check_api_key,
    get_r2_sync_client,
)
from services.remote_ocr.server.storage import (
    add_job_file,
    add_node_file,
    create_job,
    delete_job,
    get_node_info,
    get_node_pdf_r2_key,
    save_job_settings,
    update_node_r2_key,
)
from services.remote_ocr.server.task_dispatch import dispatch_ocr_task
from services.remote_ocr.server.timeout_utils import count_blocks_from_data

_logger = get_logger(__name__)


async def create_job_handler(
    document_id: str = Form(...),
    document_name: str = Form(...),
    client_id: str = Form(...),
    task_name: str = Form(""),
    engine: str = Form("openrouter"),
    text_model: str = Form(""),
    table_model: str = Form(""),
    image_model: str = Form(""),
    stamp_model: str = Form(""),
    node_id: Optional[str] = Form(None),
    is_correction_mode: str = Form("false"),
    blocks_file: UploadFile = File(..., alias="blocks_file"),
    pdf: UploadFile = File(...),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """Создать новую задачу OCR.

    Если node_id указан - файлы берутся из tree_docs/{node_id}/, не дублируем.
    """
    check_api_key(x_api_key)

    blocks_json = (await blocks_file.read()).decode("utf-8")
    _logger.info(
        f"POST /jobs: создание OCR задачи",
        extra={
            "event": "job_create_request",
            "action": "create",
            "client_id": client_id,
            "document_id": document_id[:16],
            "document_name": document_name,
            "task_name": task_name,
            "engine": engine,
            "text_model": text_model or None,
            "table_model": table_model or None,
            "image_model": image_model or None,
            "stamp_model": stamp_model or None,
            "node_id": node_id,
        },
    )

    try:
        blocks_data = json.loads(blocks_json)
    except json.JSONDecodeError as e:
        _logger.error(f"Invalid blocks_json: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid blocks_json: {e}")

    job_id = str(uuid.uuid4())

    # Определяем r2_prefix - папку для файлов задачи
    pdf_needs_upload = False

    if node_id:
        pdf_r2_key = get_node_pdf_r2_key(node_id)
        if pdf_r2_key:
            from pathlib import PurePosixPath

            try:
                s3_check, bucket_check = get_r2_sync_client()
                s3_check.head_object(Bucket=bucket_check, Key=pdf_r2_key)
            except Exception:
                _logger.warning(f"PDF not found in R2, will upload: {pdf_r2_key}")
                pdf_needs_upload = True
            r2_prefix = str(PurePosixPath(pdf_r2_key).parent)
        else:
            node_info = get_node_info(node_id)
            if node_info and node_info.get("parent_id"):
                r2_prefix = f"tree_docs/{node_info['parent_id']}"
            else:
                r2_prefix = f"tree_docs/{node_id}"
            pdf_r2_key = f"{r2_prefix}/{document_name}"
            pdf_needs_upload = True
    else:
        r2_prefix = f"ocr_jobs/{job_id}"

    # Проверка очереди ПЕРЕД созданием job в БД
    can_accept, queue_size, max_size = check_queue_capacity()
    if not can_accept:
        _logger.warning(
            f"Очередь полна, задача отклонена: {queue_size}/{max_size}",
            extra={
                "event": "queue_rejected",
                "action": "create",
                "client_id": client_id,
                "queue_size": queue_size,
                "max_queue_size": max_size,
            },
        )
        raise HTTPException(
            status_code=503,
            detail=f"Queue is full ({queue_size}/{max_size}). Try again later.",
        )

    job = create_job(
        document_id=document_id,
        document_name=document_name,
        task_name=task_name,
        engine=engine,
        r2_prefix=r2_prefix,
        client_id=client_id,
        status="queued",
        node_id=node_id,
    )

    is_correction = is_correction_mode.lower() == "true"
    save_job_settings(
        job.id, text_model, table_model, image_model, stamp_model, is_correction
    )

    try:
        s3_client, bucket_name = get_r2_sync_client()

        is_node = bool(node_id)

        # --- Upload PDF ---
        if pdf_needs_upload or not is_node:
            pdf_content = await pdf.read()
            actual_pdf_key = pdf_r2_key or make_pdf_key(r2_prefix, document_name, is_node=is_node)
            s3_client.put_object(
                Bucket=bucket_name,
                Key=actual_pdf_key,
                Body=pdf_content,
                ContentType="application/pdf",
            )
            _logger.info(f"Uploaded PDF to R2: {actual_pdf_key} ({len(pdf_content)} bytes)")

            if is_node:
                add_node_file(
                    node_id, "pdf", actual_pdf_key,
                    document_name, len(pdf_content), "application/pdf",
                )
                update_node_r2_key(node_id, actual_pdf_key)
            pdf_size = len(pdf_content)
        else:
            actual_pdf_key = pdf_r2_key
            pdf_size = 0

        # --- Upload Blocks ---
        blocks_bytes = json.dumps(blocks_data, ensure_ascii=False, indent=2).encode("utf-8")
        actual_blocks_key = make_blocks_key(r2_prefix, document_name, is_node=is_node)
        s3_client.put_object(
            Bucket=bucket_name,
            Key=actual_blocks_key,
            Body=blocks_bytes,
            ContentType="application/json",
        )

        # --- Register job files ---
        from pathlib import PurePosixPath
        pdf_file_name = document_name if is_node else "document.pdf"
        blocks_file_name = PurePosixPath(actual_blocks_key).name
        add_job_file(job.id, "pdf", actual_pdf_key, pdf_file_name, pdf_size)
        add_job_file(job.id, "blocks", actual_blocks_key, blocks_file_name, len(blocks_bytes))

    except Exception as e:
        _logger.error(f"R2 upload failed: {e}")
        delete_job(job.id)
        raise HTTPException(
            status_code=500, detail=f"Failed to upload files to R2: {e}"
        )

    # Запускаем OCR задачу в Celery
    block_count = count_blocks_from_data(blocks_data)
    try:
        dispatch_ocr_task(job.id, block_count, job.priority)
    except Exception as e:
        _logger.error(f"Dispatch failed, rolling back job {job.id}: {e}")
        delete_job(job.id)
        raise HTTPException(
            status_code=500, detail=f"Failed to dispatch OCR task: {e}"
        )

    _logger.info(
        f"Задача создана и поставлена в очередь: {job.id}",
        extra={
            "event": "job_created",
            "action": "create",
            "job_id": job.id,
            "client_id": client_id,
            "engine": engine,
            "total_blocks": block_count,
            "node_id": node_id,
        },
    )

    return {
        "id": job.id,
        "status": job.status,
        "progress": job.progress,
        "document_id": job.document_id,
        "document_name": job.document_name,
        "task_name": job.task_name,
    }
