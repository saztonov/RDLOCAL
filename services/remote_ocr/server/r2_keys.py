"""Централизованное вычисление R2 prefix и ключей для OCR задач."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Optional

from .logging_config import get_logger
from .storage_models import Job

logger = get_logger(__name__)


def resolve_r2_prefix(job: Job) -> str:
    """Вычислить R2 prefix для задачи.

    Логика:
    - node_id есть → parent dir от pdf_r2_key (из node_files)
    - node_id есть, pdf_r2_key нет → tree_docs/{node_id}
    - node_id нет → job.r2_prefix (обычно ocr_jobs/{job_id})
    """
    if job.node_id:
        from .node_storage import get_node_pdf_r2_key

        pdf_r2_key = get_node_pdf_r2_key(job.node_id)
        if pdf_r2_key:
            return str(PurePosixPath(pdf_r2_key).parent)
        return f"tree_docs/{job.node_id}"
    return job.r2_prefix


def resolve_r2_prefix_for_node(node_id: str) -> str:
    """Вычислить R2 prefix для node_id (без Job).

    Используется в node_storage и других местах,
    где Job недоступен.
    """
    from .node_storage import get_node_pdf_r2_key

    pdf_r2_key = get_node_pdf_r2_key(node_id)
    if pdf_r2_key:
        return str(PurePosixPath(pdf_r2_key).parent)
    return f"tree_docs/{node_id}"


def annotation_key(r2_prefix: str, doc_name: str) -> str:
    """R2-ключ для annotation.json: {prefix}/{stem}_annotation.json"""
    stem = PurePosixPath(doc_name).stem
    return f"{r2_prefix}/{stem}_annotation.json"


def result_key(r2_prefix: str, doc_name: str) -> str:
    """R2-ключ для result.json: {prefix}/{stem}_result.json"""
    stem = PurePosixPath(doc_name).stem
    return f"{r2_prefix}/{stem}_result.json"


def html_key(r2_prefix: str, doc_name: str) -> str:
    """R2-ключ для OCR HTML: {prefix}/{stem}_ocr.html"""
    stem = PurePosixPath(doc_name).stem
    return f"{r2_prefix}/{stem}_ocr.html"


def md_key(r2_prefix: str, doc_name: str) -> str:
    """R2-ключ для Markdown: {prefix}/{stem}_document.md"""
    stem = PurePosixPath(doc_name).stem
    return f"{r2_prefix}/{stem}_document.md"


def blocks_index_key(r2_prefix: str, doc_name: str) -> str:
    """R2-ключ для _blocks.json: {prefix}/{stem}_blocks.json"""
    stem = PurePosixPath(doc_name).stem
    return f"{r2_prefix}/{stem}_blocks.json"


def crop_key(r2_prefix: str, block_id: str) -> str:
    """R2-ключ для кропа блока: {prefix}/crops/{block_id}.pdf"""
    return f"{r2_prefix}/crops/{block_id}.pdf"
