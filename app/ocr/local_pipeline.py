"""
Локальный OCR pipeline — замена distributed Celery+Redis+HTTP.

Переиспользует серверную OCR-логику (pdf_twopass, backend_factory,
task_results, block_verification) без зависимостей на Celery/Redis/R2/Supabase.

Каждая задача выполняется в отдельном multiprocessing.Process
для изоляции утечек памяти (аналог Celery prefork worker_max_tasks=3).
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class LocalOcrResult:
    """Результат локального OCR."""

    status: str  # "done" | "partial" | "error"
    recognized: int = 0
    total_blocks: int = 0
    error_count: int = 0
    error_message: Optional[str] = None
    duration_seconds: float = 0.0
    result_files: dict[str, str] = field(default_factory=dict)


# Callback types
ProgressCallback = Callable[[float, str], None]  # (progress 0-1, message)
CancelCheck = Callable[[], bool]  # returns True if should cancel


def run_local_ocr(
    pdf_path: str | Path,
    blocks_data: list[dict],
    output_dir: str | Path,
    *,
    engine: str = "lmstudio",
    chandra_base_url: str = "",
    qwen_base_url: str = "",
    chandra_http_timeout: int = 300,
    qwen_http_timeout: int = 300,
    text_model: str | None = None,
    image_model: str | None = None,
    stamp_model: str | None = None,
    max_concurrent: int = 2,
    timeout_seconds: int = 3600,
    on_progress: ProgressCallback | None = None,
    check_cancelled: CancelCheck | None = None,
    is_correction_mode: bool = False,
    node_id: str | None = None,
) -> LocalOcrResult:
    """
    Запускает полный OCR pipeline локально.

    Это главная функция, вызываемая в subprocess (multiprocessing.Process).
    Переиспользует серверные модули напрямую.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    work_dir = Path(tempfile.mkdtemp(prefix="ocr_local_"))
    crops_dir = work_dir / "crops"
    crops_dir.mkdir()

    def _progress(value: float, msg: str):
        if on_progress:
            try:
                on_progress(value, msg)
            except Exception:
                pass

    def _is_cancelled() -> bool:
        if check_cancelled:
            try:
                return check_cancelled()
            except Exception:
                return False
        return False

    text_backend = None
    image_backend = None
    stamp_backend = None

    try:
        # ── Parse blocks ─────────────────────────────────────────
        from rd_core.models import Block

        blocks = [Block.from_dict(b, migrate_ids=False)[0] for b in blocks_data]
        total_blocks = len(blocks)

        if total_blocks == 0:
            return LocalOcrResult(
                status="done",
                total_blocks=0,
                recognized=0,
                duration_seconds=time.time() - start_time,
            )

        _progress(0.05, f"Подготовка: {total_blocks} блоков")

        if _is_cancelled():
            return LocalOcrResult(status="error", error_message="Отменено")

        # ── Create backends ──────────────────────────────────────
        from rd_core.ocr import create_ocr_engine

        chandra_url = chandra_base_url or "http://localhost:1234"
        qwen_url = qwen_base_url or chandra_url

        text_backend = create_ocr_engine(
            "chandra",
            base_url=chandra_url,
            http_timeout=chandra_http_timeout,
        )
        try:
            text_backend.preload()
        except Exception as e:
            logger.warning(f"Preload chandra failed (non-fatal): {e}")

        image_backend = create_ocr_engine(
            "qwen",
            base_url=qwen_url,
            http_timeout=qwen_http_timeout,
        )
        stamp_backend = create_ocr_engine(
            "qwen",
            base_url=qwen_url,
            http_timeout=qwen_http_timeout,
        )

        # Deadline для бэкендов
        deadline = time.time() + timeout_seconds
        for backend in (text_backend, image_backend, stamp_backend):
            if hasattr(backend, "set_deadline"):
                backend.set_deadline(deadline)

        _progress(0.1, "Бэкенды готовы")

        # ── PASS 1: Crop extraction ─────────────────────────────
        from services.remote_ocr.server.pdf_twopass import (
            cleanup_manifest_files,
            pass1_prepare_crops,
        )

        def on_pass1_progress(current, total):
            _progress(0.1 + 0.3 * (current / total), f"PASS 1: стр. {current}/{total}")

        manifest = pass1_prepare_crops(
            str(pdf_path),
            blocks,
            str(crops_dir),
            save_image_crops_as_pdf=True,
            on_progress=on_pass1_progress,
            should_stop=_is_cancelled,
        )

        if not manifest or len(manifest.blocks) == 0:
            return LocalOcrResult(
                status="error",
                total_blocks=total_blocks,
                error_message="PASS1: нет блоков для обработки",
                duration_seconds=time.time() - start_time,
            )

        if _is_cancelled():
            return LocalOcrResult(status="error", error_message="Отменено")

        _progress(0.4, f"PASS 1 завершён: {len(manifest.blocks)} block crops")

        # ── PASS 2: Recognition (async) ──────────────────────────
        import asyncio

        from services.remote_ocr.server.pdf_twopass.pass2_ocr_async import (
            pass2_ocr_from_manifest_async,
        )

        def on_pass2_progress(current, total, block_info=None):
            progress = 0.4 + 0.5 * (current / total) if total > 0 else 0.4
            msg = f"PASS 2: {block_info} ({current}/{total})" if block_info else f"PASS 2: {current}/{total}"
            _progress(progress, msg)

        # Model swap callbacks (если один LM Studio инстанс)
        def _swap_to_stamp():
            """chandra → stamp model (qwen3.5-9b)"""
            if qwen_url == chandra_url:
                logger.info("Model swap: chandra → stamp")
                try:
                    text_backend.unload_model()
                except Exception:
                    pass
            try:
                stamp_backend.preload()
            except Exception:
                pass

        def _swap_to_image():
            """stamp model → image model (qwen3.5-27b)"""
            if qwen_url == chandra_url:
                logger.info("Model swap: stamp → image")
                try:
                    stamp_backend.unload_model()
                except Exception:
                    pass
            try:
                image_backend.preload()
            except Exception:
                pass

        asyncio.run(
            pass2_ocr_from_manifest_async(
                manifest,
                blocks,
                text_backend,
                image_backend,
                stamp_backend,
                str(pdf_path),
                on_progress=on_pass2_progress,
                check_paused=_is_cancelled,
                max_concurrent=max_concurrent,
                checkpoint=None,
                work_dir=work_dir,
                deadline=deadline,
                before_stamp_phase=_swap_to_stamp,
                before_image_phase=_swap_to_image,
            )
        )

        if manifest:
            cleanup_manifest_files(manifest)

        if _is_cancelled():
            return LocalOcrResult(status="error", error_message="Отменено")

        _progress(0.9, "Распознавание завершено")

        # ── Выгрузить Qwen перед верификацией (чтобы Chandra не грузилась поверх) ──
        if image_backend and hasattr(image_backend, "unload_model"):
            try:
                image_backend.unload_model()
            except Exception:
                pass

        # ── Generate results ─────────────────────────────────────
        _progress(0.92, "Генерация результатов...")

        _generate_local_results(
            pdf_path, blocks, work_dir, output_dir,
            text_backend=text_backend,
            is_correction_mode=is_correction_mode,
            deadline=deadline,
            node_id=node_id,
        )

        _progress(0.98, "Результаты сохранены")

        # ── Compute stats ────────────────────────────────────────
        from services.remote_ocr.server.ocr_constants import (
            is_error as _is_error,
            is_success as _is_success,
        )

        recognized = sum(1 for b in blocks if _is_success(b.ocr_text))
        error_count = sum(1 for b in blocks if _is_error(b.ocr_text))
        coverage = recognized / total_blocks if total_blocks > 0 else 0

        if recognized == 0:
            final_status = "error"
        elif coverage < 0.9:
            final_status = "partial"
        else:
            final_status = "done"

        # Собираем result_files
        pdf_stem = pdf_path.stem
        result_files = {}
        for name in (
            "annotation.json",
            f"{pdf_stem}_ocr.html",
            f"{pdf_stem}_document.md",
            f"{pdf_stem}_result.json",
        ):
            path = output_dir / name
            if path.exists():
                result_files[name] = str(path)

        _progress(1.0, f"Готово: {recognized}/{total_blocks} блоков")

        return LocalOcrResult(
            status=final_status,
            recognized=recognized,
            total_blocks=total_blocks,
            error_count=error_count,
            duration_seconds=time.time() - start_time,
            result_files=result_files,
        )

    except Exception as e:
        logger.error(f"OCR pipeline error: {e}", exc_info=True)
        return LocalOcrResult(
            status="error",
            total_blocks=len(blocks_data),
            error_message=str(e),
            duration_seconds=time.time() - start_time,
        )

    finally:
        # Выгружаем модели из LM Studio
        for backend in (image_backend, text_backend):
            try:
                if backend and hasattr(backend, "unload_model"):
                    backend.unload_model()
            except Exception:
                pass

        # Cleanup temp dir
        if work_dir.exists():
            try:
                shutil.rmtree(work_dir)
            except Exception:
                pass


