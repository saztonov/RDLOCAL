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
    # Документные цифры (для correction mode)
    recognized_document: int = 0
    document_total_blocks: int = 0


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
    full_blocks_data: list[dict] | None = None,
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
        from services.remote_ocr.server.backend_factory import (
            _build_chandra_config,
            _build_qwen_config,
            _build_stamp_config,
        )
        from services.remote_ocr.server.settings import settings

        chandra_url = chandra_base_url or settings.chandra_base_url or os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234")
        qwen_url = qwen_base_url or settings.qwen_base_url or chandra_url

        text_backend = create_ocr_engine(
            "chandra",
            base_url=chandra_url,
            http_timeout=settings.chandra_http_timeout,
            model_config=_build_chandra_config(),
        )
        try:
            text_backend.preload()
        except Exception as e:
            logger.warning(f"Preload chandra failed (non-fatal): {e}")

        image_backend = create_ocr_engine(
            "qwen",
            base_url=qwen_url,
            http_timeout=settings.qwen_http_timeout,
            model_config=_build_qwen_config(),
        )
        stamp_backend = create_ocr_engine(
            "qwen",
            base_url=qwen_url,
            http_timeout=settings.stamp_http_timeout,
            model_config=_build_stamp_config(),
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
        def _model_swap_with_retry(
            swap_name: str,
            unload_backend,
            load_backend,
            same_server: bool,
            max_retries: int = 2,
            retry_delay: float = 5.0,
        ):
            """Выполнить swap моделей с retry. Raises RuntimeError при финальной ошибке."""
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    if same_server and unload_backend is not None:
                        unload_backend.unload_model()
                    load_backend.preload()
                    logger.info(f"Model swap '{swap_name}' OK (attempt {attempt + 1})")
                    return
                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"Model swap '{swap_name}' failed "
                        f"(attempt {attempt + 1}/{max_retries + 1}): {e}"
                    )
                    if attempt < max_retries:
                        time.sleep(retry_delay)

            raise RuntimeError(
                f"Model swap '{swap_name}' failed after "
                f"{max_retries + 1} attempts: {last_error}"
            )

        def _swap_to_stamp():
            _model_swap_with_retry(
                "chandra → stamp", text_backend, stamp_backend,
                same_server=(qwen_url == chandra_url),
            )

        def _swap_to_image():
            _model_swap_with_retry(
                "stamp → image", stamp_backend, image_backend,
                same_server=(qwen_url == chandra_url),
            )

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

        if _is_cancelled():
            return LocalOcrResult(status="error", error_message="Отменено")

        _progress(0.88, "Распознавание завершено")

        # ── Копируем PDF кропы в crops_final (до cleanup) ──
        from services.remote_ocr.server.task_upload import copy_crops_to_final
        copy_crops_to_final(work_dir, blocks)

        _progress(0.9, "Кропы подготовлены")

        # ── Model swap: вернуться к text backend перед верификацией ──
        def _swap_to_text():
            _model_swap_with_retry(
                "qwen → chandra", image_backend, text_backend,
                same_server=(qwen_url == chandra_url),
            )

        _swap_to_text()

        # ── Generate results ─────────────────────────────────────
        _progress(0.92, "Генерация результатов...")

        _generate_local_results(
            pdf_path, blocks, work_dir, output_dir,
            text_backend=text_backend,
            image_backend=image_backend,
            stamp_backend=stamp_backend,
            before_stamp_phase=_swap_to_stamp,
            before_image_phase=_swap_to_image,
            is_correction_mode=is_correction_mode,
            deadline=deadline,
            node_id=node_id,
            full_blocks_data=full_blocks_data,
        )

        # ── Очистка PNG кропов (PDF кропы в crops_final остаются для R2 upload) ──
        if manifest:
            cleanup_manifest_files(manifest)

        _progress(0.98, "Результаты сохранены")

        # ── Compute stats ────────────────────────────────────────
        from services.remote_ocr.server.ocr_constants import (
            is_error as _is_error,
            is_success as _is_success,
        )

        # Счётчики по обработанному subset
        recognized = sum(1 for b in blocks if _is_success(b.ocr_text))
        error_count = sum(1 for b in blocks if _is_error(b.ocr_text))
        coverage = recognized / total_blocks if total_blocks > 0 else 0

        # Документные счётчики (для correction mode — из merged annotation)
        recognized_document = recognized
        document_total_blocks = total_blocks
        if is_correction_mode and full_blocks_data:
            try:
                ann_path = output_dir / "annotation.json"
                if ann_path.exists():
                    with open(ann_path, "r", encoding="utf-8") as _af:
                        ann_data = json.load(_af)
                    all_ann_blocks = []
                    for pg in ann_data.get("pages", []):
                        all_ann_blocks.extend(pg.get("blocks", []))
                    document_total_blocks = len(all_ann_blocks)
                    recognized_document = sum(
                        1 for b in all_ann_blocks
                        if _is_success(b.get("ocr_text", ""))
                    )
            except Exception as exc:
                logger.warning(f"Failed to recount doc stats from annotation: {exc}")

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
            recognized_document=recognized_document,
            document_total_blocks=document_total_blocks,
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
    image_backend=None,
    stamp_backend=None,
    before_stamp_phase=None,
    before_image_phase=None,
    is_correction_mode: bool = False,
    deadline: float | None = None,
    node_id: str | None = None,
    full_blocks_data: list[dict] | None = None,
):
    """Генерация файлов результатов (annotation.json, HTML, MD, export_report.json).

    Делегирует основную логику в rd_core.ocr.result_pipeline.generate_ocr_results().
    """
    from datetime import datetime, timezone

    from rd_core.ocr.result_pipeline import generate_ocr_results
    from services.remote_ocr.server.pdf_streaming_core import get_page_dimensions_streaming

    page_dims = get_page_dimensions_streaming(str(pdf_path))
    r2_project_name = node_id or None
    doc_name = pdf_path.name
    pdf_stem = pdf_path.stem

    result = generate_ocr_results(
        pdf_path,
        blocks,
        work_dir,
        output_dir,
        page_dims=page_dims,
        engine="lmstudio",
        doc_name=doc_name,
        project_name=r2_project_name,
        is_correction_mode=is_correction_mode,
        full_blocks_data=full_blocks_data,
        text_backend=text_backend,
        image_backend=image_backend,
        stamp_backend=stamp_backend,
        deadline=deadline,
        before_stamp_phase=before_stamp_phase,
        before_image_phase=before_image_phase,
    )

    # annotation.json → output_dir
    annotation_path = output_dir / "annotation.json"
    with open(annotation_path, "w", encoding="utf-8") as f:
        json.dump(result.enriched_dict, f, ensure_ascii=False, indent=2)

    # Export report — машинно-читаемая статистика экспорта
    try:
        report = {
            "pdf_name": doc_name,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "output_dir": str(output_dir),
        }
        if result.html_stats:
            report["html"] = result.html_stats.to_dict()
        if result.md_stats:
            report["md"] = result.md_stats.to_dict()

        report_path = output_dir / f"{pdf_stem}_export_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"Export report: {report_path}")
    except Exception as e:
        logger.warning(f"Export report generation error: {e}")

    # Sync артефактов в tree_docs/{node_id} (если запуск с node_id)
    _sync_results_to_tree(
        node_id, pdf_stem, output_dir, work_dir,
        is_correction_mode=is_correction_mode,
    )


