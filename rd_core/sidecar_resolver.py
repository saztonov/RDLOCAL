"""Resolver для R2-ключей OCR sidecar-файлов (ocr.html, document.md).

Единый механизм для верификации, pdf_status и block_verification.
Поддерживает несколько схем хранения:
  1. node_files в БД (приоритет)
  2. tree_docs/{node_id}/  (текущая desktop-схема)
  3. {pdf_parent}/  (legacy-схема рядом с PDF)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class ResolvedSidecar:
    """Результат резолва sidecar-ключей."""

    ocr_html_key: str  # resolved R2 key (или "")
    document_md_key: str  # resolved R2 key (или "")
    source: str  # "node_files" | "node_prefix" | "pdf_parent" | "not_found"
    ocr_html_found: bool
    document_md_found: bool


@runtime_checkable
class _NodeFilesClient(Protocol):
    """Minimal interface — любой объект с get_node_files(node_id, file_type=...)."""

    def get_node_files(self, node_id: str, file_type=None) -> list: ...


def _r2_key_exists(r2, key: str) -> bool:
    """Проверить существование объекта в R2 через HEAD."""
    try:
        r2.s3_client.head_object(Bucket=r2.bucket_name, Key=key)
        return True
    except Exception:
        return False


def _get_file_type_value(ft) -> str:
    """Извлечь строковое значение file_type из enum или строки."""
    if hasattr(ft, "value"):
        return ft.value
    return str(ft)


def resolve_sidecar_keys(
    *,
    node_id: str,
    r2_key: str,
    r2,
    client=None,
) -> ResolvedSidecar:
    """Resolve R2-ключей для ocr.html и document.md.

    Args:
        node_id: ID документа (узла).
        r2_key: R2-ключ PDF файла.
        r2: R2Storage instance (нужен s3_client, bucket_name).
        client: объект с get_node_files(node_id, file_type=...) — TreeClient
                или _SupabaseStatusClient. Может быть None.

    Returns:
        ResolvedSidecar с найденными ключами и источником.
    """
    pdf_path = PurePosixPath(r2_key)
    pdf_stem = pdf_path.stem
    pdf_parent = str(pdf_path.parent)

    # --- 1. node_files (БД) ---
    if client is not None and node_id:
        try:
            resolved = _resolve_from_node_files(client, node_id)
            if resolved:
                return resolved
        except Exception as exc:
            logger.debug("node_files lookup failed: %s", exc)

    # --- 2. tree_docs/{node_id}/ (текущая desktop-схема) ---
    if node_id:
        node_prefix = f"tree_docs/{node_id}"
        ocr_key = f"{node_prefix}/{pdf_stem}_ocr.html"
        md_key = f"{node_prefix}/{pdf_stem}_document.md"

        ocr_found = _r2_key_exists(r2, ocr_key)
        md_found = _r2_key_exists(r2, md_key)

        if ocr_found or md_found:
            return ResolvedSidecar(
                ocr_html_key=ocr_key,
                document_md_key=md_key,
                source="node_prefix",
                ocr_html_found=ocr_found,
                document_md_found=md_found,
            )

    # --- 3. {pdf_parent}/ (legacy-схема рядом с PDF) ---
    ocr_key = f"{pdf_parent}/{pdf_stem}_ocr.html"
    md_key = f"{pdf_parent}/{pdf_stem}_document.md"

    ocr_found = _r2_key_exists(r2, ocr_key)
    md_found = _r2_key_exists(r2, md_key)

    if ocr_found or md_found:
        return ResolvedSidecar(
            ocr_html_key=ocr_key,
            document_md_key=md_key,
            source="pdf_parent",
            ocr_html_found=ocr_found,
            document_md_found=md_found,
        )

    # --- 4. Не найдено ---
    return ResolvedSidecar(
        ocr_html_key="",
        document_md_key="",
        source="not_found",
        ocr_html_found=False,
        document_md_found=False,
    )


def _resolve_from_node_files(client, node_id: str) -> ResolvedSidecar | None:
    """Попробовать найти sidecar-ключи через node_files в БД."""
    ocr_key = ""
    md_key = ""

    node_files = client.get_node_files(node_id)
    for nf in node_files:
        ft = _get_file_type_value(getattr(nf, "file_type", nf))
        r2 = getattr(nf, "r2_key", "") or (nf.get("r2_key", "") if isinstance(nf, dict) else "")
        if ft == "ocr_html" and r2:
            ocr_key = r2
        elif ft == "result_md" and r2:
            md_key = r2

    if ocr_key or md_key:
        return ResolvedSidecar(
            ocr_html_key=ocr_key,
            document_md_key=md_key,
            source="node_files",
            ocr_html_found=bool(ocr_key),
            document_md_found=bool(md_key),
        )
    return None
