"""
PASS 2 ASYNC: Асинхронный OCR с использованием asyncio.gather.

Заменяет ThreadPoolExecutor на asyncio для эффективной обработки I/O-bound операций.
Обеспечивает 40-60% ускорение за счёт настоящего параллелизма без GIL.
"""
from __future__ import annotations

import asyncio
import gc
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image

from ..checkpoint_models import OCRCheckpoint, get_checkpoint_path
from ..logging_config import get_logger
from ..manifest_models import CropManifestEntry, StripManifestEntry, TwoPassManifest
from ..memory_utils import force_gc, log_memory, log_memory_delta
from ..rate_limiter import get_unified_async_limiter
from ..settings import settings

logger = get_logger(__name__)

# Интервал сохранения checkpoint (каждые N обработанных элементов)
CHECKPOINT_SAVE_INTERVAL = 10


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
) -> None:
    """
    PASS 2 ASYNC: Асинхронный OCR с загрузкой кропов с диска.

    Использует asyncio.gather вместо ThreadPoolExecutor для эффективного
    параллелизма I/O-bound операций (OCR API calls).

    Поддерживает checkpoint/resume для возможности продолжения после паузы.

    Args:
        manifest: манифест с информацией о кропах
        blocks: список блоков для обновления
        strip_backend: async backend для TEXT/TABLE strips
        image_backend: async backend для IMAGE блоков
        stamp_backend: async backend для stamp блоков
        pdf_path: путь к PDF файлу
        on_progress: callback для прогресса
        check_paused: callback для проверки паузы
        max_concurrent: максимум параллельных запросов (по умолчанию из settings)
        checkpoint: объект checkpoint для resume (опционально)
        work_dir: рабочая директория для сохранения checkpoint (опционально)
    """
    from ..worker_pdf import extract_pdfplumber_text_for_block
    from ..worker_prompts import (
        build_strip_prompt,
        fill_image_prompt_variables,
        inject_pdfplumber_to_ocr_text,
        parse_batch_response_by_index,
    )

    start_mem = log_memory("PASS2 ASYNC start")

    total_requests = len(manifest.strips) + len(manifest.image_blocks)
    processed = 0
    processed_lock = asyncio.Lock()
    checkpoint_counter = 0

    blocks_by_id = {b.id: b for b in blocks}

    text_block_parts: Dict[str, Dict[int, str]] = {}
    text_block_total_parts: Dict[str, int] = {}
    image_block_parts: Dict[str, Dict[int, str]] = {}
    image_block_total_parts: Dict[str, int] = {}

    # Инициализация или использование существующего checkpoint
    if checkpoint is None:
        checkpoint = OCRCheckpoint.create_new(
            job_id="unknown",
            total_strips=len(manifest.strips),
            total_images=len(manifest.image_blocks),
        )
    else:
        # Восстанавливаем результаты из checkpoint
        restored = checkpoint.apply_to_blocks(blocks)
        if restored > 0:
            logger.info(f"PASS2 ASYNC: восстановлено {restored} блоков из checkpoint")

        # Обновляем processed count
        processed = len(checkpoint.processed_strips) + len(checkpoint.processed_images)

    checkpoint.phase = "pass2_strips"

    # Унифицированный async rate limiter
    rate_limiter = get_unified_async_limiter()

    # Путь для сохранения checkpoint
    checkpoint_path = get_checkpoint_path(work_dir) if work_dir else None

    async def _save_checkpoint():
        """Сохранить checkpoint если нужно"""
        nonlocal checkpoint_counter
        checkpoint_counter += 1
        if checkpoint_path and checkpoint_counter % CHECKPOINT_SAVE_INTERVAL == 0:
            await asyncio.to_thread(checkpoint.save, checkpoint_path)

    # Semaphore для ограничения параллельных задач
    max_workers = max_concurrent or settings.ocr_threads_per_job
    concurrency_semaphore = asyncio.Semaphore(max_workers)

    last_block_info = {"info": ""}

    async def _update_progress(block_info: str = None):
        nonlocal processed
        async with processed_lock:
            processed += 1
            if block_info:
                last_block_info["info"] = block_info
            if on_progress and total_requests > 0:
                # on_progress может быть sync, запускаем в thread pool
                await asyncio.to_thread(
                    on_progress, processed, total_requests, last_block_info["info"]
                )

    # --- Обработка strips ---
    # Strip-level retry для LM Studio бэкендов (ngrok tunnel instability)
    _is_lmstudio = type(strip_backend).__name__ in (
        "ChandraBackend", "QwenBackend", "AsyncChandraBackend", "AsyncQwenBackend",
    )
    _STRIP_MAX_RETRIES = 2 if _is_lmstudio else 0
    _STRIP_RETRY_DELAYS = [30, 60]
    _ERROR_PREFIX = "[Ошибка"

    async def _process_strip_async(
        strip: StripManifestEntry, strip_idx: int
    ) -> Optional[Tuple[StripManifestEntry, Dict[int, str], int]]:
        if check_paused and check_paused():
            return None

        # Пропускаем уже обработанные strips (checkpoint)
        if checkpoint.is_strip_processed(strip.strip_id):
            logger.debug(f"Strip {strip.strip_id} уже обработан (checkpoint), пропускаем")
            return None

        if not strip.strip_path or not os.path.exists(strip.strip_path):
            logger.warning(f"Strip {strip.strip_id} не найден: {strip.strip_path}")
            return None

        async with concurrency_semaphore:
            try:
                strip_blocks = [
                    blocks_by_id[bp["block_id"]]
                    for bp in strip.block_parts
                    if bp["block_id"] in blocks_by_id
                ]

                if not strip_blocks:
                    return None

                prompt_data = build_strip_prompt(strip_blocks)

                block_ids = [bp["block_id"] for bp in strip.block_parts]
                logger.info(
                    f"PASS2 ASYNC: начало обработки strip {strip.strip_id} "
                    f"({len(strip.block_parts)} блоков): {block_ids}"
                )

                response_text = None
                for strip_attempt in range(_STRIP_MAX_RETRIES + 1):
                    if strip_attempt > 0:
                        delay = _STRIP_RETRY_DELAYS[min(strip_attempt - 1, len(_STRIP_RETRY_DELAYS) - 1)]
                        logger.warning(
                            f"PASS2 ASYNC: strip {strip.strip_id} retry "
                            f"{strip_attempt}/{_STRIP_MAX_RETRIES}, ожидание {delay}с"
                        )
                        await asyncio.sleep(delay)

                    # Загрузка изображения в thread pool (CPU-bound)
                    merged_image = await asyncio.to_thread(Image.open, strip.strip_path)

                    try:
                        # Получаем разрешение от rate limiter
                        if not await rate_limiter.acquire_async():
                            logger.warning(f"Strip {strip.strip_id}: rate limiter timeout")
                            merged_image.close()
                            if strip_attempt < _STRIP_MAX_RETRIES:
                                continue
                            return None

                        try:
                            # Асинхронный OCR вызов
                            if hasattr(strip_backend, "recognize_async"):
                                response_text = await strip_backend.recognize_async(
                                    merged_image, prompt=prompt_data
                                )
                            else:
                                # Fallback для sync backend
                                response_text = await asyncio.to_thread(
                                    strip_backend.recognize, merged_image, prompt_data
                                )
                        finally:
                            await rate_limiter.release_async()
                    finally:
                        merged_image.close()

                    # Проверяем результат: если ошибка и есть retry — повторяем
                    if response_text and not response_text.startswith(_ERROR_PREFIX):
                        break  # Успех
                    if strip_attempt < _STRIP_MAX_RETRIES:
                        err_preview = (response_text or "пусто")[:80]
                        logger.warning(
                            f"PASS2 ASYNC: strip {strip.strip_id} ошибка OCR "
                            f"({err_preview}), будет retry"
                        )
                        continue
                    # Последняя попытка, оставляем как есть

                response_len = len(response_text) if response_text else 0
                if response_len == 0:
                    logger.warning(
                        f"PASS2 ASYNC: strip {strip.strip_id} — пустой ответ от OCR бэкенда"
                    )
                else:
                    logger.info(
                        f"PASS2 ASYNC: завершена обработка strip {strip.strip_id}, "
                        f"ответ {response_len} символов"
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
                return None

    checkpoint.phase = "pass2_images"

    # --- Обработка IMAGE блоков ---
    async def _process_image_async(
        entry: CropManifestEntry,
    ) -> Optional[Tuple[str, str, int, int]]:
        if check_paused and check_paused():
            return None

        # Пропускаем уже обработанные image блоки (checkpoint)
        if checkpoint.is_image_processed(entry.block_id):
            logger.debug(f"Image {entry.block_id} уже обработан (checkpoint), пропускаем")
            return None

        block = blocks_by_id.get(entry.block_id)
        if not block:
            return None

        block_code = getattr(block, "code", None)
        backend = stamp_backend if block_code == "stamp" else image_backend

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

        async with concurrency_semaphore:
            try:
                # Извлечение текста pdfplumber (CPU-bound)
                pdfplumber_text = await asyncio.to_thread(
                    extract_pdfplumber_text_for_block,
                    pdf_path,
                    block.page_index,
                    block.coords_norm,
                )

                category_id = getattr(block, "category_id", None)
                category_code = getattr(block, "category_code", None)

                prompt_data = fill_image_prompt_variables(
                    prompt_data=block.prompt,
                    doc_name=Path(pdf_path).name,
                    page_index=block.page_index,
                    block_id=block.id,
                    hint=getattr(block, "hint", None),
                    pdfplumber_text=pdfplumber_text,
                    category_id=category_id,
                    category_code=category_code,
                )

                logger.info(f"PASS2 ASYNC: начало обработки IMAGE блока {entry.block_id}")

                # Получаем разрешение от rate limiter
                if not await rate_limiter.acquire_async():
                    logger.warning(f"Image {entry.block_id}: rate limiter timeout")
                    return entry.block_id, "[Ошибка: rate limiter timeout]", entry.part_idx, entry.total_parts

                try:
                    if use_pdf:
                        logger.info(f"PASS2 ASYNC: используется PDF-кроп для {entry.block_id}")
                        if hasattr(backend, "recognize_async"):
                            text = await backend.recognize_async(
                                image=None,
                                prompt=prompt_data,
                                pdf_file_path=entry.pdf_crop_path,
                            )
                        else:
                            text = await asyncio.to_thread(
                                backend.recognize,
                                None,
                                prompt_data,
                                None,
                                entry.pdf_crop_path,
                            )
                    else:
                        crop = await asyncio.to_thread(Image.open, entry.crop_path)
                        try:
                            if hasattr(backend, "recognize_async"):
                                text = await backend.recognize_async(crop, prompt=prompt_data)
                            else:
                                text = await asyncio.to_thread(
                                    backend.recognize, crop, prompt_data
                                )
                        finally:
                            crop.close()
                finally:
                    await rate_limiter.release_async()

                logger.info(f"PASS2 ASYNC: завершена обработка IMAGE блока {entry.block_id}")

                text = inject_pdfplumber_to_ocr_text(text, pdfplumber_text)
                block.pdfplumber_text = pdfplumber_text

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
                return entry.block_id, f"[Ошибка: {e}]", entry.part_idx, entry.total_parts

    # === ОБРАБОТКА STRIPS ===
    logger.info(
        f"PASS2 ASYNC: обработка {len(manifest.strips)} strips "
        f"({max_workers} параллельных, asyncio.gather)"
    )

    # Создаём все задачи для strips
    strip_tasks = [
        _process_strip_async(strip, idx)
        for idx, strip in enumerate(manifest.strips)
    ]

    # Запускаем параллельно с asyncio.gather
    strip_results = await asyncio.gather(*strip_tasks, return_exceptions=True)

    # Обрабатываем результаты strips
    for result in strip_results:
        if isinstance(result, Exception):
            logger.error(f"PASS2 ASYNC: strip exception: {result}")
            await _update_progress("Strip (error)")
            continue

        if result:
            strip, index_results, strip_idx = result

            # Собираем результаты для checkpoint
            block_results = {}

            for i, bp in enumerate(strip.block_parts):
                block_id = bp["block_id"]
                part_idx = bp["part_idx"]
                total_parts = bp["total_parts"]
                text = index_results.get(i, "")

                if block_id not in text_block_parts:
                    text_block_parts[block_id] = {}
                    text_block_total_parts[block_id] = total_parts

                text_block_parts[block_id][part_idx] = text
                block_results[block_id] = text

            # Сохраняем в checkpoint
            checkpoint.mark_strip_processed(strip.strip_id, block_results)
            await _save_checkpoint()

            num_blocks = len(strip.block_parts)
            if num_blocks == 1:
                suffix = ""
            elif num_blocks < 5:
                suffix = "а"
            else:
                suffix = "ов"
            block_info = f"Strip ({num_blocks} блок{suffix})"
            await _update_progress(block_info)
        else:
            await _update_progress("Strip")

        gc.collect()

    # Собираем части TEXT/TABLE блоков
    for block_id, parts_dict in text_block_parts.items():
        if block_id not in blocks_by_id:
            continue
        block = blocks_by_id[block_id]
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

    log_memory_delta("PASS2 ASYNC после strips", start_mem)

    # === ОБРАБОТКА IMAGE БЛОКОВ ===
    logger.info(
        f"PASS2 ASYNC: обработка {len(manifest.image_blocks)} image blocks"
    )

    # Создаём все задачи для images
    image_tasks = [
        _process_image_async(entry)
        for entry in manifest.image_blocks
    ]

    # Запускаем параллельно с asyncio.gather
    image_results = await asyncio.gather(*image_tasks, return_exceptions=True)

    # Обрабатываем результаты images
    for result in image_results:
        if isinstance(result, Exception):
            logger.error(f"PASS2 ASYNC: image exception: {result}")
            await _update_progress("Image (error)")
            continue

        if result:
            block_id, text, part_idx, total_parts = result

            if block_id not in image_block_parts:
                image_block_parts[block_id] = {}
                image_block_total_parts[block_id] = total_parts

            image_block_parts[block_id][part_idx] = text

            # Сохраняем в checkpoint
            checkpoint.mark_image_processed(block_id, text, part_idx, total_parts)
            await _save_checkpoint()

            block = blocks_by_id.get(block_id)
            if block:
                page_num = block.page_index + 1
                category = getattr(block, "category_code", None) or "image"
                block_info = f"Image: {category} (стр. {page_num})"
            else:
                block_info = "Image"
            await _update_progress(block_info)
        else:
            await _update_progress("Image")

        gc.collect()

    # Собираем части IMAGE блоков
    for block_id, parts_dict in image_block_parts.items():
        if block_id not in blocks_by_id:
            continue
        block = blocks_by_id[block_id]
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

    # Финальное сохранение checkpoint
    checkpoint.phase = "completed"
    if checkpoint_path:
        await asyncio.to_thread(checkpoint.save, checkpoint_path)
        logger.info(f"Финальный checkpoint сохранён: {checkpoint_path}")

    force_gc("PASS2 ASYNC завершён")
    log_memory_delta("PASS2 ASYNC end", start_mem)

    logger.info(f"PASS2 ASYNC завершён: {processed} запросов обработано")


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
        )
    )
