"""Singleton для EmbeddedJobManager.

Отдельный модуль чтобы избежать circular imports —
queue_checker и routes/jobs могут импортировать manager
не зависимо от main.py.
"""
from __future__ import annotations

from typing import Optional

from .embedded_job_manager import EmbeddedJobManager
from .settings import settings

_manager: Optional[EmbeddedJobManager] = None


def init_job_manager(on_job_event=None) -> EmbeddedJobManager:
    """Создать и инициализировать глобальный job manager."""
    global _manager
    _manager = EmbeddedJobManager(
        max_workers=settings.max_concurrent_jobs,
        hard_timeout=settings.task_hard_timeout,
        on_job_event=on_job_event,
    )
    return _manager


def get_job_manager() -> EmbeddedJobManager:
    """Получить глобальный job manager. Создаёт если не инициализирован."""
    global _manager
    if _manager is None:
        _manager = EmbeddedJobManager(
            max_workers=settings.max_concurrent_jobs,
            hard_timeout=settings.task_hard_timeout,
        )
    return _manager
