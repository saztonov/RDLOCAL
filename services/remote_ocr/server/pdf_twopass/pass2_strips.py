"""PASS 2: Strip-фаза — обработка TEXT/TABLE strips через async OCR."""
from __future__ import annotations

import asyncio
import gc
import os
from typing import Dict, List, Optional, Tuple

from PIL import Image

from ..logging_config import get_logger
from ..manifest_models import StripManifestEntry
from ..ocr_constants import make_error
from .pass2_shared import (
    CANCELLED_SENTINEL,
    Pass2Context,
    cancellable_recognize,
    drain_queue,
    should_retry_ocr,
)

logger = get_logger(__name__)


async def run_strips_phase(
    strips: List[StripManifestEntry],
    blocks: List,
    strip_backend,
    ctx: Pass2Context,
) -> Tuple[Dict[str, Dict[int, str]], Dict[str, int]]:
    """Обработать все strips и вернуть результаты по блокам.

    Returns:
        (text_block_parts, text_block_total_parts) — словари для сборки ocr_text.
    """
    from ..worker_prompts import build_strip_prompt, parse_batch_response_by_index

    text_block_parts: Dict[str, Dict[int, str]] = {}
    text_block_total_parts: Dict[str, int] = {}

    # Retry config
    _is_lmstudio = type(strip_backend).__name__ in ("ChandraBackend", "QwenBackend")
    _max_retries = 2 if _is_lmstudio else 1
    _retry_delays = [30, 60] if _is_lmstudio else [5]

    async def _process_strip(
        strip: StripManifestEntry, strip_idx: int
    ) -> Optional[Tuple[StripManifestEntry, Dict[int, str], int]]:
        if ctx.is_paused():
            return None

        if ctx.is_deadline_exceeded():
            logger.warning(
                f"PASS2 ASYNC: time budget exhausted, пропускаем strip {strip.strip_id}",
                extra={"event": "pass2_budget_exhausted", "strip_id": strip.strip_id},
            )
            return None

        if ctx.checkpoint.is_strip_processed(strip.strip_id):
            logger.debug(f"Strip {strip.strip_id} уже обработан (checkpoint), пропускаем")
            return None

        if not strip.strip_path or not os.path.exists(strip.strip_path):
            logger.warning(f"Strip {strip.strip_id} не найден: {strip.strip_path}")
            return None

        if ctx.is_paused():
            return None

        async with ctx.concurrency_semaphore:
            try:
                strip_blocks = [
                    ctx.blocks_by_id[bp["block_id"]]
                    for bp in strip.block_parts
                    if bp["block_id"] in ctx.blocks_by_id
                ]
                if not strip_blocks:
                    return None

                prompt_data = build_strip_prompt(strip_blocks)
                block_ids = [bp["block_id"] for bp in strip.block_parts]

                logger.info(
                    f"PASS2 ASYNC: начало обработки strip {strip.strip_id} "
                    f"({len(strip.block_parts)} блоков): {block_ids}",
                    extra={
                        "event": "strip_ocr_start",
                        "strip_id": strip.strip_id,
                        "block_count": len(strip.block_parts),
                        "block_ids": block_ids,
                    },
                )

                response_text = None
                for attempt in range(_max_retries + 1):
                    if attempt > 0:
                        if ctx.is_paused():
                            return None
                        delay = _retry_delays[min(attempt - 1, len(_retry_delays) - 1)]
                        logger.warning(
                            f"PASS2 ASYNC: strip {strip.strip_id} retry "
                            f"{attempt}/{_max_retries}, ожидание {delay}с"
                        )
                        await asyncio.sleep(delay)

                    merged_image = await asyncio.to_thread(Image.open, strip.strip_path)
                    try:
                        if not await ctx.rate_limiter.acquire_async():
                            logger.warning(f"Strip {strip.strip_id}: rate limiter timeout")
                            merged_image.close()
                            if attempt < _max_retries:
                                continue
                            error_results = {i: make_error("rate limiter timeout") for i in range(len(strip.block_parts))}
                            return strip, error_results, strip_idx

                        try:
                            response_text = await cancellable_recognize(
                                ctx, strip_backend, merged_image, prompt_data
                            )
                            if response_text is CANCELLED_SENTINEL:
                                return None
                        finally:
                            await ctx.rate_limiter.release_async()
                    finally:
                        merged_image.close()

                    if not should_retry_ocr(response_text, f"strip {strip.strip_id}", attempt, _max_retries):
                        break

                response_len = len(response_text) if response_text else 0
                if response_len == 0:
                    logger.warning(
                        f"PASS2 ASYNC: strip {strip.strip_id} — пустой ответ от OCR бэкенда",
                        extra={
                            "event": "strip_ocr_empty",
                            "strip_id": strip.strip_id,
                            "block_count": len(strip.block_parts),
                            "backend_type": type(strip_backend).__name__,
                        },
                    )
                else:
                    logger.info(
                        f"PASS2 ASYNC: завершена обработка strip {strip.strip_id}, "
                        f"ответ {response_len} символов",
                        extra={
                            "event": "strip_ocr_completed",
                            "strip_id": strip.strip_id,
                            "response_length": response_len,
                            "block_count": len(strip.block_parts),
                            "strip_attempt": attempt,
                            "backend_type": type(strip_backend).__name__,
                        },
                    )

                index_results = parse_batch_response_by_index(
                    len(strip.block_parts), response_text, block_ids=block_ids
                )

                parsed_lens = {i: len(v) if v else 0 for i, v in index_results.items()}
                logger.info(
                    f"PASS2 ASYNC: strip {strip.strip_id} парсинг результата: {parsed_lens}"
                )

                return strip, index_results, strip_idx

            except Exception as e:
                logger.error(
                    f"PASS2 ASYNC: strip processing error {strip.strip_id}",
                    extra={
                        "event": "pass2_strip_error",
                        "strip_id": strip.strip_id,
                        "block_ids": [bp["block_id"] for bp in strip.block_parts],
                        "block_count": len(strip.block_parts),
                    },
                    exc_info=True,
                )
                error_results = {i: make_error(str(e)) for i in range(len(strip.block_parts))}
                return strip, error_results, strip_idx

    # === Worker loop ===
    logger.info(
        f"PASS2 ASYNC: обработка {len(strips)} strips "
        f"({ctx.max_workers} workers, bounded queue)"
    )

    strip_queue: asyncio.Queue = asyncio.Queue()
    for idx, strip in enumerate(strips):
        strip_queue.put_nowait((strip, idx))

    async def _worker():
        while not strip_queue.empty():
            if ctx.is_paused():
                drain_queue(strip_queue)
                return
            try:
                strip, idx = strip_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            try:
                result = await _process_strip(strip, idx)
            except Exception as exc:
                logger.error(f"PASS2 ASYNC: strip exception: {exc}", exc_info=True)
                await ctx.update_progress("Strip (error)")
                strip_queue.task_done()
                continue

            if result:
                strip_obj, index_results, strip_idx = result

                block_results = {}
                for i, bp in enumerate(strip_obj.block_parts):
                    block_id = bp["block_id"]
                    part_idx = bp["part_idx"]
                    total_parts = bp["total_parts"]
                    text = index_results.get(i, "")

                    if block_id not in text_block_parts:
                        text_block_parts[block_id] = {}
                        text_block_total_parts[block_id] = total_parts

                    text_block_parts[block_id][part_idx] = text
                    block_results[block_id] = text

                ctx.checkpoint.mark_strip_processed(strip_obj.strip_id, block_results)
                await ctx.save_checkpoint()

                num_blocks = len(strip_obj.block_parts)
                if num_blocks == 1:
                    suffix = ""
                elif num_blocks < 5:
                    suffix = "а"
                else:
                    suffix = "ов"
                await ctx.update_progress(f"Strip ({num_blocks} блок{suffix})")
            else:
                await ctx.update_progress("Strip")

            gc.collect()
            strip_queue.task_done()

    workers = [asyncio.create_task(_worker()) for _ in range(ctx.max_workers)]
    await asyncio.gather(*workers)

    # Собираем части TEXT/TABLE блоков
    for block_id, parts_dict in text_block_parts.items():
        if block_id not in ctx.blocks_by_id:
            continue
        block = ctx.blocks_by_id[block_id]
        total_parts = text_block_total_parts.get(block_id, 1)

        if total_parts == 1:
            block.ocr_text = parts_dict.get(0, "")
        else:
            combined = [parts_dict.get(i, "") for i in range(total_parts)]
            block.ocr_text = "\n\n".join(combined)
        logger.info(
            f"PASS2 ASYNC TEXT блок {block_id}: ocr_text длина = "
            f"{len(block.ocr_text) if block.ocr_text else 0}"
        )

    return text_block_parts, text_block_total_parts
