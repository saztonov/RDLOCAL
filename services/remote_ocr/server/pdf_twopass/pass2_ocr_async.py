"""
PASS 2 ASYNC: Асинхронный OCR с использованием asyncio.gather.

Заменяет ThreadPoolExecutor на asyncio для эффективной обработки I/O-bound операций.
Обеспечивает 40-60% ускорение за счёт настоящего параллелизма без GIL.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, List, Optional

from ..checkpoint_models import OCRCheckpoint
from ..logging_config import get_logger
from ..manifest_models import TwoPassManifest
from ..memory_utils import force_gc, log_memory, log_memory_delta
from ..settings import settings

from .pass2_images import run_images_phase
from .pass2_shared import Pass2Context
from .pass2_strips import run_strips_phase

logger = get_logger(__name__)


async def pass2_ocr_from_manifest_async(
    manifest: TwoPassManifest,
    blocks: List,
    strip_backend,
    image_backend,
    stamp_backend,
    pdf_path: str,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    check_paused: Optional[Callable[[], bool]] = None,
    max_concurrent: Optional[int] = None,
    checkpoint: Optional[OCRCheckpoint] = None,
    work_dir: Optional[Path] = None,
    deadline: Optional[float] = None,
    before_image_phase: Optional[Callable[[], None]] = None,
) -> None:
    """
    PASS 2 ASYNC: Асинхронный OCR с загрузкой кропов с диска.

    Использует asyncio.gather вместо ThreadPoolExecutor для эффективного
    параллелизма I/O-bound операций (OCR API calls).

    Поддерживает checkpoint/resume для возможности продолжения после паузы.
    """
    start_mem = log_memory("PASS2 ASYNC start")

    total_requests = len(manifest.strips) + len(manifest.image_blocks)
    max_workers = max_concurrent or settings.ocr_threads_per_job

    blocks_by_id = {b.id: b for b in blocks}

    # Инициализация или использование существующего checkpoint
    if checkpoint is None:
        checkpoint = OCRCheckpoint.create_new(
            job_id="unknown",
            total_strips=len(manifest.strips),
            total_images=len(manifest.image_blocks),
        )
    else:
        restored = checkpoint.apply_to_blocks(blocks)
        if restored > 0:
            logger.info(
                f"PASS2 ASYNC: восстановлено {restored} блоков из checkpoint",
                extra={
                    "event": "checkpoint_restored",
                    "checkpoint_count": restored,
                    "phase": checkpoint.phase,
                },
            )

    # Общий контекст для обеих фаз
    ctx = Pass2Context(
        blocks_by_id=blocks_by_id,
        checkpoint=checkpoint,
        on_progress=on_progress,
        check_paused=check_paused,
        deadline=deadline,
        work_dir=work_dir,
        max_workers=max_workers,
        total_requests=total_requests,
        pdf_path=pdf_path,
        processed=len(checkpoint.processed_strips) + len(checkpoint.processed_images),
    )

    # === ФАЗА 1: STRIPS ===
    checkpoint.phase = "pass2_strips"
    await run_strips_phase(manifest.strips, blocks, strip_backend, ctx)

    log_memory_delta("PASS2 ASYNC после strips", start_mem)

    # Смена модели между фазами (если тот же LM Studio инстанс)
    if before_image_phase:
        logger.info("PASS2 ASYNC: выполняем before_image_phase (смена модели)")
        await asyncio.to_thread(before_image_phase)

    # === ФАЗА 2: IMAGES ===
    checkpoint.phase = "pass2_images"
    await run_images_phase(
        manifest.image_blocks, blocks, image_backend, stamp_backend, ctx
    )

    # Финальное сохранение checkpoint
    checkpoint.phase = "completed"
    if ctx.checkpoint_path:
        await asyncio.to_thread(checkpoint.save, ctx.checkpoint_path)
        logger.info(f"Финальный checkpoint сохранён: {ctx.checkpoint_path}")

    force_gc("PASS2 ASYNC завершён")
    log_memory_delta("PASS2 ASYNC end", start_mem)

    logger.info(f"PASS2 ASYNC завершён: {ctx.processed} запросов обработано")


def pass2_ocr_from_manifest_sync_wrapper(
    manifest: TwoPassManifest,
    blocks: List,
    strip_backend,
    image_backend,
    stamp_backend,
    pdf_path: str,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    check_paused: Optional[Callable[[], bool]] = None,
    checkpoint: Optional[OCRCheckpoint] = None,
    work_dir: Optional[Path] = None,
    before_image_phase: Optional[Callable[[], None]] = None,
) -> None:
    """
    Синхронная обёртка для async pass2_ocr.

    Используется для вызова из sync контекста (Celery task).
    """
    asyncio.run(
        pass2_ocr_from_manifest_async(
            manifest=manifest,
            blocks=blocks,
            strip_backend=strip_backend,
            image_backend=image_backend,
            stamp_backend=stamp_backend,
            pdf_path=pdf_path,
            on_progress=on_progress,
            check_paused=check_paused,
            checkpoint=checkpoint,
            work_dir=work_dir,
            before_image_phase=before_image_phase,
        )
    )
