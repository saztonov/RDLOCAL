"""PASS 2: Обработка блоков через async OCR (последовательно или параллельно)."""
from __future__ import annotations

import asyncio
import gc
import os
from pathlib import Path
from typing import List, Optional

from PIL import Image

from ..logging_config import get_logger
from ..manifest_models import CropManifestEntry
from ..ocr_constants import is_error, make_error
from .pass2_shared import (
    CANCELLED_SENTINEL,
    Pass2Context,
    cancellable_recognize,
    should_retry_ocr,
)

logger = get_logger(__name__)


async def _process_one_block(
    entry: CropManifestEntry,
    text_backend,
    image_backend,
    stamp_backend,
    ctx: Pass2Context,
    cancel_event: asyncio.Event,
) -> None:
    """Обработать один блок с retry-логикой."""
    from ..worker_prompts import build_text_prompt, fill_image_prompt_variables

    _max_retries = 2
    _retry_delays = [30, 60]

    if cancel_event.is_set() or ctx.is_paused():
        cancel_event.set()
        return

    if ctx.is_deadline_exceeded():
        cancel_event.set()
        logger.warning(
            f"PASS2: time budget exhausted, пропускаем {entry.block_id}",
            extra={"event": "pass2_budget_exhausted", "block_id": entry.block_id},
        )
        return

    if ctx.checkpoint.is_block_processed(entry.block_id):
        logger.debug(f"Block {entry.block_id} уже обработан (checkpoint), пропускаем")
        return

    block = ctx.blocks_by_id.get(entry.block_id)
    if not block:
        return

    # Выбор backend и промпта по типу блока
    if entry.block_type == "text":
        backend = text_backend
        prompt_data = build_text_prompt(block)
    elif entry.block_type == "stamp":
        backend = stamp_backend
        category_code = getattr(block, "category_code", None) or "stamp"
        prompt_data = fill_image_prompt_variables(
            prompt_data=block.prompt,
            doc_name=Path(ctx.pdf_path).name,
            page_index=block.page_index,
            block_id=block.id,
            category_code=category_code,
            engine=None,
        )
    else:  # image
        backend = image_backend
        prompt_data = fill_image_prompt_variables(
            prompt_data=block.prompt,
            doc_name=Path(ctx.pdf_path).name,
            page_index=block.page_index,
            block_id=block.id,
            category_code=getattr(block, "category_code", None),
            engine=None,
        )

    # Определяем, использовать ли PDF crop
    use_pdf = (
        entry.pdf_crop_path
        and entry.block_type in ("image", "stamp")
        and os.path.exists(entry.pdf_crop_path)
        and hasattr(backend, "supports_pdf_input")
        and backend.supports_pdf_input()
    )

    if not use_pdf and not os.path.exists(entry.crop_path):
        logger.warning(f"Crop не найден: {entry.crop_path}")
        return

    if cancel_event.is_set() or ctx.is_paused():
        cancel_event.set()
        return

    try:
        logger.info(
            f"PASS2: обработка {entry.block_type} блока {entry.block_id}",
            extra={
                "event": "block_ocr_start",
                "block_id": entry.block_id,
                "page_index": entry.page_index,
                "backend_type": type(backend).__name__,
                "block_type": entry.block_type,
                "use_pdf_crop": bool(use_pdf),
            },
        )

        text = None
        for attempt in range(_max_retries + 1):
            if attempt > 0:
                if cancel_event.is_set() or ctx.is_paused():
                    cancel_event.set()
                    return
                delay = _retry_delays[min(attempt - 1, len(_retry_delays) - 1)]
                logger.warning(
                    f"PASS2: {entry.block_id} retry "
                    f"{attempt}/{_max_retries}, ожидание {delay}с"
                )
                await asyncio.sleep(delay)

            if not await ctx.rate_limiter.acquire_async():
                logger.warning(f"Block {entry.block_id}: rate limiter timeout")
                if attempt < _max_retries:
                    continue
                text = make_error("rate limiter timeout")
                break

            try:
                if use_pdf:
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
                    cancel_event.set()
                    return
            except Exception as ocr_err:
                text = make_error(str(ocr_err))
            finally:
                await ctx.rate_limiter.release_async()

            if not should_retry_ocr(text, f"block {entry.block_id}", attempt, _max_retries):
                break

        # Anti-transliteration: одноразовый retry для image блоков
        if (
            text
            and text is not CANCELLED_SENTINEL
            and entry.block_type == "image"
            and not is_error(text)
        ):
            text = await _check_axis_transliteration(
                text, entry, block, ctx, backend, prompt_data, use_pdf, cancel_event,
            )

        # Сохраняем результат (под lock для безопасности при параллелизме)
        if text and text is not CANCELLED_SENTINEL:
            async with ctx.processed_lock:
                block.ocr_text = text
                ctx.checkpoint.mark_block_processed(entry.block_id, text)
                await ctx.save_checkpoint()

        logger.info(
            f"PASS2: завершён {entry.block_type} блок {entry.block_id}",
            extra={
                "event": "block_ocr_completed",
                "block_id": entry.block_id,
                "page_index": entry.page_index,
                "response_length": len(text) if text else 0,
                "block_type": entry.block_type,
            },
        )

        page_num = block.page_index + 1
        block_info = f"{entry.block_type.capitalize()} (стр. {page_num})"
        await ctx.update_progress(block_info)

    except Exception as e:
        logger.error(
            f"PASS2: block processing error {entry.block_id}",
            extra={
                "event": "pass2_block_error",
                "block_id": entry.block_id,
                "page_index": entry.page_index,
                "block_type": entry.block_type,
                "backend": type(backend).__name__,
            },
            exc_info=True,
        )
        async with ctx.processed_lock:
            block.ocr_text = make_error(str(e))
            ctx.checkpoint.mark_block_processed(entry.block_id, block.ocr_text)
            await ctx.save_checkpoint()
        await ctx.update_progress(f"{entry.block_type.capitalize()} (error)")

    gc.collect()