def _generate_local_results(
    pdf_path: Path,
    blocks: list,
    work_dir: Path,
    output_dir: Path,
    *,
    text_backend=None,
    is_correction_mode: bool = False,
    deadline: float | None = None,
    node_id: str | None = None,
):
    """Генерация файлов результатов (annotation.json, HTML, MD, export_report.json)."""
    from datetime import datetime, timezone

    from rd_core.models import Block, Document, Page, ShapeType
    from rd_core.models.enums import BlockType
    from rd_core.ocr import generate_html_from_pages, generate_md_from_pages
    from rd_core.ocr_result import is_suspicious_output, make_error as make_ocr_error

    from services.remote_ocr.server.pdf_streaming_core import get_page_dimensions_streaming
    from services.remote_ocr.server.text_ocr_quality import filter_mixed_text_output

    engine = "lmstudio"

    # Filter mixed-text Chandra artifacts
    for block in blocks:
        if block.block_type == BlockType.TEXT and block.ocr_text:
            block.ocr_text, _ = filter_mixed_text_output(block.ocr_text, engine)

    # Detect suspicious output → error marker
    for block in blocks:
        if block.block_type == BlockType.TEXT and block.ocr_text:
            suspicious, reason = is_suspicious_output(block.ocr_text)
            if suspicious:
                block.ocr_text = make_ocr_error(f"suspicious OCR output: {reason}")

    # Group blocks by page
    blocks_by_page: dict[int, list[tuple[int, object]]] = {}
    for idx, b in enumerate(blocks):
        blocks_by_page.setdefault(b.page_index, []).append((idx, b))

    page_dims = get_page_dimensions_streaming(str(pdf_path))

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

    doc_name = pdf_path.name
    doc = Document(pdf_path=doc_name, pages=pages)

    # annotation.json → output_dir
    ann_dict = doc.to_dict()
    annotation_path = output_dir / "annotation.json"
    with open(annotation_path, "w", encoding="utf-8") as f:
        json.dump(ann_dict, f, ensure_ascii=False, indent=2)

    pdf_stem = pdf_path.stem
    html_stats = None
    md_stats = None

    # HTML
    html_path = output_dir / f"{pdf_stem}_ocr.html"
    try:
        _, html_stats = generate_html_from_pages(pages, str(html_path), doc_name=doc_name)
    except Exception as e:
        logger.warning(f"HTML generation error: {e}")

    # Markdown
    md_path = output_dir / f"{pdf_stem}_document.md"
    try:
        _, md_stats = generate_md_from_pages(pages, str(md_path), doc_name=doc_name)
    except Exception as e:
        logger.warning(f"MD generation error: {e}")

    # Block verification (retry missing blocks via enriched dict)
    if text_backend:
        try:
            from services.remote_ocr.server.block_verification import verify_and_retry_missing_blocks
            from services.remote_ocr.server.ocr_result_merger import (
                enrich_annotation_dict,
                regenerate_html_from_result,
                regenerate_md_from_result,
            )

            # Обогащаем annotation dict (добавляем ocr_html, ocr_json, ocr_meta)
            html_text = ""
            if html_path.exists():
                with open(html_path, "r", encoding="utf-8") as f:
                    html_text = f.read()
            enriched_dict = enrich_annotation_dict(ann_dict, html_text, project_name=doc_name)

            # Верификация и повторное распознавание пропущенных блоков
            enriched_dict = verify_and_retry_missing_blocks(
                enriched_dict, pdf_path, work_dir, text_backend,
                deadline=deadline,
            )

            # Перезаписать annotation.json из enriched_dict (после верификации)
            with open(annotation_path, "w", encoding="utf-8") as f:
                json.dump(enriched_dict, f, ensure_ascii=False, indent=2)

            # Перегенерируем HTML/MD из обновлённого enriched dict
            try:
                regenerate_html_from_result(enriched_dict, html_path, doc_name=doc_name)
            except Exception as e:
                logger.warning(f"HTML regeneration after verification error: {e}")

            try:
                regenerate_md_from_result(enriched_dict, md_path, doc_name=doc_name)
            except Exception as e:
                logger.warning(f"MD regeneration after verification error: {e}")

        except Exception as e:
            logger.warning(f"Block verification error: {e}")

    # Export report — машинно-читаемая статистика экспорта
    try:
        report = {
            "pdf_name": doc_name,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "output_dir": str(output_dir),
        }
        if html_stats:
            report["html"] = html_stats.to_dict()
        if md_stats:
            report["md"] = md_stats.to_dict()

        report_path = output_dir / f"{pdf_stem}_export_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"Export report: {report_path}")
    except Exception as e:
        logger.warning(f"Export report generation error: {e}")

    # Sync артефактов в tree_docs/{node_id} (если запуск с node_id)
    _sync_results_to_tree(node_id, pdf_stem, output_dir)