def _purge_ocr_artifacts(node_id: str, r2_prefix: str) -> None:
    """Удалить OCR-артефакты узла (crop, ocr_html, result_md, crops_folder) из R2 и БД.

    Не трогает исходный PDF и annotations.
    Вызывается только при полном перераспознавании (не correction mode).
    """
    from app.services import get_r2, get_tree_client
    from app.tree_models import FileType
    from rd_core.r2_utils import invalidate_r2_cache

    ocr_types = {
        FileType.CROP.value, FileType.OCR_HTML.value,
        FileType.RESULT_MD.value, FileType.CROPS_FOLDER.value,
    }

    r2 = get_r2()
    tc = get_tree_client()

    # 1. Получить все node_files для узла
    try:
        node_files = tc.get_node_files(node_id)
    except Exception as e:
        logger.warning(f"purge_ocr_artifacts: failed to get node_files: {e}")
        node_files = []

    # 2. Отфильтровать OCR-артефакты
    r2_keys_to_delete = []
    db_ids_to_delete = []
    for nf in node_files:
        ft = nf.file_type if isinstance(nf.file_type, str) else nf.file_type.value
        if ft in ocr_types:
            if nf.r2_key:
                r2_keys_to_delete.append(nf.r2_key)
            db_ids_to_delete.append(nf.id)

    # 3. Удалить из R2 по собранным ключам
    if r2_keys_to_delete:
        try:
            deleted_keys, errors = r2.delete_objects_batch(r2_keys_to_delete)
            logger.info(
                f"purge_ocr_artifacts: deleted {len(deleted_keys)} R2 objects "
                f"from node_files keys"
            )
        except Exception as e:
            logger.warning(f"purge_ocr_artifacts: R2 batch delete error: {e}")

    # 4. Удалить orphan R2-объекты по префиксу crops/
    crops_prefix = f"{r2_prefix}/crops/"
    try:
        deleted_orphans = r2.delete_by_prefix(crops_prefix)
        if deleted_orphans:
            logger.info(
                f"purge_ocr_artifacts: deleted {deleted_orphans} orphan R2 crops "
                f"by prefix {crops_prefix}"
            )
    except Exception as e:
        logger.warning(f"purge_ocr_artifacts: R2 prefix delete error: {e}")

    # 5. Удалить записи из БД
    for file_id in db_ids_to_delete:
        try:
            tc.delete_node_file(file_id)
        except Exception as e:
            logger.warning(f"purge_ocr_artifacts: failed to delete DB record {file_id}: {e}")

    if db_ids_to_delete:
        logger.info(
            f"purge_ocr_artifacts: deleted {len(db_ids_to_delete)} DB records "
            f"for node_id={node_id}"
        )

    # 6. Инвалидировать кэш
    invalidate_r2_cache(f"{r2_prefix}/", prefix=True)


