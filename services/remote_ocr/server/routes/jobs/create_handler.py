"""Обработчик создания задачи OCR"""
import json
import uuid
from typing import Optional

from fastapi import File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from services.remote_ocr.server.local_storage import (
    LOCAL_PREFIX,
    cleanup_job,
    ensure_dirs,
    local_input_dir,
)
from services.remote_ocr.server.logging_config import get_logger
from services.remote_ocr.server.node_storage.ocr_registry import _load_annotation_from_db
from services.remote_ocr.server.queue_checker import check_queue_capacity
from services.remote_ocr.server.r2_keys import blocks_key as make_blocks_key
from services.remote_ocr.server.r2_keys import pdf_key as make_pdf_key
from services.remote_ocr.server.routes.common import (
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
    engine: str = Form("lmstudio"),
    text_model: str = Form(""),
    image_model: str = Form(""),
    stamp_model: str = Form(""),
    node_id: str = Form(...),
    is_correction_mode: str = Form("false"),
    blocks_file: Optional[UploadFile] = File(None, alias="blocks_file"),
    pdf: Optional[UploadFile] = File(None),
) -> dict:
    """Создать новую задачу OCR.

    Для node-backed задач pdf и blocks_file опциональны:
    - PDF берётся из R2 по node_id (если не передан)
    - Blocks берутся из Supabase annotations (если не переданы)
    """
    if engine not in ("lmstudio", "chandra"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported engine '{engine}'. Only 'lmstudio' is supported (legacy alias: 'chandra').",
        )

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
            "image_model": image_model or None,
            "stamp_model": stamp_model or None,
            "node_id": node_id,
        },
    )

    # Загрузка блоков: blocks_file (приоритет) → Supabase (node_id)
    blocks_data: Optional[dict | list] = None

    if blocks_file is not None:
        blocks_json = (await blocks_file.read()).decode("utf-8")
        try:
            blocks_data = json.loads(blocks_json)
        except json.JSONDecodeError as e:
            _logger.error(f"Invalid blocks_json: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid blocks_json: {e}")
    else:
        # Загружаем аннотацию из Supabase по node_id
        blocks_data = _load_annotation_from_db(node_id)
        if blocks_data is None:
            _logger.error(
                f"No blocks_file provided and no annotation in Supabase for node {node_id}",
                extra={"event": "blocks_not_found", "node_id": node_id},
            )
            raise HTTPException(
                status_code=400,
                detail=f"No blocks provided and no annotation found in Supabase for node {node_id}",
            )
        _logger.info(
            f"Loaded blocks from Supabase for node {node_id}",
            extra={"event": "blocks_from_db", "node_id": node_id},
        )

    job_id = str(uuid.uuid4())

    # Определяем r2_prefix - папку для файлов задачи
    pdf_needs_upload = False
    pdf_provided = pdf is not None and pdf.filename

    if node_id:
        pdf_r2_key = get_node_pdf_r2_key(node_id)
        if pdf_r2_key:
            from pathlib import PurePosixPath

            try:
                s3_check, bucket_check = get_r2_sync_client()
                s3_check.head_object(Bucket=bucket_check, Key=pdf_r2_key)
            except Exception:
                if not pdf_provided:
                    raise HTTPException(
                        status_code=400,
                        detail=f"PDF not found in R2 for node {node_id} and no PDF uploaded",
                    )
                _logger.warning(f"PDF not found in R2, will upload: {pdf_r2_key}")
                pdf_needs_upload = True
            r2_prefix = str(PurePosixPath(pdf_r2_key).parent)
        else:
            if not pdf_provided:
                raise HTTPException(
                    status_code=400,
                    detail=f"No PDF registered for node {node_id} and no PDF uploaded",
                )
            node_info = get_node_info(node_id)
            if node_info and node_info.get("parent_id"):
                r2_prefix = f"tree_docs/{node_info['parent_id']}"
            else:
                r2_prefix = f"tree_docs/{node_id}"
            pdf_r2_key = f"{r2_prefix}/{document_name}"
            pdf_needs_upload = True
    else:
        if not pdf_provided:
            raise HTTPException(
                status_code=400,
                detail="PDF file is required for jobs without node_id",
            )
        r2_prefix = f"{LOCAL_PREFIX}ocr_jobs/{job_id}"

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
        job.id, text_model, image_model, stamp_model, is_correction,
    )

    is_node = bool(node_id)

    if is_node:
        # Node-backed: загрузка в R2 (без изменений)
        try:
            s3_client, bucket_name = get_r2_sync_client()

            # --- Upload PDF ---
            if (pdf_needs_upload) and pdf_provided:
                pdf_content = await pdf.read()
                actual_pdf_key = pdf_r2_key or make_pdf_key(r2_prefix, document_name, is_node=True)
                s3_client.put_object(
                    Bucket=bucket_name,
                    Key=actual_pdf_key,
                    Body=pdf_content,
                    ContentType="application/pdf",
                )
                _logger.info(f"Uploaded PDF to R2: {actual_pdf_key} ({len(pdf_content)} bytes)")
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
            actual_blocks_key = make_blocks_key(r2_prefix, document_name, is_node=True)
            s3_client.put_object(
                Bucket=bucket_name,
                Key=actual_blocks_key,
                Body=blocks_bytes,
                ContentType="application/json",
            )

            # --- Register job files ---
            from pathlib import PurePosixPath
            blocks_file_name = PurePosixPath(actual_blocks_key).name
            add_job_file(job.id, "pdf", actual_pdf_key, document_name, pdf_size)
            add_job_file(job.id, "blocks", actual_blocks_key, blocks_file_name, len(blocks_bytes))

        except Exception as e:
            _logger.error(f"R2 upload failed: {e}")
            delete_job(job.id)
            raise HTTPException(
                status_code=500, detail=f"Failed to upload files to R2: {e}"
            )
    else:
        # Standalone: сохранение на локальный диск (без R2)
        try:
            ensure_dirs(job_id)
            input_dir = local_input_dir(job_id)

            # --- Save PDF ---
            pdf_content = await pdf.read()
            pdf_local = input_dir / "document.pdf"
            pdf_local.write_bytes(pdf_content)
            pdf_size = len(pdf_content)
            _logger.info(f"Saved PDF locally: {pdf_local} ({pdf_size} bytes)")

            # --- Save Blocks ---
            blocks_bytes = json.dumps(blocks_data, ensure_ascii=False, indent=2).encode("utf-8")
            blocks_local = input_dir / "blocks.json"
            blocks_local.write_bytes(blocks_bytes)

            # --- Register job files (local:// keys) ---
            pdf_key = f"{LOCAL_PREFIX}ocr_jobs/{job_id}/input/document.pdf"
            blocks_key = f"{LOCAL_PREFIX}ocr_jobs/{job_id}/input/blocks.json"
            add_job_file(job.id, "pdf", pdf_key, "document.pdf", pdf_size)
            add_job_file(job.id, "blocks", blocks_key, "blocks.json", len(blocks_bytes))

        except Exception as e:
            _logger.error(f"Local file save failed: {e}")
            cleanup_job(job_id)
            delete_job(job.id)
            raise HTTPException(
                status_code=500, detail=f"Failed to save files locally: {e}"
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


class CreateNodeJobRequest(BaseModel):
    """JSON-тело для создания node-backed OCR задачи (без upload файлов)."""
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


async def create_node_job_handler(body: CreateNodeJobRequest) -> dict:
    """Создать OCR-задачу для node-backed документа (JSON body, без upload).

    Сервер сам берёт PDF из R2 и annotation из Supabase.
    """
    if body.engine not in ("lmstudio", "chandra"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported engine '{body.engine}'. Only 'lmstudio' is supported (legacy alias: 'chandra').",
        )

    _logger.info(
        f"POST /jobs/node: создание node-backed OCR задачи",
        extra={
            "event": "job_create_request",
            "action": "create",
            "client_id": body.client_id,
            "document_id": body.document_id[:16],
            "document_name": body.document_name,
            "task_name": body.task_name,
            "engine": body.engine,
            "text_model": body.text_model or None,
            "image_model": body.image_model or None,
            "stamp_model": body.stamp_model or None,
            "node_id": body.node_id,
        },
    )

    # Загружаем блоки из Supabase
    blocks_data = _load_annotation_from_db(body.node_id)
    if blocks_data is None:
        _logger.error(
            f"No annotation in Supabase for node {body.node_id}",
            extra={"event": "blocks_not_found", "node_id": body.node_id},
        )
        raise HTTPException(
            status_code=400,
            detail=f"No annotation found in Supabase for node {body.node_id}",
        )
    _logger.info(
        f"Loaded blocks from Supabase for node {body.node_id}",
        extra={"event": "blocks_from_db", "node_id": body.node_id},
    )

    job_id = str(uuid.uuid4())

    # PDF должен быть в R2 по node_id
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

    from pathlib import PurePosixPath
    r2_prefix = str(PurePosixPath(pdf_r2_key).parent)

    # Проверка очереди
    can_accept, queue_size, max_size = check_queue_capacity()
    if not can_accept:
        _logger.warning(
            f"Очередь полна, задача отклонена: {queue_size}/{max_size}",
            extra={
                "event": "queue_rejected",
                "action": "create",
                "client_id": body.client_id,
                "queue_size": queue_size,
                "max_queue_size": max_size,
            },
        )
        raise HTTPException(
            status_code=503,
            detail=f"Queue is full ({queue_size}/{max_size}). Try again later.",
        )

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

    try:
        s3_client, bucket_name = get_r2_sync_client()

        # Upload Blocks
        blocks_bytes = json.dumps(blocks_data, ensure_ascii=False, indent=2).encode("utf-8")
        actual_blocks_key = make_blocks_key(r2_prefix, body.document_name, is_node=True)
        s3_client.put_object(
            Bucket=bucket_name,
            Key=actual_blocks_key,
            Body=blocks_bytes,
            ContentType="application/json",
        )

        # Register job files
        pdf_file_name = body.document_name
        blocks_file_name = PurePosixPath(actual_blocks_key).name
        add_job_file(job.id, "pdf", pdf_r2_key, pdf_file_name, 0)
        add_job_file(job.id, "blocks", actual_blocks_key, blocks_file_name, len(blocks_bytes))

    except Exception as e:
        _logger.error(f"R2 upload failed: {e}")
        delete_job(job.id)
        raise HTTPException(
            status_code=500, detail=f"Failed to upload files to R2: {e}"
        )

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
        f"Node-backed задача создана: {job.id}",
        extra={
            "event": "job_created",
            "action": "create",
            "job_id": job.id,
            "client_id": body.client_id,
            "engine": body.engine,
            "total_blocks": block_count,
            "node_id": body.node_id,
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
