"""Утилиты локального хранения файлов standalone OCR-задач.

Standalone jobs (без node_id) хранят входные и выходные файлы на диске
вместо R2, чтобы избежать бессмысленного roundtrip через облако.

Структура:
    {data_dir}/ocr_jobs/{job_id}/
        input/
            document.pdf
            blocks.json
        output/
            crops/
            {stem}_ocr.html
            {stem}_document.md
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .logging_config import get_logger
from .settings import settings

logger = get_logger(__name__)

LOCAL_PREFIX = "local://"


def local_job_dir(job_id: str) -> Path:
    return Path(settings.data_dir) / "ocr_jobs" / job_id


def local_input_dir(job_id: str) -> Path:
    return local_job_dir(job_id) / "input"


def local_output_dir(job_id: str) -> Path:
    return local_job_dir(job_id) / "output"


def is_local_path(key: str | None) -> bool:
    return bool(key and key.startswith(LOCAL_PREFIX))


def resolve_local_path(key: str) -> Path:
    return Path(settings.data_dir) / key.removeprefix(LOCAL_PREFIX)


def ensure_dirs(job_id: str) -> None:
    local_input_dir(job_id).mkdir(parents=True, exist_ok=True)
    local_output_dir(job_id).mkdir(parents=True, exist_ok=True)


def cleanup_job(job_id: str) -> None:
    job_dir = local_job_dir(job_id)
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
        logger.info(f"Удалена локальная директория: {job_dir}")
