"""Контекст OCR-задачи и типизированные исключения."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from .backend_factory import JobBackends
    from .storage_models import Job

    from rd_core.models import Block


# ── Типизированные исключения ────────────────────────────────────────

class JobSkipped(Exception):
    """Задача пропущена (cancelled, done, stale task guard)."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


class JobValidationError(Exception):
    """Невалидная задача (max retries, max runtime)."""


class JobBootstrapError(Exception):
    """Ошибка подготовки задачи (скачивание, парсинг, создание бэкендов)."""


# ── Контекст задачи ──────────────────────────────────────────────────

@dataclass
class JobContext:
    """Всё состояние OCR-задачи, передаётся между стадиями."""

    job: Job
    job_id: str
    work_dir: Path
    crops_dir: Path
    pdf_path: Path
    blocks: List[Block]
    total_blocks: int
    engine: str
    backends: JobBackends
    lmstudio_acquired: bool = False
    start_mem: float = 0.0
    start_time: float = field(default_factory=time.time)