async def _check_axis_transliteration(
    text: str,
    entry: CropManifestEntry,
    block,
    ctx: Pass2Context,
    backend,
    prompt_data,
    use_pdf: bool,
    cancel_event: asyncio.Event,
) -> str:
    """Проверить image OCR на латинские lookalike-символы в осях.

    Если обнаружены (A вместо А, B вместо Б, ...) — выполняет один retry
    с усиленным anti-transliteration указанием.
    """
    import json as json_module

    from rd_core.ocr.generator_common import has_latin_axis_lookalikes, parse_ocr_json

    parsed = parse_ocr_json(text)
    if not parsed:
        return text

    grid_lines = ""
    loc = parsed.get("location")
    if isinstance(loc, dict):
        grid_lines = loc.get("grid_lines", "")

    entities_str = " ".join(str(e) for e in (parsed.get("key_entities") or []))

    if not has_latin_axis_lookalikes(grid_lines) and not has_latin_axis_lookalikes(entities_str):
        return text

    logger.warning(
        f"PASS2: {entry.block_id} обнаружены латинские lookalike-символы в осях, retry"
    )

    # Усиленный retry с добавлением anti-transliteration инструкции
    enhanced_prompt = None
    if prompt_data and isinstance(prompt_data, dict):
        enhanced_prompt = dict(prompt_data)
        suffix = (
            "\n\nCRITICAL: The previous attempt used Latin letters for axis labels. "
            "Russian construction axes MUST use Cyrillic: А (not A), Б (not B), В (not V), "
            "Г, Д, Е, Ж, И, К (not K), Л, М (not M), Н (not H), П, Р (not P), С (not C), "
            "Т (not T), У, Ф, Х (not X). Fix all axis labels to Cyrillic."
        )
        enhanced_prompt["user"] = enhanced_prompt.get("user", "") + suffix

    if cancel_event.is_set() or ctx.is_paused():
        return text

    if not await ctx.rate_limiter.acquire_async():
        return text

    try:
        if use_pdf:
            retry_text = await cancellable_recognize(
                ctx, backend, None, enhanced_prompt or prompt_data, None, entry.pdf_crop_path,
            )
        else:
            crop = await asyncio.to_thread(Image.open, entry.crop_path)
            try:
                retry_text = await cancellable_recognize(
                    ctx, backend, crop, enhanced_prompt or prompt_data,
                )
            finally:
                crop.close()
        if retry_text and retry_text is not CANCELLED_SENTINEL and not is_error(retry_text):
            logger.info(f"PASS2: {entry.block_id} anti-transliteration retry успешен")
            return retry_text
    except Exception as e:
        logger.warning(f"PASS2: {entry.block_id} anti-transliteration retry ошибка: {e}")
    finally:
        await ctx.rate_limiter.release_async()

    return text


async def run_blocks_phase(
    block_entries: List[CropManifestEntry],
    blocks: List,
    text_backend,
    image_backend,
    stamp_backend,
    ctx: Pass2Context,
    max_workers: int = 1,
) -> None:
    """Обработать блоки: последовательно (max_workers=1) или параллельно.

    Выбор backend по entry.block_type:
    - "text" → text_backend
    - "stamp" → stamp_backend
    - "image" → image_backend
    """
    cancel_event = asyncio.Event()

    if max_workers <= 1:
        # Последовательный путь (IMAGE/STAMP или fallback)
        for entry in block_entries:
            if cancel_event.is_set():
                return
            await _process_one_block(
                entry, text_backend, image_backend, stamp_backend, ctx, cancel_event,
            )
    else:
        # Параллельный путь (TEXT блоки через Chandra)
        sem = asyncio.Semaphore(max_workers)

        async def _guarded(entry: CropManifestEntry) -> None:
            if cancel_event.is_set():
                return
            async with sem:
                if cancel_event.is_set():
                    return
                await _process_one_block(
                    entry, text_backend, image_backend, stamp_backend, ctx, cancel_event,
                )

        await asyncio.gather(*[_guarded(e) for e in block_entries])
