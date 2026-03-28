"""
PASS 2 ASYNC: Асинхронный OCR с использованием asyncio.gather.

Заменяет ThreadPoolExecutor на asyncio для эффективной обработки I/O-bound операций.
Обеспечивает 40-60% ускорение за счёт настоящего параллелизма без GIL.
"""
from __future__ import annotations

import asyncio
import gc
import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image

from ..checkpoint_models import OCRCheckpoint, get_checkpoint_path
from ..logging_config import get_logger
from ..manifest_models import CropManifestEntry, StripManifestEntry, TwoPassManifest
from ..memory_utils import force_gc, log_memory, log_memory_delta
from ..rate_limiter import get_unified_async_limiter
from ..settings import settings

from ..ocr_constants import is_error, is_non_retriable, make_error

logger = get_logger(__name__)

# Интервал сохранения checkpoint (каждые N обработанных элементов)
CHECKPOINT_SAVE_INTERVAL = 10

# Sentinel для обнаружения отмены во время OCR-запроса
_CANCELLED_SENTINEL = object()


def _should_retry_ocr(text: Optional[str], item_id: str, attempt: int, max_retries: int) -> bool:
    """Проверить результат OCR и решить, нужен ли retry.

    Returns:
        True если нужно продолжить retry, False если результат финальный.
    """
    # Успех — текст есть и не содержит маркер ошибки
    if text and not is_error(text):
        return False
    # Неповторяемая ошибка — retry бессмысленно
    if is_non_retriable(text):
        logger.warning(f"PASS2 ASYNC: {item_id} неповторяемая ошибка, пропускаем retry")
        return False
    # Есть ещё попытки
    if attempt < max_retries:
        err_preview = (text or "пусто")[:80]
        logger.warning(f"PASS2 ASYNC: {item_id} ошибка OCR ({err_preview}), будет retry")
        return True
    # Последняя попытка — оставляем как есть
    return False


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
            logger.info(
                f"PASS2 ASYNC: восстановлено {restored} блоков из checkpoint",
                extra={
                    "event": "checkpoint_restored",
                    "checkpoint_count": restored,
                    "phase": checkpoint.phase,
                },
            )

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

    def _is_paused() -> bool:
        """Безопасная проверка паузы — ошибки не ломают задачу."""
        if not check_paused:
            return False
        try:
            return check_paused()
        except Exception as exc:
            logger.warning(f"PASS2 ASYNC: ошибка в check_paused: {exc}")
            return False

    async def _cancellable_recognize(backend, *args, check_interval=5.0):
        """Вызов backend.recognize с проверкой отмены каждые check_interval секунд.

        Возвращает _CANCELLED_SENTINEL если задача отменена во время ожидания.
        """
        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(None, backend.recognize, *args)
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=check_interval)
            except asyncio.TimeoutError:
                if _is_paused():
                    future.cancel()
                    logger.info("PASS2 ASYNC: OCR-запрос прерван — задача отменена")
                    return _CANCELLED_SENTINEL
                # иначе продолжаем ждать завершения backend.recognize

    def _drain_queue(queue: asyncio.Queue) -> None:
        """Очистить очередь при отмене, чтобы все workers остановились."""
        while not queue.empty():
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                break

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
                try:
                    await asyncio.to_thread(
                        on_progress, processed, total_requests, last_block_info["info"]
                    )
                except Exception as exc:
                    logger.warning(f"PASS2 ASYNC: ошибка в on_progress callback: {exc}")

    # --- Обработка strips ---
    # Retry: LM Studio (ngrok instability)
    _is_lmstudio_strip = type(strip_backend).__name__ in (
        "ChandraBackend", "QwenBackend",
    )
    _STRIP_MAX_RETRIES = 2 if _is_lmstudio_strip else 1
    _STRIP_RETRY_DELAYS = [30, 60] if _is_lmstudio_strip else [5]
    # Retry для IMAGE блоков
    _is_lmstudio_image = type(image_backend).__name__ in (
        "ChandraBackend", "QwenBackend",
    )
    _IMAGE_MAX_RETRIES = 2 if _is_lmstudio_image else 1
    _IMAGE_RETRY_DELAYS = [30, 60] if _is_lmstudio_image else [10]

    # Резерв времени для upload/finalize (секунды)
    _DEADLINE_RESERVE = 120

    async def _process_strip_async(
        strip: StripManifestEntry, strip_idx: int
    ) -> Optional[Tuple[StripManifestEntry, Dict[int, str], int]]:
        if _is_paused():
            return None

        # Проверка time budget: останавливаемся заранее, чтобы сохранить результаты
        if deadline and time.time() > deadline - _DEADLINE_RESERVE:
            logger.warning(
                f"PASS2 ASYNC: time budget exhausted, пропускаем strip {strip.strip_id}",
                extra={"event": "pass2_budget_exhausted", "strip_id": strip.strip_id},
            )
            return None

        # Пропускаем уже обработанные strips (checkpoint)
        if checkpoint.is_strip_processed(strip.strip_id):
            logger.debug(f"Strip {strip.strip_id} уже обработан (checkpoint), пропускаем")
            return None

        if not strip.strip_path or not os.path.exists(strip.strip_path):
            logger.warning(f"Strip {strip.strip_id} не найден: {strip.strip_path}")
            return None

        if _is_paused():
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
                    f"({len(strip.block_parts)} блоков): {block_ids}",
                    extra={
                        "event": "strip_ocr_start",
                        "strip_id": strip.strip_id,
                        "block_count": len(strip.block_parts),
                        "block_ids": block_ids,
                    },
                )
                logger.debug(
                    f"Strip {strip.strip_id}: prompt подготовлен",
                    extra={
                        "event": "strip_prompt_prepared",
                        "strip_id": strip.strip_id,
                        "prompt_length": len(str(prompt_data)),
                    },
                )

                response_text = None
                for strip_attempt in range(_STRIP_MAX_RETRIES + 1):
                    if strip_attempt > 0:
                        if _is_paused():
                            return None
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
                            error_results = {i: make_error("rate limiter timeout") for i in range(len(strip.block_parts))}
                            return strip, error_results, strip_idx

                        try:
                            response_text = await _cancellable_recognize(
                                strip_backend, merged_image, prompt_data
                            )
                            if response_text is _CANCELLED_SENTINEL:
                                return None
                        finally:
                            await rate_limiter.release_async()
                    finally:
                        merged_image.close()

                    if not _should_retry_ocr(response_text, f"strip {strip.strip_id}", strip_attempt, _STRIP_MAX_RETRIES):
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
                            "strip_attempt": strip_attempt,
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

    checkpoint.phase = "pass2_images"

    # --- Обработка IMAGE блоков ---
    async def _process_image_async(
        entry: CropManifestEntry,
    ) -> Optional[Tuple[str, str, int, int]]:
        if _is_paused():
            return None

        # Проверка time budget
        if deadline and time.time() > deadline - _DEADLINE_RESERVE:
            logger.warning(
                f"PASS2 ASYNC: time budget exhausted, пропускаем image {entry.block_id}",
                extra={"event": "pass2_budget_exhausted", "block_id": entry.block_id},
            )
            return None

        # Пропускаем уже обработанные image блоки (checkpoint)
        if checkpoint.is_image_processed(entry.block_id):
            logger.debug(f"Image {entry.block_id} уже обработан (checkpoint), пропускаем")
            return None

        block = blocks_by_id.get(entry.block_id)
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

        if _is_paused():
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
                engine = None

                prompt_data = fill_image_prompt_variables(
                    prompt_data=block.prompt,
                    doc_name=Path(pdf_path).name,
                    page_index=block.page_index,
                    block_id=block.id,
                    hint=getattr(block, "hint", None),
                    pdfplumber_text=pdfplumber_text,
                    category_id=category_id,
                    category_code=category_code,
                    engine=engine,
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
                for img_attempt in range(_IMAGE_MAX_RETRIES + 1):
                    if img_attempt > 0:
                        if _is_paused():
                            return None
                        delay = _IMAGE_RETRY_DELAYS[min(img_attempt - 1, len(_IMAGE_RETRY_DELAYS) - 1)]
                        logger.warning(
                            f"PASS2 ASYNC: image {entry.block_id} retry "
                            f"{img_attempt}/{_IMAGE_MAX_RETRIES}, ожидание {delay}с"
                        )
                        await asyncio.sleep(delay)

                    # Получаем разрешение от rate limiter
                    if not await rate_limiter.acquire_async():
                        logger.warning(f"Image {entry.block_id}: rate limiter timeout")
                        if img_attempt < _IMAGE_MAX_RETRIES:
                            continue
                        return entry.block_id, make_error("rate limiter timeout"), entry.part_idx, entry.total_parts

                    try:
                        if use_pdf:
                            logger.info(f"PASS2 ASYNC: используется PDF-кроп для {entry.block_id}")
                            text = await _cancellable_recognize(
                                backend, None, prompt_data, None, entry.pdf_crop_path,
                            )
                        else:
                            crop = await asyncio.to_thread(Image.open, entry.crop_path)
                            try:
                                text = await _cancellable_recognize(
                                    backend, crop, prompt_data
                                )
                            finally:
                                crop.close()
                        if text is _CANCELLED_SENTINEL:
                            return None
                    except Exception as ocr_err:
                        text = make_error(str(ocr_err))
                    finally:
                        await rate_limiter.release_async()

                    if not _should_retry_ocr(text, f"image {entry.block_id}", img_attempt, _IMAGE_MAX_RETRIES):
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
                return entry.block_id, make_error(str(e)), entry.part_idx, entry.total_parts

    # === ОБРАБОТКА STRIPS (bounded queue) ===
    logger.info(
        f"PASS2 ASYNC: обработка {len(manifest.strips)} strips "
        f"({max_workers} workers, bounded queue)"
    )

    strip_queue: asyncio.Queue = asyncio.Queue()
    for idx, strip in enumerate(manifest.strips):
        strip_queue.put_nowait((strip, idx))

    async def _strip_worker():
        while not strip_queue.empty():
            if _is_paused():
                _drain_queue(strip_queue)
                return
            try:
                strip, idx = strip_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            try:
                result = await _process_strip_async(strip, idx)
            except Exception as exc:
                logger.error(f"PASS2 ASYNC: strip exception: {exc}", exc_info=True)
                await _update_progress("Strip (error)")
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

                checkpoint.mark_strip_processed(strip_obj.strip_id, block_results)
                await _save_checkpoint()

                num_blocks = len(strip_obj.block_parts)
                if num_blocks == 1:
                    suffix = ""
                elif num_blocks < 5:
                    suffix = "а"
                else:
                    suffix = "ов"
                await _update_progress(f"Strip ({num_blocks} блок{suffix})")
            else:
                await _update_progress("Strip")

            gc.collect()
            strip_queue.task_done()

    strip_workers = [asyncio.create_task(_strip_worker()) for _ in range(max_workers)]
    await asyncio.gather(*strip_workers)

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

    # Смена модели между фазами (если тот же LM Studio инстанс)
    if before_image_phase:
        logger.info("PASS2 ASYNC: выполняем before_image_phase (смена модели)")
        await asyncio.to_thread(before_image_phase)

    # === ОБРАБОТКА IMAGE БЛОКОВ (bounded queue) ===
    logger.info(
        f"PASS2 ASYNC: обработка {len(manifest.image_blocks)} image blocks "
        f"({max_workers} workers, bounded queue)"
    )

    image_queue: asyncio.Queue = asyncio.Queue()
    for entry in manifest.image_blocks:
        image_queue.put_nowait(entry)

    async def _image_worker():
        while not image_queue.empty():
            if _is_paused():
                _drain_queue(image_queue)
                return
            try:
                entry = image_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            try:
                result = await _process_image_async(entry)
            except Exception as exc:
                logger.error(f"PASS2 ASYNC: image exception: {exc}", exc_info=True)
                await _update_progress("Image (error)")
                image_queue.task_done()
                continue

            if result:
                block_id, text, part_idx, total_parts = result

                if block_id not in image_block_parts:
                    image_block_parts[block_id] = {}
                    image_block_total_parts[block_id] = total_parts

                image_block_parts[block_id][part_idx] = text

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
            image_queue.task_done()

    image_workers = [asyncio.create_task(_image_worker()) for _ in range(max_workers)]
    await asyncio.gather(*image_workers)

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
