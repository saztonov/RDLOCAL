"""Утилиты для расчёта динамического таймаута OCR задач."""

from __future__ import annotations

import json
import logging
from typing import Union

from .settings import settings

logger = logging.getLogger(__name__)


def count_blocks_from_data(blocks_data: Union[list, dict]) -> int:
    """Подсчитать количество блоков из данных.

    Args:
        blocks_data: Данные блоков в формате списка или document-структуры

    Returns:
        Количество блоков
    """
    if isinstance(blocks_data, list):
        return len(blocks_data)
    elif isinstance(blocks_data, dict) and "pages" in blocks_data:
        return sum(len(p.get("blocks", [])) for p in blocks_data.get("pages", []))
    return 0


def calculate_dynamic_timeout(block_count: int) -> tuple[int, int]:
    """Рассчитать динамический таймаут на основе количества блоков.

    Формула: base + (block_count * seconds_per_block) + verification_buffer
    Результат ограничен min_task_timeout и max_task_timeout.

    verification_buffer: min(block_count * 5, verification_timeout_minutes * 60)
    — резерв на верификацию пропущенных блоков.

    Args:
        block_count: Количество блоков в документе

    Returns:
        Tuple (soft_timeout, hard_timeout) в секундах
    """
    ocr_timeout = settings.dynamic_timeout_base + (
        block_count * settings.seconds_per_block
    )

    # Резерв на верификацию: пропорционально блокам, но не больше verification_timeout
    verification_buffer = min(
        block_count * 5,
        settings.verification_timeout_minutes * 60,
    )

    soft_timeout = ocr_timeout + verification_buffer
    soft_timeout = max(
        settings.min_task_timeout, min(soft_timeout, settings.max_task_timeout)
    )

    # Hard timeout = soft + 10 минут запаса
    hard_timeout = soft_timeout + 600

    logger.info(
        f"Динамический таймаут: soft={soft_timeout}s, hard={hard_timeout}s "
        f"для {block_count} блоков "
        f"(base={settings.dynamic_timeout_base}, per_block={settings.seconds_per_block}, "
        f"verification_buffer={verification_buffer}s)"
    )

    return soft_timeout, hard_timeout


def parse_blocks_json(content: Union[str, bytes]) -> Union[list, dict]:
    """Распарсить JSON с блоками.

    Args:
        content: JSON строка или bytes

    Returns:
        Распарсенные данные блоков
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    return json.loads(content)
