"""Единая генерация результатов OCR.

Консолидирует логику из:
- app/ocr/local_pipeline.py::_generate_local_results
- services/remote_ocr/server/task_results.py::generate_results

Чистая логика без серверных зависимостей (Celery, Redis, Supabase).
Вызывающий код отвечает за I/O: сохранение в файловую систему, Supabase, R2.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ResultPipelineOutput:
    """Результат генерации OCR — enriched annotation dict + артефакты."""
    annotation_dict: dict
    enriched_dict: dict
    html_path: Path | None = None
    md_path: Path | None = None
    html_stats: object | None = None
    md_stats: object | None = None
    partial_failures: list[str] = field(default_factory=list)


def generate_ocr_results(
    pdf_path: Path,
    blocks: list,
    work_dir: Path,
    output_dir: Path,
    *,
    page_dims: dict[int, tuple[int, int]],
    engine: str = "lmstudio",
    doc_name: str | None = None,
    project_name: str | None = None,
    # Correction mode
    is_correction_mode: bool = False,
    full_blocks_data: list[dict] | None = None,
    # Backends для верификации
    text_backend=None,
    text_fallback_backend=None,
    image_backend=None,
    stamp_backend=None,
    # Verification
    verification_config=None,
    on_verification_progress: Callable[[int, int], None] | None = None,
    job_id: str | None = None,
    deadline: float | None = None,
    before_stamp_phase: Callable | None = None,
    before_image_phase: Callable | None = None,
    # Output naming
    html_filename: str | None = None,
    md_filename: str | None = None,
) -> ResultPipelineOutput:
    """Единая генерация результатов OCR.

    Args:
        pdf_path: путь к PDF файлу
        blocks: список Block объектов с ocr_text
        work_dir: рабочая директория (для промежуточных файлов)
        output_dir: директория для итоговых файлов (HTML, MD)
        page_dims: размеры страниц {page_index: (width, height)}
        engine: имя OCR движка
        doc_name: название документа (для HTML/MD заголовков)
        project_name: имя проекта (для R2 ссылок)
        is_correction_mode: режим корректировки
        full_blocks_data: полный набор блоков для merge (local correction mode)
        text_backend: OCR backend для TEXT блоков
        text_fallback_backend: fallback backend
        image_backend: backend для IMAGE блоков
        stamp_backend: backend для STAMP блоков
        verification_config: VerificationConfig для block_verification
        on_verification_progress: callback прогресса верификации
        job_id: ID задачи (для логирования)
        deadline: абсолютное время deadline
        before_stamp_phase: callback для model swap
        before_image_phase: callback для model swap
        html_filename: имя HTML файла (default: {stem}_ocr.html)
        md_filename: имя MD файла (default: {stem}_document.md)

    Returns:
        ResultPipelineOutput с enriched dict и путями к файлам
    """
    from rd_core.models import Block, Document, Page, ShapeType
    from rd_core.models.enums import BlockType
    from rd_core.ocr import generate_html_from_pages, generate_md_from_pages
    from rd_core.ocr_result import is_suspicious_output, make_error as make_ocr_error

    from rd_core.ocr.text_ocr_quality import filter_mixed_text_output
    from rd_core.ocr.ocr_result_merger import (
        enrich_annotation_dict,
        regenerate_html_from_result,
        regenerate_md_from_result,
    )
    from rd_core.ocr.block_verification import verify_and_retry_missing_blocks

    partial_failures: list[str] = []

    # ── Фильтрация артефактов и детекция suspicious output ──

    for block in blocks:
        if block.block_type == BlockType.TEXT and block.ocr_text:
            block.ocr_text, _ = filter_mixed_text_output(block.ocr_text, engine)

    for block in blocks:
        if block.block_type == BlockType.TEXT and block.ocr_text:
            suspicious, reason = is_suspicious_output(block.ocr_text)
            if suspicious:
                block.ocr_text = make_ocr_error(f"suspicious OCR output: {reason}")

    # ── Correction mode: merge в полный набор блоков ──

    if is_correction_mode and full_blocks_data:
        ocr_results = {b.id: b.ocr_text for b in blocks if b.ocr_text}

        full_blocks = [Block.from_dict(b, migrate_ids=False)[0] for b in full_blocks_data]

        for fb in full_blocks:
            if fb.id in ocr_results:
                fb.ocr_text = ocr_results[fb.id]

        # Фильтрация только свежераспознанных блоков
        for fb in full_blocks:
            if fb.id in ocr_results and fb.block_type == BlockType.TEXT and fb.ocr_text:
                fb.ocr_text, _ = filter_mixed_text_output(fb.ocr_text, engine)
                suspicious, reason = is_suspicious_output(fb.ocr_text)
                if suspicious:
                    fb.ocr_text = make_ocr_error(f"suspicious OCR output: {reason}")

        logger.info(
            f"Correction mode: merged {len(ocr_results)} OCR results "
            f"into {len(full_blocks)} total blocks"
        )
        blocks = full_blocks

    # ── Группировка блоков по страницам и пересчёт координат ──

    blocks_by_page: dict[int, list[tuple[int, object]]] = {}
    for idx, b in enumerate(blocks):
        blocks_by_page.setdefault(b.page_index, []).append((idx, b))

    pages = []
    for page_idx in sorted(blocks_by_page.keys()):
        dims = page_dims.get(page_idx)
        width, height = dims if dims else (0, 0)
        page_blocks = [b for _, b in sorted(blocks_by_page[page_idx], key=lambda x: x[0])]

        if width > 0 and height > 0:
            for block in page_blocks:
                old_x1, old_y1, old_x2, old_y2 = block.coords_px
                old_bbox_w = max(old_x2 - old_x1, 1)
                old_bbox_h = max(old_y2 - old_y1, 1)
                block.coords_px = Block.norm_to_px(block.coords_norm, width, height)

                if block.shape_type == ShapeType.POLYGON and block.polygon_points:
                    new_x1, new_y1, new_x2, new_y2 = block.coords_px
                    new_bbox_w = max(new_x2 - new_x1, 1)
                    new_bbox_h = max(new_y2 - new_y1, 1)
                    block.polygon_points = [
                        (
                            int(new_x1 + (px - old_x1) / old_bbox_w * new_bbox_w),
                            int(new_y1 + (py - old_y1) / old_bbox_h * new_bbox_h),
                        )
                        for px, py in block.polygon_points
                    ]

        pages.append(Page(page_number=page_idx, width=width, height=height, blocks=page_blocks))

    # ── Document и annotation dict ──

    if not doc_name:
        doc_name = pdf_path.name
    doc = Document(pdf_path=doc_name, pages=pages)
    ann_dict = doc.to_dict()

    # ── Генерация HTML ──

    pdf_stem = pdf_path.stem
    html_name = html_filename or f"{pdf_stem}_ocr.html"
    html_path = output_dir / html_name
    html_stats = None
    try:
        _, html_stats = generate_html_from_pages(
            pages, str(html_path), doc_name=doc_name, project_name=project_name
        )
    except Exception as e:
        logger.warning(f"HTML generation error: {e}")
        partial_failures.append(f"HTML: {e}")

    # ── Генерация Markdown ──

    md_name = md_filename or f"{pdf_stem}_document.md"
    md_path = output_dir / md_name
    md_stats = None
    try:
        _, md_stats = generate_md_from_pages(
            pages, str(md_path), doc_name=doc_name, project_name=project_name
        )
    except Exception as e:
        logger.warning(f"MD generation error: {e}")
        partial_failures.append(f"MD: {e}")

    # ── Обогащение annotation dict ──

    try:
        html_text = ""
        if html_path.exists():
            with open(html_path, "r", encoding="utf-8") as f:
                html_text = f.read()
        enriched_dict = enrich_annotation_dict(
            ann_dict, html_text, project_name=project_name or ""
        )
    except Exception as e:
        logger.warning(f"Enrichment error: {e}")
        partial_failures.append(f"enrich: {e}")
        enriched_dict = ann_dict

    # ── Верификация блоков (retry missing) ──

    if text_backend:
        try:
            if on_verification_progress:
                on_verification_progress(0, 0)

            enriched_dict = verify_and_retry_missing_blocks(
                enriched_dict,
                pdf_path,
                work_dir,
                text_backend,
                text_fallback_backend=text_fallback_backend,
                image_backend=image_backend,
                stamp_backend=stamp_backend,
                on_progress=on_verification_progress,
                job_id=job_id,
                deadline=deadline,
                before_stamp_phase=before_stamp_phase,
                before_image_phase=before_image_phase,
                config=verification_config,
            )
        except Exception as e:
            logger.warning(f"Block verification error: {e}", exc_info=True)
            partial_failures.append(f"verification: {e}")

    # ── Регенерация HTML/MD после верификации ──

    try:
        regenerate_html_from_result(enriched_dict, html_path, doc_name=doc_name)
    except Exception as e:
        logger.warning(f"HTML regeneration error: {e}")
        partial_failures.append(f"regen_html: {e}")

    try:
        regenerate_md_from_result(enriched_dict, md_path, doc_name=doc_name)
    except Exception as e:
        logger.warning(f"MD regeneration error: {e}")
        partial_failures.append(f"regen_md: {e}")

    if partial_failures:
        logger.warning(f"Partial failures: {partial_failures}")

    return ResultPipelineOutput(
        annotation_dict=ann_dict,
        enriched_dict=enriched_dict,
        html_path=html_path,
        md_path=md_path,
        html_stats=html_stats,
        md_stats=md_stats,
        partial_failures=partial_failures,
    )