def _sync_results_to_tree(
    node_id: str | None,
    pdf_stem: str,
    output_dir: Path,
    work_dir: Path | None = None,
    *,
    is_correction_mode: bool = False,
) -> None:
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

        # При полном перераспознавании — очистить старые OCR-артефакты
        if not is_correction_mode:
            try:
                _purge_ocr_artifacts(node_id, r2_prefix)
            except Exception as e:
                logger.warning(f"Pre-sync purge failed (non-fatal): {e}")

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

        # Загрузка crop PDF в R2 (из crops_final)
        crops_final = work_dir / "crops_final" if work_dir else None
        if crops_final and crops_final.exists():
            crop_count = 0
            for crop_file in crops_final.glob("*.pdf"):
                r2_key = f"{r2_prefix}/crops/{crop_file.name}"
                r2.upload_file(str(crop_file), r2_key, content_type="application/pdf")
                tc.upsert_node_file(
                    node_id=node_id,
                    file_type=FileType.CROP,
                    r2_key=r2_key,
                    file_name=crop_file.name,
                    file_size=crop_file.stat().st_size,
                    mime_type="application/pdf",
                )
                crop_count += 1
            if crop_count:
                logger.info(f"Uploaded {crop_count} crop PDFs to R2: {r2_prefix}/crops/")

        invalidate_r2_cache(f"{r2_prefix}/", prefix=True)
        logger.info(f"Synced OCR results to tree: node_id={node_id}")
    except Exception as e:
        logger.warning(f"Sync to tree failed (non-fatal): {e}")
