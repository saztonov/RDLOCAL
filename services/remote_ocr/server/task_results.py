"""Генерация результатов OCR"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from .logging_config import get_logger
from .ocr_result_merger import merge_ocr_results
from .storage import Job, get_node_full_path, get_node_pdf_r2_key

logger = get_logger(__name__)


def generate_blocks_json(
    blocks: list,
    work_dir: Path,
    r2_prefix: str,
) -> Path:
    """Генерация _blocks.json с информацией об IMAGE блоках и их кропах.

    Args:
        blocks: список Block объектов
        work_dir: рабочая директория
        r2_prefix: префикс R2 (tree_docs/{node_id})

    Returns:
        Path к созданному файлу
    """
    from rd_core.models.enums import BlockType

    r2_public_url = os.getenv("R2_PUBLIC_URL", "https://rd1.svarovsky.ru").rstrip("/")

    image_blocks = []
    for block in blocks:
        if block.block_type == BlockType.IMAGE:
            # Исключаем штампы (у них нет кропов в R2)
            if getattr(block, "category_code", None) == "stamp":
                continue

            crop_url = f"{r2_public_url}/{r2_prefix}/crops/{block.id}.pdf"

            image_blocks.append({
                "id": block.id,
                "page_index": block.page_index,
                "block_type": "image",
                "category_code": getattr(block, "category_code", None),
                "crop_url": crop_url,
            })

    blocks_json_path = work_dir / "_blocks.json"
    with open(blocks_json_path, "w", encoding="utf-8") as f:
        json.dump({"blocks": image_blocks}, f, ensure_ascii=False, indent=2)

    logger.info(
        f"_blocks.json сгенерирован: {blocks_json_path} ({len(image_blocks)} IMAGE блоков)"
    )
    return blocks_json_path


def generate_results(
    job: Job,
    pdf_path: Path,
    blocks: list,
    work_dir: Path,
    ocr_backend=None,
    on_verification_progress: Callable[[int, int], None] = None,
) -> str:
    """Генерация результатов OCR (annotation.json + HTML)"""
    from rd_core.models import Block, Document, Page, ShapeType
    from rd_core.ocr import generate_html_from_pages, generate_md_from_pages

    from .pdf_streaming_core import get_page_dimensions_streaming

    # Проверяем режим корректировки
    is_correction_mode = job.settings.is_correction_mode if job.settings else False
    if is_correction_mode and job.node_id:
        logger.info(f"[{job.id}] Correction mode detected, using merge strategy")
        return _generate_correction_results(
            job, pdf_path, blocks, work_dir, ocr_backend, on_verification_progress
        )

    # Логирование состояния блоков
    blocks_with_ocr = sum(1 for b in blocks if b.ocr_text)
    logger.info(
        f"generate_results: всего блоков={len(blocks)}, с ocr_text={blocks_with_ocr}"
    )

    # Сохраняем оригинальный порядок блоков (индекс в исходном списке)
    blocks_by_page: dict[int, list[tuple[int, any]]] = {}
    for orig_idx, b in enumerate(blocks):
        blocks_by_page.setdefault(b.page_index, []).append((orig_idx, b))

    # Streaming получение размеров страниц
    page_dims = get_page_dimensions_streaming(str(pdf_path))

    pages = []
    for page_idx in sorted(blocks_by_page.keys()):
        dims = page_dims.get(page_idx)
        width, height = dims if dims else (0, 0)
        page_blocks = [
            b for _, b in sorted(blocks_by_page[page_idx], key=lambda x: x[0])
        ]

        # Пересчитываем coords_px и polygon_points
        if width > 0 and height > 0:
            for block in page_blocks:
                old_x1, old_y1, old_x2, old_y2 = block.coords_px
                old_bbox_w = old_x2 - old_x1 if old_x2 != old_x1 else 1
                old_bbox_h = old_y2 - old_y1 if old_y2 != old_y1 else 1

                block.coords_px = Block.norm_to_px(block.coords_norm, width, height)

                if block.shape_type == ShapeType.POLYGON and block.polygon_points:
                    new_x1, new_y1, new_x2, new_y2 = block.coords_px
                    new_bbox_w = new_x2 - new_x1 if new_x2 != new_x1 else 1
                    new_bbox_h = new_y2 - new_y1 if new_y2 != new_y1 else 1
                    block.polygon_points = [
                        (
                            int(new_x1 + (px - old_x1) / old_bbox_w * new_bbox_w),
                            int(new_y1 + (py - old_y1) / old_bbox_h * new_bbox_h),
                        )
                        for px, py in block.polygon_points
                    ]

        pages.append(
            Page(page_number=page_idx, width=width, height=height, blocks=page_blocks)
        )

    # Вычисляем r2_prefix
    if job.node_id:
        pdf_r2_key = get_node_pdf_r2_key(job.node_id)
        if pdf_r2_key:
            from pathlib import PurePosixPath

            r2_prefix = str(PurePosixPath(pdf_r2_key).parent)
        else:
            r2_prefix = f"tree_docs/{job.node_id}"
    else:
        r2_prefix = job.r2_prefix

    # Извлекаем путь для ссылок
    if r2_prefix.startswith("tree_docs/"):
        project_name = r2_prefix[len("tree_docs/") :]
    else:
        project_name = job.node_id if job.node_id else job.id

    # Получаем полный путь из дерева проектов (используется в HTML и JSON)
    if job.node_id:
        full_path = get_node_full_path(job.node_id)
        doc_name = full_path if full_path else pdf_path.name
    else:
        doc_name = pdf_path.name

    # annotation.json (для хранения разметки блоков)
    annotation_path = work_dir / "annotation.json"
    doc = Document(pdf_path=doc_name, pages=pages)
    with open(annotation_path, "w", encoding="utf-8") as f:
        json.dump(doc.to_dict(), f, ensure_ascii=False, indent=2)

    # Генерация итогового HTML файла
    html_path = work_dir / "ocr_result.html"
    try:
        generate_html_from_pages(
            pages, str(html_path), doc_name=doc_name, project_name=project_name
        )
        logger.info(f"HTML файл сгенерирован: {html_path}")
    except Exception as e:
        logger.warning(f"Ошибка генерации HTML: {e}")

    # Генерация компактного Markdown файла (оптимизирован для LLM)
    md_path = work_dir / "document.md"
    try:
        generate_md_from_pages(
            pages, str(md_path), doc_name=doc_name, project_name=project_name
        )
        if md_path.exists():
            logger.info(f"✅ MD файл сгенерирован: {md_path} ({md_path.stat().st_size} bytes)")
        else:
            logger.error(f"❌ MD файл не создан: {md_path}")
    except Exception as e:
        logger.error(f"❌ Ошибка генерации MD: {e}", exc_info=True)

    # Генерация result.json (annotation + ocr_html + crop_url для каждого блока)
    result_path = work_dir / "result.json"
    try:
        merge_ocr_results(
            annotation_path,
            html_path,
            result_path,
            project_name=project_name,
            doc_name=doc_name,
        )
    except Exception as e:
        logger.warning(f"Ошибка генерации result.json: {e}")

    # Генерация _blocks.json (список IMAGE блоков с URL кропов)
    try:
        generate_blocks_json(blocks, work_dir, r2_prefix)
    except Exception as e:
        logger.warning(f"Ошибка генерации _blocks.json: {e}")

    # Верификация и повторное распознавание пропущенных блоков
    if ocr_backend and result_path.exists():
        from .block_verification import verify_and_retry_missing_blocks

        try:
            # Сигнализируем начало верификации (total=0 означает "начало проверки")
            if on_verification_progress:
                on_verification_progress(0, 0)

            logger.info("Запуск верификации блоков...")
            verify_and_retry_missing_blocks(
                result_path,
                pdf_path,
                work_dir,
                ocr_backend,
                on_progress=on_verification_progress,
                job_id=job.id,
            )
        except Exception as e:
            logger.warning(f"Ошибка верификации блоков: {e}", exc_info=True)

    return r2_prefix


def _generate_correction_results(
    job: Job,
    pdf_path: Path,
    blocks: list,
    work_dir: Path,
    ocr_backend=None,
    on_verification_progress: Callable[[int, int], None] = None,
) -> str:
    """
    Генерация результатов в режиме корректировки.
    Merge новых OCR результатов с существующим result.json.
    """
    from rd_core.ocr import generate_html_from_pages
    from rd_core.ocr.generator_common import sanitize_html
    from .ocr_html_parser import build_segments_from_html

    from .ocr_result_merger import regenerate_html_from_result, regenerate_md_from_result
    from .task_helpers import get_r2_storage

    r2_storage = get_r2_storage()
    doc_stem = pdf_path.stem

    # Вычисляем r2_prefix
    if job.node_id:
        pdf_r2_key = get_node_pdf_r2_key(job.node_id)
        if pdf_r2_key:
            from pathlib import PurePosixPath

            r2_prefix = str(PurePosixPath(pdf_r2_key).parent)
        else:
            r2_prefix = f"tree_docs/{job.node_id}"
    else:
        r2_prefix = job.r2_prefix

    logger.info(f"[{job.id}] Correction mode: merging results, r2_prefix={r2_prefix}")

    # 1. Скачать существующий result.json из R2
    old_result_r2_key = f"{r2_prefix}/{doc_stem}_result.json"
    old_result_path = work_dir / "old_result.json"

    if not r2_storage.download_file(old_result_r2_key, str(old_result_path)):
        logger.warning(
            f"[{job.id}] No existing result.json at {old_result_r2_key}, "
            "falling back to full generation"
        )
        # Отключаем is_correction_mode и вызываем обычную генерацию
        if job.settings:
            job.settings.is_correction_mode = False
        return generate_results(
            job, pdf_path, blocks, work_dir, ocr_backend, on_verification_progress
        )

    with open(old_result_path, "r", encoding="utf-8") as f:
        old_result = json.load(f)

    logger.info(f"[{job.id}] Loaded existing result.json with {len(old_result.get('pages', []))} pages")

    # 2. Собрать ID корректировочных блоков и построить ocr_text map
    correction_block_ids = set()
    new_ocr_map = {}
    new_blocks_map = {}

    for block in blocks:
        new_blocks_map[block.id] = block
        if block.ocr_text:
            new_ocr_map[block.id] = block.ocr_text
        if getattr(block, "is_correction", False):
            correction_block_ids.add(block.id)

    # Если нет корректировочных блоков - все блоки считаем корректировочными
    if not correction_block_ids:
        correction_block_ids = set(new_blocks_map.keys())

    logger.info(
        f"[{job.id}] Correction blocks: {len(correction_block_ids)}, "
        f"with ocr_text: {len(new_ocr_map)}"
    )

    # 3. Генерируем временный HTML для корректировочных блоков (чтобы получить ocr_html)
    correction_blocks = [b for b in blocks if b.id in correction_block_ids]
    if correction_blocks:
        temp_pages = _build_pages_from_blocks(correction_blocks, pdf_path)
        temp_html_path = work_dir / "temp_correction_ocr.html"

        # Получаем doc_name для HTML
        if job.node_id:
            doc_name = get_node_full_path(job.node_id) or pdf_path.name
        else:
            doc_name = pdf_path.name

        generate_html_from_pages(temp_pages, str(temp_html_path), doc_name=doc_name)

        with open(temp_html_path, "r", encoding="utf-8") as f:
            new_html_text = f.read()

        # Парсим HTML фрагменты для корректировочных блоков
        segments, meta = build_segments_from_html(new_html_text, list(correction_block_ids))
    else:
        segments, meta = {}, {}

    # 4. Merge: обновляем только корректировочные блоки в old_result
    updated_count = 0
    existing_ids = set()

    for page in old_result.get("pages", []):
        for blk in page.get("blocks", []):
            block_id = blk.get("id")
            existing_ids.add(block_id)

            if block_id in correction_block_ids:
                # Обновляем ocr_text
                if block_id in new_ocr_map:
                    blk["ocr_text"] = new_ocr_map[block_id]

                # Обновляем ocr_html
                if block_id in segments:
                    blk["ocr_html"] = sanitize_html(segments[block_id])
                    blk["ocr_meta"] = meta.get(block_id, {"method": ["correction"]})

                # Обновляем coords если изменились
                if block_id in new_blocks_map:
                    new_block = new_blocks_map[block_id]
                    blk["coords_px"] = list(new_block.coords_px)
                    blk["coords_norm"] = list(new_block.coords_norm)
                    if new_block.polygon_points:
                        blk["polygon_points"] = new_block.polygon_points

                # Снимаем флаг корректировки
                blk["is_correction"] = False
                updated_count += 1

    logger.info(f"[{job.id}] Updated {updated_count} existing correction blocks")

    # 5. Добавляем НОВЫЕ блоки (которых не было в old_result)
    new_blocks_added = 0
    for block in blocks:
        if block.id not in existing_ids and block.id in correction_block_ids:
            # Новый блок - добавляем на соответствующую страницу
            # page_index в result.json 1-based, в блоках 0-based
            page_idx_0based = block.page_index
            page_idx_1based = page_idx_0based + 1

            # Ищем страницу или создаём новую
            target_page = None
            for page in old_result.get("pages", []):
                if page.get("page_number") == page_idx_1based:
                    target_page = page
                    break

            if target_page is None:
                # Создаём новую страницу
                target_page = {
                    "page_number": page_idx_1based,
                    "page_index": page_idx_1based,
                    "blocks": [],
                }
                old_result.setdefault("pages", []).append(target_page)
                old_result["pages"].sort(key=lambda p: p.get("page_number", 0))

            # Формируем dict блока
            block_dict = block.to_dict()
            block_dict["is_correction"] = False
            # Конвертируем page_index в 1-based
            block_dict["page_index"] = page_idx_1based

            if block.id in segments:
                block_dict["ocr_html"] = sanitize_html(segments[block.id])
                block_dict["ocr_meta"] = meta.get(block.id, {"method": ["correction"]})

            target_page["blocks"].append(block_dict)
            new_blocks_added += 1
            logger.info(f"[{job.id}] Added new block {block.id} to page {page_idx_1based}")

    if new_blocks_added > 0:
        logger.info(f"[{job.id}] Added {new_blocks_added} new blocks")

    # 6. Получаем doc_name для регенерации
    if job.node_id:
        doc_name = get_node_full_path(job.node_id) or pdf_path.name
    else:
        doc_name = pdf_path.name

    # 7. Сохраняем обновлённый result.json
    result_path = work_dir / "result.json"
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(old_result, f, ensure_ascii=False, indent=2)
    logger.info(f"[{job.id}] Saved merged result.json: {result_path}")

    # 8. Регенерируем HTML из result.json
    html_path = work_dir / "ocr_result.html"
    regenerate_html_from_result(old_result, html_path, doc_name=doc_name)

    # 9. Регенерируем MD из result.json
    md_path = work_dir / "document.md"
    regenerate_md_from_result(old_result, md_path, doc_name=doc_name)

    # 10. Также обновляем annotation.json
    annotation_path = work_dir / "annotation.json"
    old_ann_r2_key = f"{r2_prefix}/{doc_stem}_annotation.json"

    if r2_storage.download_file(old_ann_r2_key, str(annotation_path)):
        with open(annotation_path, "r", encoding="utf-8") as f:
            old_annotation = json.load(f)

        # Обновляем блоки в аннотации
        for page in old_annotation.get("pages", []):
            for blk in page.get("blocks", []):
                block_id = blk.get("id")
                if block_id in new_ocr_map:
                    blk["ocr_text"] = new_ocr_map[block_id]
                if block_id in correction_block_ids:
                    blk["is_correction"] = False

        with open(annotation_path, "w", encoding="utf-8") as f:
            json.dump(old_annotation, f, ensure_ascii=False, indent=2)
        logger.info(f"[{job.id}] Updated annotation.json")
    else:
        logger.warning(f"[{job.id}] Could not download annotation.json from {old_ann_r2_key}")

    # 11. Генерируем _blocks.json
    try:
        generate_blocks_json(blocks, work_dir, r2_prefix)
    except Exception as e:
        logger.warning(f"[{job.id}] Error generating _blocks.json: {e}")

    logger.info(
        f"[{job.id}] Correction results generated: "
        f"updated={updated_count}, new={new_blocks_added}"
    )

    return r2_prefix


def _build_pages_from_blocks(blocks: list, pdf_path: Path) -> list:
    """Построить список Page объектов из блоков для генерации HTML."""
    from rd_core.models import Page

    from .pdf_streaming_core import get_page_dimensions_streaming

    # Группируем блоки по страницам
    pages_dict: dict[int, list] = {}
    for block in blocks:
        page_idx = block.page_index
        if page_idx not in pages_dict:
            pages_dict[page_idx] = []
        pages_dict[page_idx].append(block)

    # Получаем размеры страниц
    page_dims = get_page_dimensions_streaming(str(pdf_path))

    pages = []
    for page_idx in sorted(pages_dict.keys()):
        dims = page_dims.get(page_idx)
        width, height = dims if dims else (612, 792)
        page = Page(
            page_number=page_idx,
            width=int(width),
            height=int(height),
            blocks=pages_dict[page_idx],
        )
        pages.append(page)

    return pages
