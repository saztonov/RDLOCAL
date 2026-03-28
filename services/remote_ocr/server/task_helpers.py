"""Вспомогательные функции для OCR задач"""
from __future__ import annotations

import json
from pathlib import Path

from .logging_config import get_logger
from .node_storage.ocr_registry import _load_annotation_from_db
from .r2_keys import annotation_key, resolve_r2_prefix_for_node
from .storage import Job, get_job_file_by_type, get_node_pdf_r2_key, is_job_paused

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
    """Скачать файлы задачи из R2 во временную директорию.

    Если есть node_id - берём из tree_docs/{node_id}/ (через node_files)
    Иначе - из ocr_jobs/{job_id}/ (обратная совместимость)

    Использует batch download для параллельного скачивания PDF и blocks.
    """
    r2 = get_r2_storage()
    pdf_path = work_dir / "document.pdf"
    blocks_path = work_dir / "blocks.json"

    blocks_from_db = False

    # Определяем R2 ключи для обоих файлов
    if job.node_id:
        # Берём PDF из node_files или tree_nodes.attributes
        pdf_r2_key = get_node_pdf_r2_key(job.node_id)
        if not pdf_r2_key:
            raise RuntimeError(f"PDF r2_key not found for node {job.node_id}")

        # Пробуем загрузить аннотацию из Supabase (source of truth для node-backed jobs)
        ann_data = _load_annotation_from_db(job.node_id)
        if ann_data is not None:
            logger.info(
                f"Loaded blocks from Supabase for node {job.node_id}",
                extra={"event": "blocks_from_db", "job_id": job.id, "node_id": job.node_id},
            )
            with open(blocks_path, "w", encoding="utf-8") as f:
                json.dump(ann_data, f, ensure_ascii=False, indent=2)
            blocks_from_db = True
            blocks_r2_key = None  # не нужен, файл уже на диске
        else:
            # Fallback: загружаем из R2
            logger.info(
                f"Annotation not found in Supabase for node {job.node_id}, falling back to R2",
                extra={"event": "blocks_from_r2_fallback", "job_id": job.id, "node_id": job.node_id},
            )
            blocks_file = get_job_file_by_type(job.id, "blocks")
            if blocks_file:
                blocks_r2_key = blocks_file.r2_key
            else:
                # Fallback: {prefix}/{doc_stem}_annotation.json
                r2_prefix = resolve_r2_prefix_for_node(job.node_id)
                blocks_r2_key = annotation_key(r2_prefix, job.document_name)
    else:
        # Обратная совместимость: файлы из ocr_jobs
        pdf_file = get_job_file_by_type(job.id, "pdf")
        if not pdf_file:
            raise RuntimeError(f"PDF file not found for job {job.id}")
        pdf_r2_key = pdf_file.r2_key

        blocks_file = get_job_file_by_type(job.id, "blocks")
        if not blocks_file:
            raise RuntimeError(f"Blocks file not found for job {job.id}")
        blocks_r2_key = blocks_file.r2_key

    # Скачивание файлов из R2
    if blocks_from_db:
        # Blocks уже на диске, скачиваем только PDF
        logger.info(f"Downloading PDF for job {job.id}")
        if not r2.download_file(pdf_r2_key, str(pdf_path)):
            raise RuntimeError(f"Failed to download PDF from R2: {pdf_r2_key}")
    else:
        # Параллельное скачивание обоих файлов
        downloads = [
            (pdf_r2_key, str(pdf_path)),
            (blocks_r2_key, str(blocks_path)),
        ]

        logger.info(f"Batch downloading {len(downloads)} files for job {job.id}")
        results = r2.download_files_batch(downloads)

        # Проверяем результаты
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
