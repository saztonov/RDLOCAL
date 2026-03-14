"""Utilities for PDF document status checks."""
from __future__ import annotations

import logging
import os
from enum import Enum
from pathlib import PurePosixPath
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class PDFStatus(str, Enum):
    """Status of a PDF document."""

    COMPLETE = "complete"
    MISSING_FILES = "missing_files"
    MISSING_BLOCKS = "missing_blocks"
    UNKNOWN = "unknown"


class _SupabaseStatusClient:
    """Minimal REST client for server environments without the desktop `app` package."""

    def __init__(self):
        self._base_url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
        self._api_key = os.getenv("SUPABASE_KEY") or ""
        if not self._base_url or not self._api_key:
            raise RuntimeError("SUPABASE_URL or SUPABASE_KEY not set")

        self._headers = {
            "apikey": self._api_key,
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        response = httpx.get(
            f"{self._base_url}/rest/v1{path}",
            params=params,
            headers=self._headers,
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    def get_node_files(self, node_id: str) -> list[dict[str, Any]]:
        return self._get(
            "/node_files",
            {"node_id": f"eq.{node_id}", "select": "file_type"},
        )

    def has_annotation_in_db(self, node_id: str) -> bool:
        rows = self._get(
            "/annotations",
            {"node_id": f"eq.{node_id}", "select": "id", "limit": 1},
        )
        return bool(rows)

    def get_annotation_data_for_status(self, node_id: str) -> dict[str, Any] | None:
        rows = self._get(
            "/annotations",
            {"node_id": f"eq.{node_id}", "select": "data", "limit": 1},
        )
        if not rows:
            return None
        return rows[0].get("data")


def _create_status_client():
    """Create a Supabase REST client for PDF status checks."""
    return _SupabaseStatusClient()


def _normalize_file_type(value: Any) -> str:
    if hasattr(value, "value"):
        value = value.value
    elif isinstance(value, dict):
        value = value.get("file_type")

    if value is None:
        return ""
    return str(value).strip().lower()


def calculate_pdf_status(
    r2_storage, node_id: str, r2_key: str, check_blocks: bool = True,
    client=None,
) -> tuple[PDFStatus, str]:
    """Calculate PDF document status from R2 and Supabase state.

    Args:
        client: Optional pre-created client (TreeClient or compatible).
                If None, uses Supabase REST fallback.
    """
    if not r2_key:
        return PDFStatus.UNKNOWN, "Нет R2 ключа"

    try:
        if client is None:
            client = _create_status_client()

        pdf_path = PurePosixPath(r2_key)
        pdf_stem = pdf_path.stem
        pdf_parent = str(pdf_path.parent)

        ocr_r2_key = f"{pdf_parent}/{pdf_stem}_ocr.html"
        res_r2_key = f"{pdf_parent}/{pdf_stem}_result.json"

        r2_objects = r2_storage.list_objects_with_metadata(f"{pdf_parent}/")
        r2_keys = {obj["Key"] for obj in r2_objects}

        has_ocr_html_r2 = ocr_r2_key in r2_keys
        has_result_json_r2 = res_r2_key in r2_keys

        has_annotation = client.has_annotation_in_db(node_id)

        try:
            node_files = client.get_node_files(node_id)
            file_types_in_db = {
                _normalize_file_type(getattr(item, "file_type", item))
                for item in node_files
            }
        except Exception as exc:
            logger.error(
                "Failed to get node files for %s: %s", node_id, exc, exc_info=True
            )
            raise

        has_ocr_html_db = "ocr_html" in file_types_in_db
        has_result_json_db = "result_json" in file_types_in_db

        pages_without_blocks: list[int] = []
        if check_blocks and has_annotation:
            try:
                ann_data = client.get_annotation_data_for_status(node_id)
                if isinstance(ann_data, dict):
                    pages = ann_data.get("pages", [])
                elif isinstance(ann_data, list):
                    pages = ann_data
                else:
                    pages = []

                for page in pages:
                    if not isinstance(page, dict):
                        continue
                    page_num = page.get("page_number", -1)
                    blocks = page.get("blocks", [])
                    if not blocks:
                        pages_without_blocks.append(page_num)
            except Exception as exc:
                logger.error("Failed to check annotation blocks: %s", exc)

        missing_r2: list[str] = []
        missing_db: list[str] = []

        if not has_ocr_html_r2:
            missing_r2.append("ocr.html")
        if not has_ocr_html_db:
            missing_db.append("ocr.html")
        if not has_result_json_r2:
            missing_r2.append("result.json")
        if not has_result_json_db:
            missing_db.append("result.json")

        if not has_annotation:
            return PDFStatus.MISSING_BLOCKS, "Нет аннотации в базе данных"
        if pages_without_blocks:
            pages_str = ", ".join(str(p) for p in sorted(pages_without_blocks))
            return PDFStatus.MISSING_BLOCKS, f"Страницы без блоков: {pages_str}"
        if missing_r2 or missing_db:
            parts: list[str] = []
            if missing_r2:
                parts.append(f"R2: {', '.join(missing_r2)}")
            if missing_db:
                parts.append(f"БД: {', '.join(missing_db)}")
            return PDFStatus.MISSING_FILES, "Отсутствует:\n" + "\n".join(parts)
        return PDFStatus.COMPLETE, "Все файлы на месте, блоки размечены"

    except Exception as exc:
        logger.error("Failed to calculate PDF status: %s", exc, exc_info=True)
        return PDFStatus.UNKNOWN, f"Ошибка проверки: {exc}"


def update_pdf_status_in_db(
    client, node_id: str, status: PDFStatus, message: str = None
):
    """Update PDF status via the existing TreeClient-like interface."""
    try:
        client._request(
            "post",
            "/rpc/update_pdf_status",
            json={"p_node_id": node_id, "p_status": status.value, "p_message": message},
        )
        logger.debug("Updated PDF status for %s: %s", node_id, status.value)
    except Exception as exc:
        logger.error("Failed to update PDF status in DB: %s", exc)
