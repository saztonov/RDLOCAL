"""Загрузка результатов OCR в R2"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .local_storage import is_local_path
from .logging_config import get_logger
from .r2_keys import resolve_r2_prefix
from .storage import Job
from .task_helpers import get_r2_storage

logger = get_logger(__name__)


def _load_blocks_metadata(work_dir: Path) -> tuple:
    """Загрузить метаданные блоков из annotation.json.

    Returns:
        Tuple: (stamp_ids: set, blocks_by_id: dict)
            - stamp_ids: ID блоков-штампов для исключения
            - blocks_by_id: словарь {block_id: metadata} для кропов
    """
    annotation_path = work_dir / "annotation.json"
    if not annotation_path.exists():
        return set(), {}

    try:
        with open(annotation_path, "r", encoding="utf-8") as f:
            ann = json.load(f)

        stamp_ids = set()
        blocks_by_id = {}

        for page in ann.get("pages", []):
            page_index = page.get("page_index", 0)
            for blk in page.get("blocks", []):
                block_id = blk.get("id")
                block_type = blk.get("block_type", "")

                # Собираем ID штампов
                if block_type == "stamp" or (block_type == "image" and blk.get("category_code") == "stamp"):
                    stamp_ids.add(block_id)

                # Собираем метаданные для всех блоков
                blocks_by_id[block_id] = {
                    "block_id": block_id,
                    "page_index": page_index,
                    "coords_norm": blk.get("coords_norm"),
                    "block_type": block_type,
                }

        return stamp_ids, blocks_by_id
    except Exception as e:
        logger.warning(f"Ошибка чтения annotation.json: {e}")
        return set(), {}


def upload_results_to_r2(job: Job, work_dir: Path, r2_prefix: str = None) -> str:
    """Загрузить результаты в R2 и записать в БД.

    Если есть node_id - загружаем в папку где лежит PDF (parent dir от pdf_r2_key)
    Иначе - в ocr_jobs/{job_id}/ (обратная совместимость)

    Использует batch upload для параллельной загрузки всех файлов.
    Для кропов сохраняет metadata с информацией о блоке (block_id, page_index, coords_norm, block_type).
    """
    r2 = get_r2_storage()

    # Определяем prefix для загрузки (если не передан)
    if r2_prefix is None:
        r2_prefix = resolve_r2_prefix(job)

    doc_stem = Path(job.document_name).stem

    # Загружаем метаданные блоков из annotation.json
    stamp_ids, blocks_by_id = _load_blocks_metadata(work_dir)

    # Собираем все файлы для batch upload
    # Формат: (local_path, r2_key, content_type, file_type, filename, size, metadata)
    files_to_upload = []

    # ocr_result.html -> {doc_stem}_ocr.html
    html_path = work_dir / "ocr_result.html"
    if html_path.exists():
        html_filename = f"{doc_stem}_ocr.html"
        r2_key = f"{r2_prefix}/{html_filename}"
        files_to_upload.append((
            str(html_path), r2_key, None,
            "ocr_html", html_filename, html_path.stat().st_size, None
        ))

    # document.md -> {doc_stem}_document.md
    md_path = work_dir / "document.md"
    if md_path.exists():
        md_filename = f"{doc_stem}_document.md"
        r2_key = f"{r2_prefix}/{md_filename}"
        files_to_upload.append((
            str(md_path), r2_key, None,
            "result_md", md_filename, md_path.stat().st_size, None
        ))
    else:
        logger.warning(f"document.md не найден для загрузки в R2: {md_path}")

    # crops/ (проверяем оба варианта: crops и crops_final)
    # Исключаем блоки-штампы (category_code='stamp')
    for crops_subdir in ["crops", "crops_final"]:
        crops_path = work_dir / crops_subdir
        if crops_path.exists():
            for crop_file in crops_path.iterdir():
                if crop_file.is_file() and crop_file.suffix.lower() == ".pdf":
                    block_id = crop_file.stem
                    if block_id in stamp_ids:
                        logger.debug(f"Пропущен кроп штампа: {crop_file.name}")
                        continue

                    r2_key = f"{r2_prefix}/crops/{crop_file.name}"
                    # Получаем metadata для кропа из annotation.json
                    crop_metadata = blocks_by_id.get(block_id)
                    files_to_upload.append((
                        str(crop_file), r2_key, None,
                        "crop", crop_file.name, crop_file.stat().st_size, crop_metadata
                    ))

    # Загрузка файлов
    if files_to_upload:
        is_standalone = is_local_path(r2_prefix)

        if is_standalone:
            # Standalone: файлы уже на диске, upload не нужен
            logger.info(f"Standalone: {len(files_to_upload)} файлов на диске (без R2)")
        else:
            # Node-backed: batch upload в R2
            uploads = [(local, r2_key, ct) for local, r2_key, ct, *_ in files_to_upload]
            logger.info(f"Batch uploading {len(uploads)} files for job {job.id}")

            results = r2.upload_files_batch(uploads)

            success_count = sum(1 for r in results if r)
            for i, (local_path, r2_key, ct, file_type, filename, size, metadata) in enumerate(files_to_upload):
                if not results[i]:
                    logger.error(f"Не удалось загрузить файл в R2: {r2_key}")

            logger.info(f"Batch upload завершён: {success_count}/{len(files_to_upload)} файлов загружено")

    return r2_prefix


def copy_crops_to_final(work_dir: Path, blocks) -> None:
    """Копировать PDF кропы из crops/images в crops_final для загрузки в R2.

    Исключает блоки с category_code='stamp' - они не сохраняются на R2.
    """
    crops_dir = work_dir / "crops"
    images_subdir = crops_dir / "images"
    crops_final = work_dir / "crops_final"

    if not images_subdir.exists():
        return

    crops_final.mkdir(exist_ok=True)
    blocks_by_id = {b.id: b for b in blocks}

    # ID блоков-штампов для исключения
    from rd_core.ocr.generator_common import is_stamp_block
    stamp_ids = {b.id for b in blocks if is_stamp_block(b)}

    for pdf_file in images_subdir.glob("*.pdf"):
        try:
            block_id = pdf_file.stem

            # Пропускаем штампы
            if block_id in stamp_ids:
                logger.debug(f"Пропущен кроп штампа: {pdf_file.name}")
                continue

            target = crops_final / pdf_file.name
            shutil.copy2(pdf_file, target)

            if block_id in blocks_by_id:
                blocks_by_id[block_id].image_file = str(target)

            logger.debug(f"PDF кроп скопирован: {pdf_file.name}")
        except Exception as e:
            logger.warning(f"Ошибка копирования PDF кропа {pdf_file}: {e}")
