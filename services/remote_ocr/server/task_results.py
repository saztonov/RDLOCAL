"""Генерация результатов OCR (серверная обёртка).

Основная логика генерации — в rd_core.ocr.result_pipeline.generate_ocr_results().
Этот модуль добавляет серверную специфику: Job, Supabase, R2, correction mode через Supabase.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .logging_config import get_logger
from .r2_keys import resolve_r2_prefix
from .storage import Job, get_node_full_path

logger = get_logger(__name__)


def _build_verification_config():
    """Построить VerificationConfig из серверных settings."""
    from rd_core.ocr.block_verification import VerificationConfig

    from .settings import settings

    def _flush_progress(job_id: str) -> None:
        try:
            from .debounced_updater import get_debounced_updater
            get_debounced_updater(job_id).flush()
        except Exception:
            pass

    def _prompt_builder(prompt_data, doc_name, page_index, block_id, category_code):
        from .worker_prompts import fill_image_prompt_variables
        return fill_image_prompt_variables(
            prompt_data=prompt_data,
            doc_name=doc_name,
            page_index=page_index,
            block_id=block_id,
            category_code=category_code,
            engine=None,
        )

    def _stamp_json_parser(ocr_text: str):
        from .pdf_twopass.pass2_images import _parse_stamp_json
        return _parse_stamp_json(ocr_text)

    from .pdf_streaming_core import StreamingPDFProcessor

    return VerificationConfig(
        chandra_retry_delay=settings.chandra_retry_delay,
        max_retry_blocks=settings.max_retry_blocks,
        verification_timeout_minutes=settings.verification_timeout_minutes,
        on_flush_progress=_flush_progress,
        prompt_builder=_prompt_builder,
        stamp_json_parser=_stamp_json_parser,
        pdf_processor_factory=StreamingPDFProcessor,
    )


def generate_results(
    job: Job,
    pdf_path: Path,
    blocks: list,
    work_dir: Path,
    ocr_backend=None,
    text_fallback_backend=None,
    image_backend=None,
    stamp_backend=None,
    on_verification_progress: Callable[[int, int], None] = None,
    verification_deadline: float | None = None,
    before_stamp_phase: Callable = None,
    before_image_phase: Callable = None,
) -> str:
    """Генерация результатов OCR (серверная обёртка).

    Делегирует основную логику в rd_core.ocr.result_pipeline.generate_ocr_results().
    Добавляет: Job-специфику, Supabase сохранение, correction mode через Supabase.
    """
    from rd_core.ocr.result_pipeline import generate_ocr_results

    from .node_storage.ocr_registry import _save_annotation_to_db
    from .pdf_streaming_core import get_page_dimensions_streaming

    # Проверяем режим корректировки
    is_correction_mode = job.settings.is_correction_mode if job.settings else False
    if is_correction_mode and job.node_id:
        logger.info(f"[{job.id}] Correction mode detected, using merge strategy")
        return _generate_correction_results(
            job, pdf_path, blocks, work_dir, ocr_backend, on_verification_progress
        )

    # ── Resolve server-specific context ──

    r2_prefix = resolve_r2_prefix(job)

    if r2_prefix.startswith("tree_docs/"):
        project_name = r2_prefix[len("tree_docs/"):]
    else:
        project_name = job.node_id if job.node_id else job.id

    if job.node_id:
        full_path = get_node_full_path(job.node_id)
        doc_name = full_path if full_path else pdf_path.name
    else:
        doc_name = pdf_path.name

    page_dims = get_page_dimensions_streaming(str(pdf_path))
    verification_config = _build_verification_config()

    # ── Вызов единой генерации ──

    result = generate_ocr_results(
        pdf_path,
        blocks,
        work_dir,
        work_dir,  # output_dir = work_dir для сервера
        page_dims=page_dims,
        engine=job.engine or "lmstudio",
        doc_name=doc_name,
        project_name=project_name,
        text_backend=ocr_backend,
        text_fallback_backend=text_fallback_backend,
        image_backend=image_backend,
        stamp_backend=stamp_backend,
        verification_config=verification_config,
        on_verification_progress=on_verification_progress,
        job_id=job.id,
        deadline=verification_deadline,
        before_stamp_phase=before_stamp_phase,
        before_image_phase=before_image_phase,
        html_filename="ocr_result.html",
        md_filename="document.md",
    )

    # ── Сохранение enriched dict в Supabase ──

    if job.node_id:
        try:
            _save_annotation_to_db(job.node_id, result.enriched_dict)
            logger.info(f"Enriched annotation saved to Supabase: node_id={job.node_id}")
        except Exception as e:
            logger.warning(f"Ошибка сохранения annotation в Supabase: {e}")
            result.partial_failures.append(f"save_db: {e}")

    if result.partial_failures:
        logger.warning(
            f"Частичные ошибки постобработки: {result.partial_failures}",
            extra={"job_id": job.id, "event": "partial_failures"},
        )

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
    Merge новых OCR результатов с существующим enriched dict из Supabase.

    Эта функция содержит Supabase-специфичную логику merge, которая
    не может быть обобщена в rd_core.
    """
    from rd_core.ocr import generate_html_from_pages
    from rd_core.ocr.generator_common import sanitize_html
    from rd_core.ocr.ocr_html_parser import build_segments_from_html
    from rd_core.ocr.ocr_result_merger import regenerate_html_from_result, regenerate_md_from_result

    from .node_storage.ocr_registry import _load_annotation_from_db, _save_annotation_to_db

    r2_prefix = resolve_r2_prefix(job)

    logger.info(f"[{job.id}] Correction mode: merging results, r2_prefix={r2_prefix}")

    # 1. Загрузить существующий enriched dict из Supabase
    old_result = None
    if job.node_id:
        old_result = _load_annotation_from_db(job.node_id)

    if not old_result:
        logger.warning(
            f"[{job.id}] No existing annotation in Supabase for node_id={job.node_id}, "
            "falling back to full generation"
        )
        # Отключаем is_correction_mode и вызываем обычную генерацию
        if job.settings:
            job.settings.is_correction_mode = False
        return generate_results(
            job, pdf_path, blocks, work_dir, ocr_backend, on_verification_progress
        )

    logger.info(f"[{job.id}] Loaded existing annotation with {len(old_result.get('pages', []))} pages")

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

        generate_html_from_pages(temp_pages, str(temp_html_path), doc_name=doc_name)  # stats unused

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
            # page_index в result 1-based, в блоках 0-based
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

    # 7. Сохраняем enriched dict в Supabase
    if job.node_id:
        try:
            _save_annotation_to_db(job.node_id, old_result)
            logger.info(f"[{job.id}] Saved merged annotation to Supabase")
        except Exception as e:
            logger.warning(f"[{job.id}] Error saving annotation to Supabase: {e}")

    # 8. Регенерируем HTML из enriched dict
    html_path = work_dir / "ocr_result.html"
    regenerate_html_from_result(old_result, html_path, doc_name=doc_name)

    # 9. Регенерируем MD из enriched dict
    md_path = work_dir / "document.md"
    regenerate_md_from_result(old_result, md_path, doc_name=doc_name)

    logger.info(
        f"[{job.id}] Correction results generated: "
        f"updated={updated_count}, new={new_blocks_added}"
    )

    return r2_prefix


def _build_pages_from_blocks(blocks: list, pdf_path: Path) -> list:
    """Построить список Page объектов и�� блоков для генерации HTML."""
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
