"""PASS 2: Image-фаза — обработка IMAGE/STAMP блоков через async OCR."""
from __future__ import annotations

import asyncio
import gc
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image

from ..logging_config import get_logger
from ..manifest_models import CropManifestEntry
from ..ocr_constants import make_error
from .pass2_shared import (
    CANCELLED_SENTINEL,
    Pass2Context,
    cancellable_recognize,
    drain_queue,
    should_retry_ocr,
)

logger = get_logger(__name__)


async def run_images_phase(
    image_blocks: List[CropManifestEntry],
    blocks: List,
    image_backend,
    stamp_backend,
    ctx: Pass2Context,
) -> Tuple[Dict[str, Dict[int, str]], Dict[str, int]]:
    """Обработать все image блоки и вернуть результаты.

    Returns:
        (image_block_parts, image_block_total_parts)
    """
    from ..worker_prompts import fill_image_prompt_variables

    image_block_parts: Dict[str, Dict[int, str]] = {}
    image_block_total_parts: Dict[str, int] = {}

    # Retry config
    _is_lmstudio = type(image_backend).__name__ in ("ChandraBackend", "QwenBackend")
    _max_retries = 2 if _is_lmstudio else 1
    _retry_delays = [30, 60] if _is_lmstudio else [10]

    async def _process_image(
        entry: CropManifestEntry,
    ) -> Optional[Tuple[str, str, int, int]]:
        if ctx.is_paused():
            return None

        if ctx.is_deadline_exceeded():
            logger.warning(
                f"PASS2 ASYNC: time budget exhausted, пропускаем image {entry.block_id}",
                extra={"event": "pass2_budget_exhausted", "block_id": entry.block_id},
            )
            return None

        if ctx.checkpoint.is_image_processed(entry.block_id):
            logger.debug(f"Image {entry.block_id} уже обработан (checkpoint), пропускаем")
            return None

        block = ctx.blocks_by_id.get(entry.block_id)
        if not block:
            return None

        category_code = getattr(block, "category_code", None)
        backend = stamp_backend if category_code == "stamp" else image_backend

        use_pdf = (
            entry.pdf_crop_path
            and entry.total_parts == 1
            and os.path.exists(entry.pdf_crop_path)
            and hasattr(backend, "supports_pdf_input")
            and backend.supports_pdf_input()
        )

        if not use_pdf and not os.path.exists(entry.crop_path):
            logger.warning(f"Image crop не найден: {entry.crop_path}")
            return None

        if ctx.is_paused():
            return None

        async with ctx.concurrency_semaphore:
            try:
                category_id = getattr(block, "category_id", None)

                prompt_data = fill_image_prompt_variables(
                    prompt_data=block.prompt,
                    doc_name=Path(ctx.pdf_path).name,
                    page_index=block.page_index,
                    block_id=block.id,
                    category_id=category_id,
                    category_code=category_code,
                    engine=None,
                )

                logger.info(
                    f"PASS2 ASYNC: начало обработки IMAGE блока {entry.block_id}",
                    extra={
                        "event": "image_ocr_start",
                        "block_id": entry.block_id,
                        "page_index": entry.page_index,
                        "backend_type": type(backend).__name__,
                        "category_code": category_code,
                        "use_pdf_crop": bool(use_pdf),
                    },
                )

                text = None
                for attempt in range(_max_retries + 1):
                    if attempt > 0:
                        if ctx.is_paused():
                            return None
                        delay = _retry_delays[min(attempt - 1, len(_retry_delays) - 1)]
                        logger.warning(
                            f"PASS2 ASYNC: image {entry.block_id} retry "
                            f"{attempt}/{_max_retries}, ожидание {delay}с"
                        )
                        await asyncio.sleep(delay)

                    if not await ctx.rate_limiter.acquire_async():
                        logger.warning(f"Image {entry.block_id}: rate limiter timeout")
                        if attempt < _max_retries:
                            continue
                        return entry.block_id, make_error("rate limiter timeout"), entry.part_idx, entry.total_parts

                    try:
                        if use_pdf:
                            logger.info(f"PASS2 ASYNC: используется PDF-кроп для {entry.block_id}")
                            text = await cancellable_recognize(
                                ctx, backend, None, prompt_data, None, entry.pdf_crop_path,
                            )
                        else:
                            crop = await asyncio.to_thread(Image.open, entry.crop_path)
                            try:
                                text = await cancellable_recognize(
                                    ctx, backend, crop, prompt_data
                                )
                            finally:
                                crop.close()
                        if text is CANCELLED_SENTINEL:
                            return None
                    except Exception as ocr_err:
                        text = make_error(str(ocr_err))
                    finally:
                        await ctx.rate_limiter.release_async()

                    if not should_retry_ocr(text, f"image {entry.block_id}", attempt, _max_retries):
                        break

                logger.info(
                    f"PASS2 ASYNC: завершена обработка IMAGE блока {entry.block_id}",
                    extra={
                        "event": "image_ocr_completed",
                        "block_id": entry.block_id,
                        "page_index": entry.page_index,
                        "response_length": len(text) if text else 0,
                        "backend_type": type(backend).__name__,
                        "category_code": category_code,
                        "use_pdf_crop": bool(use_pdf),
                    },
                )

                return entry.block_id, text, entry.part_idx, entry.total_parts

            except Exception as e:
                logger.error(
                    f"PASS2 ASYNC: image processing error {entry.block_id}",
                    extra={
                        "event": "pass2_image_error",
                        "block_id": entry.block_id,
                        "page_index": entry.page_index,
                        "block_type": entry.block_type,
                        "backend": type(backend).__name__,
                        "use_pdf_crop": use_pdf,
                    },
                    exc_info=True,
                )
                return entry.block_id, make_error(str(e)), entry.part_idx, entry.total_parts

    # === Worker loop ===
    logger.info(
        f"PASS2 ASYNC: обработка {len(image_blocks)} image blocks "
        f"({ctx.max_workers} workers, bounded queue)"
    )

    image_queue: asyncio.Queue = asyncio.Queue()
    for entry in image_blocks:
        image_queue.put_nowait(entry)

    async def _worker():
        while not image_queue.empty():
            if ctx.is_paused():
                drain_queue(image_queue)
                return
            try:
                entry = image_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            try:
                result = await _process_image(entry)
            except Exception as exc:
                logger.error(f"PASS2 ASYNC: image exception: {exc}", exc_info=True)
                await ctx.update_progress("Image (error)")
                image_queue.task_done()
                continue

            if result:
                block_id, text, part_idx, total_parts = result

                if block_id not in image_block_parts:
                    image_block_parts[block_id] = {}
                    image_block_total_parts[block_id] = total_parts

                image_block_parts[block_id][part_idx] = text

                ctx.checkpoint.mark_image_processed(block_id, text, part_idx, total_parts)
                await ctx.save_checkpoint()

                block = ctx.blocks_by_id.get(block_id)
                if block:
                    page_num = block.page_index + 1
                    category = getattr(block, "category_code", None) or "image"
                    block_info = f"Image: {category} (стр. {page_num})"
                else:
                    block_info = "Image"
                await ctx.update_progress(block_info)
            else:
                await ctx.update_progress("Image")

            gc.collect()
            image_queue.task_done()

    workers = [asyncio.create_task(_worker()) for _ in range(ctx.max_workers)]
    await asyncio.gather(*workers)

    # Собираем части IMAGE блоков
    for block_id, parts_dict in image_block_parts.items():
        if block_id not in ctx.blocks_by_id:
            continue
        block = ctx.blocks_by_id[block_id]
        total_parts = image_block_total_parts.get(block_id, 1)

        if total_parts == 1:
            block.ocr_text = parts_dict.get(0, "")
        else:
            combined = [parts_dict.get(i, "") for i in range(total_parts)]
            block.ocr_text = "\n\n".join(combined)
        logger.info(
            f"PASS2 ASYNC IMAGE блок {block_id}: ocr_text длина = "
            f"{len(block.ocr_text) if block.ocr_text else 0}"
        )

    return image_block_parts, image_block_total_parts
