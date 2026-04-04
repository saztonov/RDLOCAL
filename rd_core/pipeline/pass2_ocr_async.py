"""
PASS 2 ASYNC: Последовательный per-block OCR.

Обрабатывает блоки в документном порядке:
1. TEXT блоки (через text_backend / ChandraBackend)
2. Смена модели (если тот же LM Studio инстанс)
3. STAMP блоки (через stamp_backend / QwenBackend)
4. IMAGE блоки (через image_backend / QwenBackend)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .checkpoint_models import OCRCheckpoint
from .manifest_models import TwoPassManifest
from .memory_utils import force_gc, log_memory, log_memory_delta
from .pass2_images import run_blocks_phase
from .pass2_shared import Pass2Context
from .prompts import CategoryPromptFn

logger = logging.getLogger(__name__)


@dataclass
class PhaseConcurrency:
    """Concurrency settings per OCR phase."""
    text_max_concurrent: int = 1
    stamp_max_concurrent: int = 1
    image_max_concurrent: int = 1


async def pass2_ocr_from_manifest_async(
    manifest: TwoPassManifest,
    blocks: List,
    text_backend,
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
    before_stamp_phase: Optional[Callable[[], None]] = None,
    phase_concurrency: Optional[PhaseConcurrency] = None,
    rate_limiter: object = None,
    category_prompt_fn: CategoryPromptFn = None,
) -> None:
    """
    PASS 2: Per-block OCR в три фазы.

    Блоки обрабатываются в порядке:
    1. TEXT (Chandra) -> model swap -> 2. STAMP (Qwen 9b) -> model swap -> 3. IMAGE (Qwen 27b)
    """
    start_mem = log_memory("PASS2 start")

    total_requests = len(manifest.blocks)

    blocks_by_id = {b.id: b for b in blocks}

    if checkpoint is None:
        checkpoint = OCRCheckpoint.create_new(
            job_id="unknown",
            total_blocks=total_requests,
        )
    else:
        restored = checkpoint.apply_to_blocks(blocks)
        if restored > 0:
            logger.info(
                f"PASS2: восстановлено {restored} блоков из checkpoint",
                extra={
                    "event": "checkpoint_restored",
                    "checkpoint_count": restored,
                    "phase": checkpoint.phase,
                },
            )

    ctx = Pass2Context(
        blocks_by_id=blocks_by_id,
        checkpoint=checkpoint,
        on_progress=on_progress,
        check_paused=check_paused,
        deadline=deadline,
        work_dir=work_dir,
        max_workers=1,
        total_requests=total_requests,
        pdf_path=pdf_path,
        processed=len(checkpoint.processed_blocks),
        rate_limiter=rate_limiter,
    )

    # Разделяем блоки по типу для трёхфазной обработки
    text_entries = [e for e in manifest.blocks if e.block_type == "text"]
    stamp_entries = [e for e in manifest.blocks if e.block_type == "stamp"]
    image_entries = [e for e in manifest.blocks if e.block_type not in ("text", "stamp")]

    # Phase concurrency
    pc = phase_concurrency or PhaseConcurrency()

    def _cap(phase_max: int) -> int:
        if max_concurrent is not None:
            return min(phase_max, max_concurrent)
        return phase_max

    text_workers = _cap(pc.text_max_concurrent)
    stamp_workers = _cap(pc.stamp_max_concurrent)
    image_workers = _cap(pc.image_max_concurrent)

    checkpoint.phase = "pass2"

    # ── Phase 1: TEXT блоки (Chandra) ────────────────────────────
    if text_entries:
        logger.info(
            f"PASS2: phase=text, max_workers={text_workers}, blocks={len(text_entries)}"
        )
        await run_blocks_phase(
            text_entries, blocks, text_backend, image_backend, stamp_backend, ctx,
            max_workers=text_workers,
            category_prompt_fn=category_prompt_fn,
        )

    log_memory_delta("PASS2 после TEXT", start_mem)

    # ── Model swap: chandra -> stamp model ────────────────────────
    if before_stamp_phase and stamp_entries:
        logger.info("PASS2: model swap chandra -> stamp")
        await asyncio.to_thread(before_stamp_phase)

    # ── Phase 2: STAMP блоки (Qwen 9b) ──────────────────────────
    if stamp_entries:
        logger.info(
            f"PASS2: phase=stamp, max_workers={stamp_workers}, blocks={len(stamp_entries)}"
        )
        await run_blocks_phase(
            stamp_entries, blocks, text_backend, image_backend, stamp_backend, ctx,
            max_workers=stamp_workers,
            category_prompt_fn=category_prompt_fn,
        )

    log_memory_delta("PASS2 после STAMP", start_mem)

    # ── Model swap: stamp -> image model ──────────────────────────
    if before_image_phase and image_entries:
        logger.info("PASS2: model swap stamp -> image")
        await asyncio.to_thread(before_image_phase)

    # ── Phase 3: IMAGE блоки (Qwen 27b) ─────────────────────────
    if image_entries:
        logger.info(
            f"PASS2: phase=image, max_workers={image_workers}, blocks={len(image_entries)}"
        )
        await run_blocks_phase(
            image_entries, blocks, text_backend, image_backend, stamp_backend, ctx,
            max_workers=image_workers,
            category_prompt_fn=category_prompt_fn,
        )

    # Финальное сохранение checkpoint
    checkpoint.phase = "completed"
    if ctx.checkpoint_path:
        await asyncio.to_thread(checkpoint.save, ctx.checkpoint_path)
        logger.info(f"Финальный checkpoint сохранён: {ctx.checkpoint_path}")

    force_gc("PASS2 завершён")
    log_memory_delta("PASS2 end", start_mem)

    logger.info(f"PASS2 завершён: {ctx.processed} запросов обработано")


def pass2_ocr_from_manifest_sync_wrapper(
    manifest: TwoPassManifest,
    blocks: List,
    text_backend,
    image_backend,
    stamp_backend,
    pdf_path: str,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    check_paused: Optional[Callable[[], bool]] = None,
    checkpoint: Optional[OCRCheckpoint] = None,
    work_dir: Optional[Path] = None,
    before_image_phase: Optional[Callable[[], None]] = None,
    before_stamp_phase: Optional[Callable[[], None]] = None,
    phase_concurrency: Optional[PhaseConcurrency] = None,
    rate_limiter: object = None,
    category_prompt_fn: CategoryPromptFn = None,
) -> None:
    """Синхронная обёртка для async pass2_ocr."""
    asyncio.run(
        pass2_ocr_from_manifest_async(
            manifest=manifest,
            blocks=blocks,
            text_backend=text_backend,
            image_backend=image_backend,
            stamp_backend=stamp_backend,
            pdf_path=pdf_path,
            on_progress=on_progress,
            check_paused=check_paused,
            checkpoint=checkpoint,
            work_dir=work_dir,
            before_image_phase=before_image_phase,
            before_stamp_phase=before_stamp_phase,
            phase_concurrency=phase_concurrency,
            rate_limiter=rate_limiter,
            category_prompt_fn=category_prompt_fn,
        )
    )
