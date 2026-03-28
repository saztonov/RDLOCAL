"""Двухпроходный OCR алгоритм (экономия памяти)"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .checkpoint_models import OCRCheckpoint, get_checkpoint_path
from .debounced_updater import get_debounced_updater
from .logging_config import get_logger
from .memory_utils import log_memory_delta
from .pdf_twopass import (
    cleanup_manifest_files,
    pass1_prepare_crops,
)
from .storage import Job, is_job_paused
from .task_helpers import check_paused
from .task_upload import copy_crops_to_final

logger = get_logger(__name__)


# Флаг для включения checkpoint (можно переключать)
USE_CHECKPOINT = True


def run_two_pass_ocr(
    job: Job,
    pdf_path: Path,
    blocks: list,
    crops_dir: Path,
    work_dir: Path,
    text_backend,
    image_backend,
    stamp_backend,
    start_mem: float,
    engine: str = "lmstudio",
    soft_timeout_at: float = None,
):
    """Двухпроходный алгоритм OCR (экономия памяти)"""
    from .settings import settings

    logger.info(
        f"Используется двухпроходный алгоритм (per-block OCR)"
    )
    manifest = None
    updater = get_debounced_updater(job.id)
    checkpoint = None

    # Загрузка checkpoint для resume
    if USE_CHECKPOINT:
        checkpoint_path = get_checkpoint_path(work_dir)
        checkpoint = OCRCheckpoint.load(checkpoint_path)
        if checkpoint:
            logger.info(
                f"Checkpoint загружен: phase={checkpoint.phase}, "
                f"blocks={len(checkpoint.processed_blocks)}"
            )
        else:
            logger.info("Checkpoint не найден, начинаем с начала")

    try:
        # PASS 1: Подготовка кропов на диск
        def on_pass1_progress(current, total):
            progress = 0.1 + 0.3 * (current / total)
            status_msg = f"📦 PASS 1: Подготовка кропов (стр. {current}/{total})"
            if not is_job_paused(job.id):
                updater.update("processing", progress=progress, status_message=status_msg)

        manifest = pass1_prepare_crops(
            str(pdf_path),
            blocks,
            str(crops_dir),
            save_image_crops_as_pdf=True,
            on_progress=on_pass1_progress,
            should_stop=lambda: check_paused(job.id),
        )

        total_blocks = len(manifest.blocks) if manifest else 0

        if total_blocks == 0:
            raise RuntimeError(
                "PASS1: пустой manifest — нет блоков для обработки"
            )

        logger.info(
            f"PASS1 завершён: {total_blocks} block crops",
            extra={
                "event": "pass1_completed",
                "job_id": job.id,
                "block_count": total_blocks,
                "total_blocks": len(blocks),
                "engine": engine,
            },
        )

        log_memory_delta("После PASS1", start_mem)

        if check_paused(job.id):
            return

        def on_pass2_progress(current, total, block_info: str = None):
            progress = 0.4 + 0.5 * (current / total)
            if block_info:
                status_msg = f"🔍 PASS 2: {block_info} ({current}/{total})"
            else:
                status_msg = f"🔍 PASS 2: Распознавание ({current}/{total})"
            try:
                if not is_job_paused(job.id):
                    updater.update("processing", progress=progress, status_message=status_msg)
            except Exception as exc:
                logger.warning(f"on_pass2_progress: ошибка обновления прогресса: {exc}")

        # Создаём или обновляем checkpoint
        if USE_CHECKPOINT and checkpoint is None:
            checkpoint = OCRCheckpoint.create_new(
                job_id=job.id,
                total_blocks=total_blocks,
                manifest_path=str(crops_dir / "manifest.json") if crops_dir else None,
            )
        elif USE_CHECKPOINT and checkpoint:
            checkpoint.total_blocks = total_blocks

        from .pdf_twopass.pass2_ocr_async import pass2_ocr_from_manifest_async
        from .rate_limiter import reset_async_limiter

        # Сброс asyncio-привязанного rate limiter перед новым event loop
        reset_async_limiter()

        # Callback для смены модели между text и image фазами
        def _swap_to_qwen():
            """Выгрузить chandra, загрузить qwen (если тот же LM Studio инстанс)."""
            qwen_url = settings.qwen_base_url or settings.chandra_base_url
            if qwen_url == settings.chandra_base_url:
                logger.info("Смена модели: chandra → qwen (тот же LM Studio инстанс)")
                try:
                    text_backend.unload_model()
                except Exception as e:
                    logger.warning(f"Ошибка выгрузки chandra: {e}")
                try:
                    image_backend.preload()
                except Exception as e:
                    logger.warning(f"Ошибка загрузки qwen: {e}")
            else:
                logger.info("Preload qwen (отдельный LM Studio инстанс)")
                try:
                    image_backend.preload()
                except Exception as e:
                    logger.warning(f"Ошибка загрузки qwen: {e}")

        logger.info(
            f"PASS2: запуск per-block OCR",
            extra={
                "event": "pass2_start",
                "job_id": job.id,
                "block_count": total_blocks,
                "engine": engine,
            },
        )

        # Запуск async pass2 через asyncio.run
        asyncio.run(
            pass2_ocr_from_manifest_async(
                manifest,
                blocks,
                text_backend,
                image_backend,
                stamp_backend,
                str(pdf_path),
                on_progress=on_pass2_progress,
                check_paused=lambda: is_job_paused(job.id),
                checkpoint=checkpoint if USE_CHECKPOINT else None,
                work_dir=work_dir if USE_CHECKPOINT else None,
                deadline=soft_timeout_at,
                before_image_phase=_swap_to_qwen,
            )
        )

        log_memory_delta("После PASS2", start_mem)

        # Копируем PDF кропы в crops_final
        copy_crops_to_final(work_dir, blocks)

        # Удаляем checkpoint после успешного завершения
        if USE_CHECKPOINT:
            checkpoint_path = get_checkpoint_path(work_dir)
            if checkpoint_path.exists():
                checkpoint_path.unlink()
                logger.info("Checkpoint удалён после успешного завершения")

    finally:
        # Очистка временных файлов кропов
        if manifest:
            cleanup_manifest_files(manifest)