def _sync_results_to_tree(node_id: str | None, pdf_stem: str, output_dir: Path) -> None:
    """Загрузить свежие OCR-артефакты в R2 и обновить node_files."""
    if not node_id:
        return
    try:
        from app.services import get_r2, get_tree_client
        from app.tree_models import FileType
        from rd_core.r2_utils import invalidate_r2_cache

        r2 = get_r2()
        tc = get_tree_client()
        r2_prefix = f"tree_docs/{node_id}"

        files_to_sync = [
            (f"{pdf_stem}_ocr.html", FileType.OCR_HTML, "text/html"),
            (f"{pdf_stem}_document.md", FileType.RESULT_MD, "text/markdown"),
        ]

        for filename, file_type, mime in files_to_sync:
            local_path = output_dir / filename
            if not local_path.exists():
                continue
            r2_key = f"{r2_prefix}/{filename}"
            r2.upload_file(str(local_path), r2_key, content_type=mime)
            tc.upsert_node_file(
                node_id=node_id,
                file_type=file_type,
                r2_key=r2_key,
                file_name=filename,
                file_size=local_path.stat().st_size,
                mime_type=mime,
            )
            invalidate_r2_cache(r2_key)

        invalidate_r2_cache(f"{r2_prefix}/", prefix=True)
        logger.info(f"Synced OCR results to tree: node_id={node_id}")
    except Exception as e:
        logger.warning(f"Sync to tree failed (non-fatal): {e}")
