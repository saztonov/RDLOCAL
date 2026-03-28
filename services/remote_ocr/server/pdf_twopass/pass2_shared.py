"""Общие утилиты для pass2 async OCR: retry, checkpoint cadence, progress, queue drain."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional

from ..checkpoint_models import OCRCheckpoint, get_checkpoint_path
from ..logging_config import get_logger
from ..ocr_constants import is_error, is_non_retriable
from ..rate_limiter import get_unified_async_limiter

logger = get_logger(__name__)

# Интервал сохранения checkpoint (каждые N обработанных элементов)
CHECKPOINT_SAVE_INTERVAL = 10

# Sentinel для обнаружения отмены во время OCR-запроса
CANCELLED_SENTINEL = object()

# Резерв времени для upload/finalize (секунды)
DEADLINE_RESERVE = 120


def should_retry_ocr(text: Optional[str], item_id: str, attempt: int, max_retries: int) -> bool:
    """Проверить результат OCR и решить, нужен ли retry."""
    if text and not is_error(text):
        return False
    if is_non_retriable(text):
        logger.warning(f"PASS2 ASYNC: {item_id} неповторяемая ошибка, пропускаем retry")
        return False
    if attempt < max_retries:
        err_preview = (text or "пусто")[:80]
        logger.warning(f"PASS2 ASYNC: {item_id} ошибка OCR ({err_preview}), будет retry")
        return True
    return False


@dataclass
class Pass2Context:
    """Контекст для передачи между фазами pass2 OCR."""

    blocks_by_id: Dict
    checkpoint: OCRCheckpoint
    on_progress: Optional[Callable[[int, int, str], None]]
    check_paused: Optional[Callable[[], bool]]
    deadline: Optional[float]
    work_dir: Optional[Path]
    max_workers: int
    total_requests: int
    pdf_path: str

    # Мутируемое состояние
    processed: int = 0
    processed_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    checkpoint_counter: int = 0
    checkpoint_path: Optional[Path] = None
    rate_limiter: object = None
    concurrency_semaphore: asyncio.Semaphore = None
    last_block_info: Dict = field(default_factory=lambda: {"info": ""})

    def __post_init__(self):
        self.checkpoint_path = get_checkpoint_path(self.work_dir) if self.work_dir else None
        self.rate_limiter = get_unified_async_limiter()
        self.concurrency_semaphore = asyncio.Semaphore(self.max_workers)

    def is_paused(self) -> bool:
        """Безопасная проверка паузы."""
        if not self.check_paused:
            return False
        try:
            return self.check_paused()
        except Exception as exc:
            logger.warning(f"PASS2 ASYNC: ошибка в check_paused: {exc}")
            return False

    async def save_checkpoint(self):
        """Сохранить checkpoint если нужно."""
        self.checkpoint_counter += 1
        if self.checkpoint_path and self.checkpoint_counter % CHECKPOINT_SAVE_INTERVAL == 0:
            await asyncio.to_thread(self.checkpoint.save, self.checkpoint_path)

    async def update_progress(self, block_info: str = None):
        """Обновить прогресс."""
        async with self.processed_lock:
            self.processed += 1
            if block_info:
                self.last_block_info["info"] = block_info
            if self.on_progress and self.total_requests > 0:
                try:
                    await asyncio.to_thread(
                        self.on_progress, self.processed, self.total_requests,
                        self.last_block_info["info"],
                    )
                except Exception as exc:
                    logger.warning(f"PASS2 ASYNC: ошибка в on_progress callback: {exc}")

    def is_deadline_exceeded(self) -> bool:
        """Проверка time budget."""
        import time
        return bool(self.deadline and time.time() > self.deadline - DEADLINE_RESERVE)


async def cancellable_recognize(ctx: Pass2Context, backend, *args, check_interval=5.0):
    """Вызов backend.recognize с проверкой отмены каждые check_interval секунд."""
    loop = asyncio.get_event_loop()
    future = loop.run_in_executor(None, backend.recognize, *args)
    while True:
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=check_interval)
        except asyncio.TimeoutError:
            if ctx.is_paused():
                future.cancel()
                logger.info("PASS2 ASYNC: OCR-запрос прерван — задача отменена")
                return CANCELLED_SENTINEL


def drain_queue(queue: asyncio.Queue) -> None:
    """Очистить очередь при отмене."""
    while not queue.empty():
        try:
            queue.get_nowait()
            queue.task_done()
        except asyncio.QueueEmpty:
            break
