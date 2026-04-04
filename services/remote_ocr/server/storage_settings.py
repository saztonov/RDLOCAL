"""Операции с настройками задач OCR"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from .logging_config import get_logger
from .storage_client import get_client
from .storage_models import JobSettings

logger = get_logger(__name__)


def get_category_prompt(
    category_code: Optional[str] = None,
    engine: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """
    Получить промпт по категории и движку из config.yaml.
    Возвращает {"system": "...", "user": "..."}
    """
    from .settings import settings

    code = category_code or "default"

    if code == "stamp":
        return {
            "system": settings.stamp_system_prompt,
            "user": settings.stamp_user_prompt,
        }
    return {
        "system": settings.image_system_prompt,
        "user": settings.image_user_prompt,
    }


def save_job_settings(
    job_id: str,
    text_model: str = "",
    image_model: str = "",
    stamp_model: str = "",
    is_correction_mode: bool = False,
) -> JobSettings:
    """Сохранить настройки задачи в jobs.phase_data (inline, без отдельной таблицы)."""
    client = get_client()
    now = datetime.utcnow().isoformat()

    # Читаем существующий phase_data чтобы не затереть другие поля
    result = client.table("jobs").select("phase_data").eq("id", job_id).execute()
    phase_data = (result.data[0].get("phase_data") or {}) if result.data else {}

    phase_data["settings"] = {
        "text_model": text_model,
        "image_model": image_model,
        "stamp_model": stamp_model,
        "is_correction_mode": is_correction_mode,
    }

    client.table("jobs").update(
        {"phase_data": phase_data, "updated_at": now}
    ).eq("id", job_id).execute()

    return JobSettings(
        job_id=job_id,
        text_model=text_model,
        image_model=image_model,
        stamp_model=stamp_model,
        is_correction_mode=is_correction_mode,
    )


def get_job_settings(job_id: str) -> Optional[JobSettings]:
    """Получить настройки задачи из jobs.phase_data."""
    client = get_client()
    result = client.table("jobs").select("phase_data").eq("id", job_id).execute()

    if not result.data:
        return None

    phase_data = result.data[0].get("phase_data") or {}
    s = phase_data.get("settings")
    if not s:
        return None

    return JobSettings(
        job_id=job_id,
        text_model=s.get("text_model", ""),
        image_model=s.get("image_model", ""),
        stamp_model=s.get("stamp_model", ""),
        is_correction_mode=s.get("is_correction_mode", False),
    )
