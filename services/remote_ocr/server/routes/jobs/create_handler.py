"""Обработчик создания задачи OCR"""
import json
import uuid
from typing import Optional

from fastapi import File, Form, Header, HTTPException, UploadFile

from services.remote_ocr.server.logging_config import get_logger
from services.remote_ocr.server.queue_checker import check_queue_capacity
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
from services.remote_ocr.server.storage_jobs import save_celery_task_id
from services.remote_ocr.server.tasks import run_ocr_task
from services.remote_ocr.server.timeout_utils import (
    calculate_dynamic_timeout,
    count_blocks_from_data,
)

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

    can_accept, queue_size, max_size = check_queue_capacity()
    if not can_accept:
        _logger.warning(
            f"Очередь полна, задача отклонена: {queue_size}/{max_size}",
            extra={
                "event": "queue_rejected",
                "action": "create",
                "job_id": job_id,
                "client_id": client_id,
                "queue_size": queue_size,
                "max_queue_size": max_size,
            },
        )
        raise HTTPException(
            status_code=503,
            detail=f"Queue is full ({queue_size}/{max_size}). Try again later.",
        )

    try:
        s3_client, bucket_name = get_r2_sync_client()

        if node_id:
            from pathlib import PurePosixPath

            doc_stem = PurePosixPath(document_name).stem

            if pdf_needs_upload:
                pdf_content = await pdf.read()
                pdf_key = pdf_r2_key or f"{r2_prefix}/{document_name}"
                s3_client.put_object(
                    Bucket=bucket_name,
                    Key=pdf_key,
                    Body=pdf_content,
                    ContentType="application/pdf",
                )
                _logger.info(
                    f"Uploaded PDF to R2: {pdf_key} ({len(pdf_content)} bytes)"
                )

                add_node_file(
                    node_id,
                    "pdf",
                    pdf_key,
                    document_name,
                    len(pdf_content),
                    "application/pdf",
                )
                update_node_r2_key(node_id, pdf_key)
            else:
                pdf_key = pdf_r2_key

            blocks_bytes = json.dumps(blocks_data, ensure_ascii=False, indent=2).encode(
                "utf-8"
            )
            blocks_key = f"{r2_prefix}/{doc_stem}_annotation.json"
            s3_client.put_object(
                Bucket=bucket_name,
                Key=blocks_key,
                Body=blocks_bytes,
                ContentType="application/json",
            )
            add_job_file(
                job.id,
                "blocks",
                blocks_key,
                f"{doc_stem}_annotation.json",
                len(blocks_bytes),
            )
            add_job_file(job.id, "pdf", pdf_key, document_name, 0)
        else:
            pdf_content = await pdf.read()
            pdf_key = f"{r2_prefix}/document.pdf"
            s3_client.put_object(
                Bucket=bucket_name,
                Key=pdf_key,
                Body=pdf_content,
                ContentType="application/pdf",
            )
            add_job_file(job.id, "pdf", pdf_key, "document.pdf", len(pdf_content))

            blocks_bytes = json.dumps(blocks_data, ensure_ascii=False, indent=2).encode(
                "utf-8"
            )
            blocks_key = f"{r2_prefix}/blocks.json"
            s3_client.put_object(
                Bucket=bucket_name,
                Key=blocks_key,
                Body=blocks_bytes,
                ContentType="application/json",
            )
            add_job_file(job.id, "blocks", blocks_key, "blocks.json", len(blocks_bytes))

    except Exception as e:
        _logger.error(f"R2 upload failed: {e}")
        delete_job(job.id)
        raise HTTPException(
            status_code=500, detail=f"Failed to upload files to R2: {e}"
        )

    # Рассчитываем динамический таймаут на основе количества блоков
    block_count = count_blocks_from_data(blocks_data)
    soft_timeout, hard_timeout = calculate_dynamic_timeout(block_count)

    celery_result = run_ocr_task.apply_async(
        args=[job.id],
        priority=max(0, min(10, job.priority)),
        soft_time_limit=soft_timeout,
        time_limit=hard_timeout,
    )
    save_celery_task_id(job.id, celery_result.id)

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
