"""Регистрация OCR результатов в node_files"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from services.remote_ocr.server.logging_config import get_logger
from services.remote_ocr.server.node_storage.file_manager import add_node_file
from services.remote_ocr.server.r2_keys import resolve_r2_prefix_for_node

logger = get_logger(__name__)


def _load_annotation_from_db(node_id: str) -> Optional[dict]:
    """Загрузить аннотацию из таблицы annotations (Supabase) по node_id."""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL or SUPABASE_KEY not set")
        return None

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.get(
            f"{supabase_url}/rest/v1/annotations",
            params={"node_id": f"eq.{node_id}", "select": "data"},
            headers=headers,
            timeout=15.0,
        )
        resp.raise_for_status()
        rows = resp.json()
        if rows and rows[0].get("data"):
            return rows[0]["data"]
        return None
    except Exception as e:
        logger.error(f"Failed to load annotation from Supabase: {e}")
        return None


def _save_annotation_to_db(node_id: str, ann_data: dict) -> bool:
    """Сохранить аннотацию в таблицу annotations (Supabase).

    Использует UPSERT: если запись для node_id уже есть — обновляет.
    """
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL or SUPABASE_KEY not set")
        return False

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    format_version = ann_data.get("format_version", 2)

    payload = {
        "node_id": node_id,
        "data": ann_data,
        "format_version": format_version,
        "updated_at": datetime.utcnow().isoformat(),
    }

    annotations_url = f"{supabase_url}/rest/v1/annotations"
    resp = httpx.post(
        annotations_url,
        params={"on_conflict": "node_id"},
        json=payload,
        headers=headers,
        timeout=15.0,
    )

    # Some PostgREST/Supabase setups still return 409 on unique(node_id) without applying
    # the merge. In that case, fall back to an explicit update of the existing row.
    if resp.status_code == 409:
        resp = httpx.patch(
            annotations_url,
            params={"node_id": f"eq.{node_id}"},
            json={
                "data": ann_data,
                "format_version": format_version,
                "updated_at": payload["updated_at"],
            },
            headers=headers,
            timeout=15.0,
        )

    resp.raise_for_status()
    return True


def register_ocr_results_to_node(node_id: str, doc_name: str, work_dir, blocks_metadata: dict = None) -> int:
    """Зарегистрировать все OCR результаты в node_files.

    Файлы загружены в папку исходного PDF (parent dir от pdf_r2_key).
    Кропы сохраняются с метаданными блоков (block_id, page_index, coords, block_type).

    Args:
        node_id: ID узла документа.
        doc_name: Имя документа (для формирования имён файлов).
        work_dir: Рабочая директория с результатами OCR.
        blocks_metadata: Данные аннотации (dict) для извлечения метаданных блоков.
            Если None — метаданные кропов не заполняются.
    """
    if not node_id:
        return 0

    work_path = Path(work_dir)
    now = datetime.utcnow().isoformat()

    tree_prefix = resolve_r2_prefix_for_node(node_id)

    registered = 0

    doc_stem = Path(doc_name).stem

    # Загружаем метаданные блоков из переданной аннотации
    blocks_by_id: Dict[str, dict] = {}
    if blocks_metadata:
        for page in blocks_metadata.get("pages", []):
            for blk in page.get("blocks", []):
                blocks_by_id[blk["id"]] = blk

    # ocr_result.html -> {doc_stem}_ocr.html
    ocr_html = work_path / "ocr_result.html"
    if ocr_html.exists():
        ocr_filename = f"{doc_stem}_ocr.html"
        add_node_file(
            node_id,
            "ocr_html",
            f"{tree_prefix}/{ocr_filename}",
            ocr_filename,
            ocr_html.stat().st_size,
            "text/html",
        )
        registered += 1

    # document.md -> {doc_stem}_document.md (file_type=result_md)
    document_md = work_path / "document.md"
    if document_md.exists():
        md_filename = f"{doc_stem}_document.md"
        add_node_file(
            node_id,
            "result_md",
            f"{tree_prefix}/{md_filename}",
            md_filename,
            document_md.stat().st_size,
            "text/markdown",
        )
        registered += 1
        logger.info(
            f"✅ Зарегистрирован document.md в node_files: {md_filename} (file_type=result_md)"
        )
    else:
        logger.warning(
            f"⚠️ document.md не найден для регистрации в node_files: {document_md}"
        )

    # Собираем все кропы из crops/ и crops_final/
    all_crop_files: List[Path] = []
    for crops_subdir in ["crops", "crops_final"]:
        crops_dir = work_path / crops_subdir
        if crops_dir.exists():
            for crop_file in crops_dir.iterdir():
                if crop_file.is_file() and crop_file.suffix.lower() == ".pdf":
                    # Избегаем дубликатов (проверяем по имени файла)
                    if not any(c.name == crop_file.name for c in all_crop_files):
                        all_crop_files.append(crop_file)

    # Регистрируем папку кропов как сущность
    if all_crop_files:
        add_node_file(
            node_id,
            "crops_folder",
            f"{tree_prefix}/crops/",
            "crops",
            0,
            "inode/directory",
            metadata={"crops_count": len(all_crop_files), "created_at": now},
        )
        registered += 1

    # Регистрируем каждый кроп с метаданными блока
    for crop_file in all_crop_files:
        block_id = crop_file.stem  # block_id = имя файла без расширения
        block_data = blocks_by_id.get(block_id, {})

        add_node_file(
            node_id,
            "crop",
            f"{tree_prefix}/crops/{crop_file.name}",
            crop_file.name,
            crop_file.stat().st_size,
            "application/pdf",
            metadata={
                "block_id": block_id,
                "page_index": block_data.get("page_index"),
                "coords_norm": block_data.get("coords_norm"),
                "block_type": block_data.get("block_type"),
            },
        )
        registered += 1

    logger.info(
        f"Registered {registered} OCR result files for node {node_id} ({len(all_crop_files)} crops)"
    )
    return registered


def update_node_pdf_status(node_id: str):
    """
    Обновить статус PDF документа в БД

    Args:
        node_id: ID узла документа
    """
    try:
        # Graceful degradation: rd_core.pdf_status может зависеть от app модуля,
        # недоступного в Docker окружении сервера
        try:
            from rd_core.pdf_status import calculate_pdf_status
            from rd_core.r2_storage import R2Storage
        except ImportError as e:
            logger.warning(f"pdf_status module unavailable (likely missing app module): {e}")
            return

        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_KEY")

        if not supabase_url or not supabase_key:
            logger.error("SUPABASE_URL or SUPABASE_KEY not set")
            return

        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
        }

        # Получаем узел
        response = httpx.get(
            f"{supabase_url}/rest/v1/tree_nodes",
            params={"id": f"eq.{node_id}", "select": "id,attributes"},
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
        nodes = response.json()

        if not nodes:
            logger.warning(f"Node {node_id} not found")
            return

        r2_key = nodes[0].get("attributes", {}).get("r2_key", "")
        if not r2_key:
            logger.warning(f"Node {node_id} has no r2_key")
            return

        # Вычисляем статус
        r2 = R2Storage()
        status, message = calculate_pdf_status(r2, node_id, r2_key, check_blocks=True)

        # Обновляем в БД
        rpc_response = httpx.post(
            f"{supabase_url}/rest/v1/rpc/update_pdf_status",
            json={"p_node_id": node_id, "p_status": status.value, "p_message": message},
            headers=headers,
            timeout=10.0,
        )
        rpc_response.raise_for_status()

        logger.info(f"Updated PDF status for {node_id}: {status.value}")

    except Exception as e:
        logger.error(f"Failed to update node PDF status: {e}")
        raise
