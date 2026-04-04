"""Проверка ёмкости очереди для backpressure — embedded режим.

Заменяет Redis/Celery-based проверку на запрос к EmbeddedJobManager.
"""
from __future__ import annotations

from .settings import settings


def _get_manager():
    """Получить singleton EmbeddedJobManager."""
    from .embedded_job_manager_singleton import get_job_manager
    return get_job_manager()


def get_queue_size() -> int:
    """Получить текущий размер очереди (pending)."""
    try:
        manager = _get_manager()
        return manager.pending_count
    except Exception:
        return 0


def get_active_count() -> int:
    """Получить количество активных задач."""
    try:
        manager = _get_manager()
        return manager.active_count
    except Exception:
        return 0


def is_queue_full() -> bool:
    """Проверить, переполнена ли очередь."""
    if settings.max_queue_size <= 0:
        return False
    return get_queue_size() + get_active_count() >= settings.max_queue_size


def check_queue_capacity() -> tuple[bool, int, int]:
    """Проверить ёмкость очереди.

    Returns:
        (can_accept, current_total_load, max_size)
    """
    current = get_queue_size() + get_active_count()
    max_size = settings.max_queue_size
    can_accept = max_size <= 0 or current < max_size
    return can_accept, current, max_size
