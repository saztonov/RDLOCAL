"""Стадии OCR-задачи: validate → bootstrap → ocr → generate → upload → register → finalize.

Каждая стадия — чистая функция, получающая JobContext и возвращающая результат.
tasks.py остаётся тонким entrypoint, делегирующим сюда.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .debounced_updater import cleanup_updater, get_debounced_updater
from .execution_lock import acquire_execution_lock, release_execution_lock
from .job_context import JobContext, JobSkipped, JobValidationError
from .logging_config import get_logger
from .lmstudio_lifecycle import acquire_chandra, release_chandra
from .memory_utils import force_gc, log_memory_delta
from .ocr_constants import is_error, is_non_retriable, is_success, is_suspicious_output
from .settings import settings
from .storage import get_job, register_ocr_results_to_node, update_job_status
from .storage_jobs import increment_retry_count, set_job_started_at
from .task_helpers import check_paused, create_empty_result, download_job_files
from .task_ocr_twopass import run_two_pass_ocr
from .task_results import generate_results
from .task_upload import upload_results_to_r2
from .worker_pdf import clear_page_size_cache

logger = get_logger(__name__)


# ── Stage 1: Validate ────────────────────────────────────────────────

def validate_job(job_id: str, celery_task_id: str) -> "Job":
    """Guard checks: дубли, cancelled/done, max retries, max runtime.

    Returns:
        Validated Job object.

    Raises:
        JobSkipped: если задачу не нужно выполнять.
        JobValidationError: если задача невалидна.
    """

    job = get_job(job_id, with_files=True, with_settings=True)
    if not job:
        raise JobValidationError(f"Задача {job_id} не найдена")

    # Task guard: stale task после reorder
    if job.celery_task_id and job.celery_task_id != celery_task_id:
        raise JobSkipped("aborted", f"Stale task (reordered), expected {job.celery_task_id}")

    if job.status in ("cancelled", "done"):
        raise JobSkipped("skipped", f"Job already {job.status}")

    # Защита от зацикливания: количество попыток
    if job.retry_count >= settings.job_max_retries:
        error_msg = f"Превышен лимит попыток: {job.retry_count}/{settings.job_max_retries}"
        update_job_status(
            job_id, "error",
            error_message=error_msg,
            status_message="❌ Превышен лимит попыток",
        )
        raise JobValidationError(error_msg)

    # Защита от зацикливания: общее время выполнения
    if job.started_at:
        try:
            started = datetime.fromisoformat(job.started_at.replace("Z", "+00:00"))
            runtime_hours = (datetime.now(timezone.utc) - started).total_seconds() / 3600
            # LM Studio движки (Chandra/Qwen) получают увеличенный лимит
            is_lmstudio = job.engine == "chandra"
            max_hours = (
                settings.job_max_runtime_hours_lmstudio
                if is_lmstudio
                else settings.job_max_runtime_hours
            )
            if runtime_hours > max_hours:
                error_msg = (
                    f"Превышено время выполнения: {runtime_hours:.1f}h "
                    f"(лимит: {max_hours}h)"
                )
                update_job_status(
                    job_id, "error",
                    error_message=error_msg,
                    status_message="❌ Превышено время выполнения",
                )
                raise JobValidationError(error_msg)
        except (ValueError, TypeError) as e:
            logger.warning(f"Job {job_id}: ошибка парсинга started_at ({job.started_at}): {e}")

    # Execution lock: предотвращение параллельной обработки одного job
    # (защита от duplicate delivery при visibility_timeout / requeue)
    if not acquire_execution_lock(job_id, celery_task_id):
        raise JobSkipped("duplicate", f"Job already executing (duplicate delivery)")

    # Инкрементируем retry_count и ставим started_at
    new_retry_count = increment_retry_count(job_id)
    logger.info(f"Job {job_id}: попытка {new_retry_count}/{settings.job_max_retries}")

    if not job.started_at:
        set_job_started_at(job_id)

    if check_paused(job.id):
        raise JobSkipped("paused", "Job paused before start")

    return job


# ── Stage 2: Bootstrap ───────────────────────────────────────────────

def bootstrap_job(job, start_mem: float) -> JobContext:
    """Скачивание файлов, парсинг блоков, создание бэкендов, acquire LM Studio.

    Returns:
        Готовый JobContext со всеми ресурсами.

    Raises:
        JobSkipped: если нет блоков или задача на паузе.
        JobBootstrapError: при ошибке подготовки.
    """
    job_id = job.id
    update_job_status(job_id, "processing", progress=0.05, status_message="📥 Инициализация задачи...")

    # Временная директория
    work_dir = Path(tempfile.mkdtemp(prefix=f"ocr_job_{job_id}_"))
    crops_dir = work_dir / "crops"
    crops_dir.mkdir(exist_ok=True)

    logger.info(f"Задача {job_id}: скачивание файлов из R2...")
    update_job_status(job_id, "processing", progress=0.06, status_message="📥 Скачивание файлов из R2...")
    pdf_path, blocks_path = download_job_files(job, work_dir)
    log_memory_delta("После скачивания файлов", start_mem)

    # Парсинг блоков
    with open(blocks_path, "r", encoding="utf-8") as f:
        blocks_data = json.load(f)

    # annotation.json: {pdf_path, pages: [{blocks: [...]}]}
    if isinstance(blocks_data, dict) and "pages" in blocks_data:
        all_blocks = []
        for page in blocks_data.get("pages", []):
            all_blocks.extend(page.get("blocks", []))
        blocks_data = all_blocks

    if not blocks_data:
        update_job_status(job_id, "done", progress=1.0, status_message="✅ Нет блоков для распознавания")
        create_empty_result(job, work_dir, pdf_path)
        upload_results_to_r2(job, work_dir)
        raise JobSkipped("done", "No blocks to process")

    from rd_core.models import Block

    blocks = [Block.from_dict(b, migrate_ids=False)[0] for b in blocks_data]

    # Серверная фильтрация блоков:
    # - correction mode: только блоки с is_correction=True
    # - обычный mode: все блоки (клиент уже очистил ocr_text перед отправкой)
    is_correction = job.settings.is_correction_mode if job.settings else False
    if is_correction:
        all_count = len(blocks)
        blocks = [b for b in blocks if getattr(b, "is_correction", False)]
        logger.info(
            f"Задача {job_id}: correction mode — отфильтровано {len(blocks)}/{all_count} блоков",
            extra={"event": "blocks_filtered", "job_id": job_id, "filtered": len(blocks), "total": all_count},
        )

    total_blocks = len(blocks)

    logger.info(f"Задача {job_id}: {total_blocks} блоков")

    if check_paused(job.id):
        # Очистка work_dir при паузе
        shutil.rmtree(work_dir, ignore_errors=True)
        raise JobSkipped("paused", "Job paused during bootstrap")

    update_job_status(job_id, "processing", progress=0.1, status_message=f"⚙️ Подготовка: {total_blocks} блоков")

    # Создание бэкендов
    from .backend_factory import create_job_backends

    backends = create_job_backends(job)
    engine = backends.engine

    # Устанавливаем deadline для бэкендов (time budget)
    from .timeout_utils import calculate_dynamic_timeout

    soft_timeout, _ = calculate_dynamic_timeout(total_blocks)
    import time as _time

    deadline = _time.time() + soft_timeout
    for backend in (backends.text, backends.image, backends.stamp):
        if hasattr(backend, "set_deadline"):
            backend.set_deadline(deadline)

    logger.info(
        f"Бэкенды готовы: engine={engine}, блоков={total_blocks}",
        extra={
            "event": "task_backends_ready",
            "job_id": job_id,
            "engine": engine,
            "backend_type": type(backends.text).__name__,
            "total_blocks": total_blocks,
        },
    )

    # Acquire LM Studio
    lmstudio_acquired = False
    if backends.needs_lmstudio:
        if engine == "chandra":
            acquire_chandra(job_id)
        lmstudio_acquired = True

    return JobContext(
        job=job,
        job_id=job_id,
        work_dir=work_dir,
        crops_dir=crops_dir,
        pdf_path=pdf_path,
        blocks=blocks,
        total_blocks=total_blocks,
        engine=engine,
        backends=backends,
        lmstudio_acquired=lmstudio_acquired,
        start_mem=start_mem,
    )


# ── Stage 3: OCR ─────────────────────────────────────────────────────

def run_ocr(ctx: JobContext) -> None:
    """Двухпроходный OCR: pass1 (crops) → pass2 (recognition)."""
    # Вычисляем deadline для PASS2 time budget
    from .timeout_utils import calculate_dynamic_timeout
    soft_timeout, _ = calculate_dynamic_timeout(ctx.total_blocks)
    soft_timeout_at = ctx.start_time + soft_timeout

    run_two_pass_ocr(
        ctx.job,
        ctx.pdf_path,
        ctx.blocks,
        ctx.crops_dir,
        ctx.work_dir,
        ctx.backends.text,
        ctx.backends.image,
        ctx.backends.stamp,
        ctx.start_mem,
        engine=ctx.engine,
        soft_timeout_at=soft_timeout_at,
    )
    force_gc("после OCR обработки")


# ── Stage 4: Generate + Upload ────────────────────────────────────────

def generate_and_upload(ctx: JobContext) -> str:
    """Генерация результатов, верификация блоков, загрузка в R2.

    Returns:
        r2_prefix для загруженных файлов.
    """
    update_job_status(ctx.job_id, "processing", progress=0.92, status_message="📄 Генерация результатов...")

    # Callback для верификации (диапазон 0.92 → 0.97)
    def on_verification_progress(current: int, total: int):
        if total > 0:
            progress = 0.92 + 0.05 * (current / total)
            status_msg = f"🔍 Верификация блоков ({current + 1}/{total})"
        else:
            progress = 0.92
            status_msg = "🔍 Проверка распознанных блоков..."

        updater = get_debounced_updater(ctx.job_id)
        if total == 0 or current == 0 or current == total - 1 or current % 5 == 0:
            updater.force_update("processing", progress=progress, status_message=status_msg)
        else:
            update_job_status(ctx.job_id, "processing", progress=progress, status_message=status_msg)

    # Вычисляем deadline для верификации (= soft_timeout задачи)
    from .timeout_utils import calculate_dynamic_timeout as _calc_timeout
    _soft_timeout, _ = _calc_timeout(ctx.total_blocks)
    _verification_deadline = ctx.start_time + _soft_timeout

    # Model swap callbacks для верификации IMAGE/STAMP блоков
    same_instance = ctx.backends.needs_lmstudio

    def _verif_swap_to_stamp():
        if same_instance:
            logger.info("Верификация: model swap → stamp")
            try:
                ctx.backends.text.unload_model()
            except Exception:
                pass
        try:
            ctx.backends.stamp.preload()
        except Exception:
            pass

    def _verif_swap_to_image():
        if same_instance:
            logger.info("Верификация: model swap → image")
            try:
                ctx.backends.stamp.unload_model()
            except Exception:
                pass
        try:
            ctx.backends.image.preload()
        except Exception:
            pass

    r2_prefix = generate_results(
        ctx.job, ctx.pdf_path, ctx.blocks, ctx.work_dir,
        ctx.backends.text,
        text_fallback_backend=ctx.backends.text_fallback,
        image_backend=ctx.backends.image,
        stamp_backend=ctx.backends.stamp,
        on_verification_progress=on_verification_progress,
        verification_deadline=_verification_deadline,
        before_stamp_phase=_verif_swap_to_stamp,
        before_image_phase=_verif_swap_to_image,
    )

    # Upload
    logger.info("Загрузка результатов в R2...")
    update_job_status(ctx.job_id, "processing", progress=0.97, status_message="☁️ Загрузка в облако...")
    upload_results_to_r2(ctx.job, ctx.work_dir, r2_prefix)

    return r2_prefix


# ── Stage 5: Register ─────────────────────────────────────────────────

def register_results(ctx: JobContext) -> None:
    """Регистрация OCR результатов в node_files и обновление pdf_status."""
    if not ctx.job.node_id:
        return

    update_job_status(ctx.job_id, "processing", progress=0.98, status_message="📝 Регистрация файлов...")
    registered_count = register_ocr_results_to_node(ctx.job.node_id, ctx.job.document_name, ctx.work_dir, blocks_metadata=getattr(ctx, "blocks_metadata", None))
    logger.info(f"✅ Зарегистрировано {registered_count} файлов в node_files для node {ctx.job.node_id}")

    try:
        from .node_storage import update_node_pdf_status

        update_node_pdf_status(ctx.job.node_id)
        logger.info(f"PDF status updated for node {ctx.job.node_id}")
    except Exception as e:
        logger.warning(f"Failed to update PDF status: {e}")


# ── Stage 6: Finalize ─────────────────────────────────────────────────

def finalize(ctx: JobContext) -> dict:
    """Подсчёт статистики, обновление финального статуса.

    Returns:
        dict с результатом задачи.
    """
    total_blocks = ctx.total_blocks

    # Читаем актуальный result.json (может быть обновлён верификацией)
    result_path = ctx.work_dir / "result.json"
    if result_path.exists():
        with open(result_path, "r", encoding="utf-8") as f:
            result_data = json.load(f)
        all_ocr_texts = [
            b.get("ocr_text")
            for page in result_data.get("pages", [])
            for b in page.get("blocks", [])
        ]
        recognized = sum(1 for t in all_ocr_texts if is_success(t))
        error_count = sum(1 for t in all_ocr_texts if is_error(t))
        non_retriable_count = sum(1 for t in all_ocr_texts if is_non_retriable(t))
        suspicious_count = sum(1 for t in all_ocr_texts if t and is_suspicious_output(t)[0])
    else:
        # Fallback на ctx.blocks (если result.json не создан)
        recognized = sum(1 for b in ctx.blocks if is_success(b.ocr_text))
        error_count = sum(1 for b in ctx.blocks if is_error(b.ocr_text))
        non_retriable_count = sum(1 for b in ctx.blocks if is_non_retriable(b.ocr_text))
        suspicious_count = sum(1 for b in ctx.blocks if b.ocr_text and is_suspicious_output(b.ocr_text)[0])

    # Определяем финальный статус на основе coverage
    coverage = recognized / total_blocks if total_blocks > 0 else 0
    if recognized == 0:
        final_status = "error"
        status_msg = f"❌ Ошибка: 0/{total_blocks} блоков распознано"
    elif coverage < 0.9:
        final_status = "partial"
        status_msg = f"⚠️ Частично: {recognized}/{total_blocks} блоков распознано ({coverage:.0%})"
    else:
        final_status = "done"
        status_msg = f"✅ Завершено: {recognized}/{total_blocks} блоков"

    if final_status == "error":
        update_job_status(
            ctx.job_id, "error", progress=1.0,
            status_message=status_msg,
            error_message=f"0/{total_blocks} блоков распознано",
        )
    else:
        update_job_status(ctx.job_id, final_status, progress=1.0, status_message=status_msg)

    logger.info(
        f"Задача {ctx.job_id} завершена: {recognized}/{total_blocks} блоков распознано → {final_status}",
        extra={
            "event": "task_completed",
            "job_id": ctx.job_id,
            "engine": ctx.engine,
            "recognized_count": recognized,
            "error_count": error_count,
            "non_retriable_count": non_retriable_count,
            "suspicious_count": suspicious_count,
            "total_blocks": total_blocks,
            "coverage": round(coverage, 3),
            "final_status": final_status,
            "duration_ms": int((time.time() - ctx.start_time) * 1000),
        },
    )

    return {"status": final_status, "job_id": ctx.job_id}


# ── Error handler ─────────────────────────────────────────────────────

def handle_error(job_id: str, exc: Exception, ctx: Optional[JobContext], start_time: float, engine: str) -> dict:
    """Обработка ошибки: логирование + обновление статуса."""
    error_msg = f"{exc}\n{traceback.format_exc()}"
    logger.error(
        f"Ошибка обработки задачи {job_id}: {error_msg}",
        extra={
            "event": "task_error",
            "job_id": job_id,
            "engine": engine,
            "exception_type": type(exc).__name__,
            "duration_ms": int((time.time() - start_time) * 1000),
        },
    )
    update_job_status(job_id, "error", error_message=str(exc), status_message="❌ Ошибка обработки")
    return {"status": "error", "message": str(exc)}


# ── Cleanup ───────────────────────────────────────────────────────────

def cleanup(job_id: str, ctx: Optional[JobContext], engine: str, lmstudio_acquired: bool,
            celery_task_id: str = "") -> None:
    """Освобождение ресурсов: execution lock, debounced updater, temp dir, LM Studio, GC."""
    start_mem = ctx.start_mem if ctx else 0.0

    # Execution lock
    release_execution_lock(job_id, celery_task_id)

    # Debounced updater metrics
    stats = cleanup_updater(job_id)
    if stats:
        logger.info(
            f"[METRICS] Job {job_id} status updates: "
            f"{stats['db_calls']} DB calls, {stats['skipped']} skipped "
            f"({stats['reduction_percent']}% reduction)"
        )

    # Временная директория
    work_dir = ctx.work_dir if ctx else None
    if work_dir and work_dir.exists():
        try:
            shutil.rmtree(work_dir)
            logger.info(f"✅ Временная директория очищена: {work_dir}")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка очистки временной директории: {e}")

    # LM Studio — delayed unload (grace period вместо немедленной выгрузки)
    from .lmstudio_lifecycle import schedule_pending_unload

    if engine == "chandra" and lmstudio_acquired:
        remaining = release_chandra(job_id)
        if remaining == 0:
            schedule_pending_unload("chandra")
            logger.info("Chandra: последняя задача завершена, выгрузка отложена (grace period)")
        else:
            logger.info(f"Chandra: модели НЕ выгружены, активных задач: {remaining}")

    # Кэш и GC
    clear_page_size_cache()
    force_gc("финальная")
    log_memory_delta(f"[END] Задача {job_id}", start_mem)
