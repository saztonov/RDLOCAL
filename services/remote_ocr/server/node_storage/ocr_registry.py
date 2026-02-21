"""Регистрация OCR результатов в node_files"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Dict, List

import httpx

from services.remote_ocr.server.logging_config import get_logger
from services.remote_ocr.server.node_storage.file_manager import (
    add_node_file,
    get_node_pdf_r2_key,
)

logger = get_logger(__name__)


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
        "Prefer": "resolution=merge-duplicates",
    }

    format_version = ann_data.get("format_version", 2)

    payload = {
        "node_id": node_id,
        "data": ann_data,
        "format_version": format_version,
        "updated_at": datetime.utcnow().isoformat(),
    }

    resp = httpx.post(
        f"{supabase_url}/rest/v1/annotations",
        json=payload,
        headers=headers,
        timeout=15.0,
    )
    resp.raise_for_status()
    return True


def register_ocr_results_to_node(node_id: str, doc_name: str, work_dir) -> int:
    """Зарегистрировать все OCR результаты в node_files.

    Файлы загружены в папку исходного PDF (parent dir от pdf_r2_key).
    Кропы сохраняются с метаданными блоков (block_id, page_index, coords, block_type).
    """
    if not node_id:
        return 0

    work_path = Path(work_dir)
    now = datetime.utcnow().isoformat()

    # Получаем r2_key исходного PDF и используем его родительскую папку
    pdf_r2_key = get_node_pdf_r2_key(node_id)
    if pdf_r2_key:
        tree_prefix = str(PurePosixPath(pdf_r2_key).parent)
    else:
        tree_prefix = f"tree_docs/{node_id}"

    registered = 0

    doc_stem = Path(doc_name).stem

    # Загружаем annotation.json для получения метаданных блоков
    blocks_by_id: Dict[str, dict] = {}
    annotation_path = work_path / "annotation.json"
    if annotation_path.exists():
        try:
            with open(annotation_path, "r", encoding="utf-8") as f:
                ann = json.load(f)
            for page in ann.get("pages", []):
                for blk in page.get("blocks", []):
                    blocks_by_id[blk["id"]] = blk
        except Exception as e:
            logger.warning(f"Failed to load annotation.json for metadata: {e}")

    # result.json -> {doc_stem}_result.json
    result_json = work_path / "result.json"
    if result_json.exists():
        json_filename = f"{doc_stem}_result.json"
        add_node_file(
            node_id,
            "result_json",
            f"{tree_prefix}/{json_filename}",
            json_filename,
            result_json.stat().st_size,
            "application/json",
        )
        registered += 1

    # annotation.json -> сохраняем в таблицу annotations (Supabase)
    # Аннотация хранится в Supabase (привязана к node_id), а не в node_files
    if annotation_path.exists():
        try:
            with open(annotation_path, "r", encoding="utf-8") as f:
                ann_data = json.load(f)
            _save_annotation_to_db(node_id, ann_data)
            registered += 1
            logger.info(f"Annotation saved to Supabase annotations table: node_id={node_id}")
        except Exception as e:
            logger.warning(f"Failed to save annotation to Supabase: {e}")

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

    # _blocks.json -> {doc_stem}_blocks.json (file_type=blocks_index)
    blocks_json = work_path / "_blocks.json"
    if blocks_json.exists():
        blocks_filename = f"{doc_stem}_blocks.json"
        add_node_file(
            node_id,
            "blocks_index",
            f"{tree_prefix}/{blocks_filename}",
            blocks_filename,
            blocks_json.stat().st_size,
            "application/json",
        )
        registered += 1
        logger.info(
            f"✅ Зарегистрирован _blocks.json в node_files: {blocks_filename} (file_type=blocks_index)"
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
    # Добавляем корневую директорию проекта в путь если ещё не добавлено
    project_root = Path(__file__).parent.parent.parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        import httpx

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
