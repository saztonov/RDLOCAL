"""Вспомогательные функции для OCR задач"""
from __future__ import annotations

import json
from pathlib import Path

from .logging_config import get_logger
from .node_storage.ocr_registry import _load_annotation_from_db
from .r2_keys import annotation_key, resolve_r2_prefix_for_node
from .storage import Job, get_node_pdf_r2_key, is_job_paused

logger = get_logger(__name__)


def get_r2_storage():
    """Получить R2 Storage клиент (async-обёртка)"""
    from .async_r2_storage import AsyncR2StorageSync

    return AsyncR2StorageSync()


def check_paused(job_id: str) -> bool:
    """Проверить, не поставлена ли задача на паузу"""
    if is_job_paused(job_id):
        logger.info(f"Задача {job_id} поставлена на паузу")
        return True
    return False


def download_job_files(job: Job, work_dir: Path) -> tuple[Path, Path]:
    """Скачать файлы node-backed задачи из R2 во временную директорию.

    PDF берётся из node_files (get_node_pdf_r2_key).
    Blocks берутся из Supabase annotation, fallback — по R2 конвенции.

    Standalone задачи НЕ используют эту функцию — их файлы уже на диске.
    """
    r2 = get_r2_storage()
    pdf_path = work_dir / "document.pdf"
    blocks_path = work_dir / "blocks.json"

    # PDF: из node_files
    pdf_r2_key = get_node_pdf_r2_key(job.node_id)
    if not pdf_r2_key:
        raise RuntimeError(f"PDF r2_key not found for node {job.node_id}")

    # Blocks: из Supabase annotation → fallback по R2 конвенции
    blocks_from_db = False
    ann_data = _load_annotation_from_db(job.node_id)
    if ann_data is not None:
        logger.info(
            f"Loaded blocks from Supabase for node {job.node_id}",
            extra={"event": "blocks_from_db", "job_id": job.id, "node_id": job.node_id},
        )
        with open(blocks_path, "w", encoding="utf-8") as f:
            json.dump(ann_data, f, ensure_ascii=False, indent=2)
        blocks_from_db = True
    else:
        # Fallback: R2 ключ по конвенции {prefix}/{doc_stem}_annotation.json
        logger.info(
            f"Annotation not found in Supabase for node {job.node_id}, falling back to R2",
            extra={"event": "blocks_from_r2_fallback", "job_id": job.id, "node_id": job.node_id},
        )
        r2_prefix = resolve_r2_prefix_for_node(job.node_id)
        blocks_r2_key = annotation_key(r2_prefix, job.document_name)

    # Скачивание из R2
    if blocks_from_db:
        logger.info(f"Downloading PDF for job {job.id}")
        if not r2.download_file(pdf_r2_key, str(pdf_path)):
            raise RuntimeError(f"Failed to download PDF from R2: {pdf_r2_key}")
    else:
        downloads = [
            (pdf_r2_key, str(pdf_path)),
            (blocks_r2_key, str(blocks_path)),
        ]
        logger.info(f"Batch downloading {len(downloads)} files for job {job.id}")
        results = r2.download_files_batch(downloads)
        if not results[0]:
            raise RuntimeError(f"Failed to download PDF from R2: {pdf_r2_key}")
        if not results[1]:
            raise RuntimeError(f"Failed to download blocks from R2: {blocks_r2_key}")

    logger.info(f"Successfully downloaded files for job {job.id}")
    return pdf_path, blocks_path


def create_empty_result(job: Job, work_dir: Path, pdf_path: Path) -> None:
    """Создать пустой результат и сохранить в Supabase."""
    from rd_core.models import Document

    from .node_storage.ocr_registry import _save_annotation_to_db

    empty_doc = Document(pdf_path=pdf_path.name, pages=[])
    ann_dict = empty_doc.to_dict()

    if job.node_id:
        _save_annotation_to_db(job.node_id, ann_dict)
