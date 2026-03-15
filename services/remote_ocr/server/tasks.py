"""Celery задачи для OCR обработки"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .celery_app import celery_app
from .debounced_updater import cleanup_updater, get_debounced_updater
from .logging_config import get_logger
from .memory_utils import force_gc, log_memory, log_memory_delta
from .ocr_constants import ERROR_PREFIX, NON_RETRIABLE_PREFIX
from .settings import settings
from .lmstudio_lifecycle import acquire_chandra, acquire_lmstudio, release_chandra, release_lmstudio
from .storage import get_job, register_ocr_results_to_node, update_job_status
from .storage_jobs import increment_retry_count, set_job_started_at
from .task_helpers import check_paused, create_empty_result, download_job_files
from .task_ocr_twopass import run_two_pass_ocr
from .task_results import generate_results
from .task_upload import upload_results_to_r2
from .worker_pdf import clear_page_size_cache

logger = get_logger(__name__)


@celery_app.task(bind=True, name="run_ocr_task", max_retries=3, rate_limit="4/m")
def run_ocr_task(self, job_id: str) -> dict:
    """Celery задача для обработки OCR"""
    start_mem = log_memory(f"[START] Задача {job_id}")
    start_time = time.time()

    work_dir = None
    engine = "openrouter"
    strip_backend = None
    lmstudio_acquired = False
    try:
        # Получаем задачу из БД с настройками
        job = get_job(job_id, with_files=True, with_settings=True)
        if not job:
            logger.error(f"Задача {job_id} не найдена")
            return {"status": "error", "message": "Job not found"}

        # ===== TASK GUARD: защита от дублей после reorder =====
        if job.celery_task_id and job.celery_task_id != self.request.id:
            logger.warning(
                f"Task guard: stale task {self.request.id} for job {job_id}, "
                f"expected {job.celery_task_id}. Aborting."
            )
            return {"status": "aborted", "message": "Stale task (reordered)"}

        if job.status in ("cancelled", "done"):
            logger.info(f"Task guard: job {job_id} status={job.status}, skipping")
            return {"status": "skipped", "message": f"Job already {job.status}"}

        # ===== ЗАЩИТА ОТ ЗАЦИКЛИВАНИЯ =====
        # Проверка 1: количество попыток
        if job.retry_count >= settings.job_max_retries:
            error_msg = f"Превышен лимит попыток: {job.retry_count}/{settings.job_max_retries}"
            logger.error(f"Job {job_id}: {error_msg}")
            update_job_status(
                job_id, "error",
                error_message=error_msg,
                status_message="❌ Превышен лимит попыток"
            )
            return {"status": "error", "message": "Max retries exceeded"}

        # Проверка 2: общее время выполнения
        if job.started_at:
            try:
                started = datetime.fromisoformat(job.started_at.replace('Z', '+00:00'))
                runtime_hours = (datetime.now(timezone.utc) - started).total_seconds() / 3600

                if runtime_hours > settings.job_max_runtime_hours:
                    error_msg = f"Превышено время выполнения: {runtime_hours:.1f}h (лимит: {settings.job_max_runtime_hours}h)"
                    logger.error(f"Job {job_id}: {error_msg}")
                    update_job_status(
                        job_id, "error",
                        error_message=error_msg,
                        status_message="❌ Превышено время выполнения"
                    )
                    return {"status": "error", "message": "Max runtime exceeded"}
            except Exception as e:
                logger.warning(f"Job {job_id}: ошибка парсинга started_at ({job.started_at}): {e}")

        # Инкрементируем retry_count
        new_retry_count = increment_retry_count(job_id)
        logger.info(f"Job {job_id}: попытка {new_retry_count}/{settings.job_max_retries}")

        # Устанавливаем started_at только при первом запуске
        if not job.started_at:
            set_job_started_at(job_id)
        # ===== КОНЕЦ ЗАЩИТЫ ОТ ЗАЦИКЛИВАНИЯ =====

        if check_paused(job.id):
            return {"status": "paused"}

        # Обновляем статус на processing
        update_job_status(job.id, "processing", progress=0.05, status_message="📥 Инициализация задачи...")

        # Создаём временную директорию
        work_dir = Path(tempfile.mkdtemp(prefix=f"ocr_job_{job.id}_"))
        crops_dir = work_dir / "crops"
        crops_dir.mkdir(exist_ok=True)

        logger.info(f"Задача {job.id}: скачивание файлов из R2...")
        update_job_status(job.id, "processing", progress=0.06, status_message="📥 Скачивание файлов из R2...")
        pdf_path, blocks_path = download_job_files(job, work_dir)
        log_memory_delta("После скачивания файлов", start_mem)

        with open(blocks_path, "r", encoding="utf-8") as f:
            blocks_data = json.load(f)

        # annotation.json имеет структуру {pdf_path, pages: [{blocks: [...]}]}
        # Извлекаем блоки из всех страниц
        if isinstance(blocks_data, dict) and "pages" in blocks_data:
            all_blocks = []
            for page in blocks_data.get("pages", []):
                all_blocks.extend(page.get("blocks", []))
            blocks_data = all_blocks

        if not blocks_data:
            update_job_status(job.id, "done", progress=1.0, status_message="✅ Нет блоков для распознавания")
            create_empty_result(job, work_dir, pdf_path)
            upload_results_to_r2(job, work_dir)
            return {"status": "done", "job_id": job_id}

        from rd_core.models import Block

        blocks = [Block.from_dict(b, migrate_ids=False)[0] for b in blocks_data]
        total_blocks = len(blocks)

        logger.info(f"Задача {job.id}: {total_blocks} блоков")

        if check_paused(job.id):
            return {"status": "paused"}

        update_job_status(job.id, "processing", progress=0.1, status_message=f"⚙️ Подготовка: {total_blocks} блоков")

        # Создание бэкендов через фабрику
        from .backend_factory import create_job_backends

        backends = create_job_backends(job)
        engine = backends.engine
        strip_backend = backends.strip
        image_backend = backends.image
        stamp_backend = backends.stamp

        logger.info(
            f"Бэкенды готовы: engine={engine}, блоков={total_blocks}",
            extra={
                "event": "task_backends_ready",
                "job_id": job.id,
                "engine": engine,
                "backend_type": type(strip_backend).__name__,
                "total_blocks": total_blocks,
            },
        )

        if backends.needs_lmstudio:
            if engine == "chandra":
                acquire_chandra(job_id)
            elif engine == "qwen":
                acquire_lmstudio("qwen", job_id)
            lmstudio_acquired = True

        # OCR обработка (двухпроходный алгоритм)
        run_two_pass_ocr(
            job,
            pdf_path,
            blocks,
            crops_dir,
            work_dir,
            strip_backend,
            image_backend,
            stamp_backend,
            start_mem,
            engine=engine,
        )

        force_gc("после OCR обработки")

        # Генерация результатов (передаём OCR backend для верификации)
        update_job_status(job.id, "processing", progress=0.92, status_message="📄 Генерация результатов...")
        verification_backend = strip_backend

        # Callback для верификации блоков (диапазон 0.92 -> 0.97)
        def on_verification_progress(current: int, total: int):
            if total > 0:
                progress = 0.92 + 0.05 * (current / total)
                status_msg = f"🔍 Верификация блоков ({current + 1}/{total})"
            else:
                progress = 0.92
                status_msg = "🔍 Проверка распознанных блоков..."

            # Форсируем обновление для важных этапов (начало, каждый 5-й блок, конец)
            updater = get_debounced_updater(job.id)
            if total == 0 or current == 0 or current == total - 1 or current % 5 == 0:
                updater.force_update("processing", progress=progress, status_message=status_msg)
            else:
                update_job_status(job.id, "processing", progress=progress, status_message=status_msg)

        r2_prefix = generate_results(
            job, pdf_path, blocks, work_dir, verification_backend, on_verification_progress
        )

        # Загрузка результатов в R2
        logger.info(f"Загрузка результатов в R2...")
        update_job_status(job.id, "processing", progress=0.97, status_message="☁️ Загрузка в облако...")
        upload_results_to_r2(job, work_dir, r2_prefix)

        # Регистрация OCR результатов в node_files
        if job.node_id:
            update_job_status(job.id, "processing", progress=0.98, status_message="📝 Регистрация файлов...")
            registered_count = register_ocr_results_to_node(job.node_id, job.document_name, work_dir)
            logger.info(f"✅ Зарегистрировано {registered_count} файлов в node_files для node {job.node_id}")

            # Обновляем статус PDF документа
            try:
                from .node_storage import update_node_pdf_status

                update_node_pdf_status(job.node_id)
                logger.info(f"PDF status updated for node {job.node_id}")
            except Exception as e:
                logger.warning(f"Failed to update PDF status: {e}")

        # Подсчёт распознанных блоков для информативного статуса
        recognized = sum(
            1 for b in blocks
            if b.ocr_text
            and not b.ocr_text.startswith(ERROR_PREFIX)
            and not b.ocr_text.startswith(NON_RETRIABLE_PREFIX)
        )
        error_count = sum(
            1 for b in blocks
            if b.ocr_text and b.ocr_text.startswith(ERROR_PREFIX)
        )
        non_retriable_count = sum(
            1 for b in blocks
            if b.ocr_text and b.ocr_text.startswith(NON_RETRIABLE_PREFIX)
        )
        if recognized == total_blocks:
            status_msg = f"✅ Завершено: {recognized}/{total_blocks} блоков"
        elif recognized > 0:
            status_msg = f"⚠️ Частично: {recognized}/{total_blocks} блоков распознано"
        else:
            status_msg = f"❌ Ошибка: 0/{total_blocks} блоков распознано"

        update_job_status(job.id, "done", progress=1.0, status_message=status_msg)
        logger.info(
            f"Задача {job.id} завершена: {recognized}/{total_blocks} блоков распознано",
            extra={
                "event": "task_completed",
                "job_id": job.id,
                "engine": engine,
                "recognized_count": recognized,
                "error_count": error_count,
                "non_retriable_count": non_retriable_count,
                "total_blocks": total_blocks,
                "duration_ms": int((time.time() - start_time) * 1000),
            },
        )

        return {"status": "done", "job_id": job_id}

    except Exception as e:
        error_msg = f"{e}\n{traceback.format_exc()}"
        logger.error(
            f"Ошибка обработки задачи {job_id}: {error_msg}",
            extra={
                "event": "task_error",
                "job_id": job_id,
                "engine": engine,
                "exception_type": type(e).__name__,
                "duration_ms": int((time.time() - start_time) * 1000),
            },
        )
        update_job_status(job_id, "error", error_message=str(e), status_message="❌ Ошибка обработки")
        return {"status": "error", "message": str(e)}

    finally:
        # Логируем метрики debounced updater
        stats = cleanup_updater(job_id)
        if stats:
            logger.info(
                f"[METRICS] Job {job_id} status updates: "
                f"{stats['db_calls']} DB calls, {stats['skipped']} skipped "
                f"({stats['reduction_percent']}% reduction)"
            )

        # Очистка временной директории
        if work_dir and work_dir.exists():
            try:
                shutil.rmtree(work_dir)
                logger.info(f"✅ Временная директория очищена: {work_dir}")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка очистки временной директории: {e}")

        # Выгрузить модель LM Studio (только если нет других активных задач)
        if engine == "chandra" and lmstudio_acquired:
            remaining = release_chandra(job_id)
            if remaining == 0 and strip_backend is not None and hasattr(strip_backend, "unload_model"):
                strip_backend.unload_model()
                logger.info(f"Chandra: последняя задача завершена, модель выгружена")
            else:
                logger.info(f"Chandra: модель НЕ выгружена, активных задач: {remaining}")

        if engine == "qwen" and lmstudio_acquired:
            remaining = release_lmstudio("qwen", job_id)
            if remaining == 0 and strip_backend is not None and hasattr(strip_backend, "unload_model"):
                strip_backend.unload_model()
                logger.info(f"Qwen: последняя задача завершена, модель выгружена")
            else:
                logger.info(f"Qwen: модель НЕ выгружена, активных задач: {remaining}")

        # Очищаем кэш размеров страниц
        clear_page_size_cache()

        # Финальная сборка мусора
        force_gc("финальная")
        log_memory_delta(f"[END] Задача {job_id}", start_mem)
