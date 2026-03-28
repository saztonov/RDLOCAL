"""Операции с настройками задач OCR"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from .logging_config import get_logger
from .storage_client import get_client
from .storage_models import JobSettings

logger = get_logger(__name__)


def get_category_prompt(
    category_id: Optional[str] = None,
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
    """Сохранить/обновить настройки задачи"""
    now = datetime.utcnow().isoformat()
    client = get_client()

    # Upsert: вставить или обновить
    client.table("job_settings").upsert(
        {
            "job_id": job_id,
            "text_model": text_model,
            "image_model": image_model,
            "stamp_model": stamp_model,
            "is_correction_mode": is_correction_mode,
            "updated_at": now,
        },
        on_conflict="job_id",
    ).execute()

    return JobSettings(
        job_id=job_id,
        text_model=text_model,
        image_model=image_model,
        stamp_model=stamp_model,
        is_correction_mode=is_correction_mode,
    )


def get_job_settings(job_id: str) -> Optional[JobSettings]:
    """Получить настройки задачи"""
    client = get_client()
    result = client.table("job_settings").select("*").eq("job_id", job_id).execute()

    if not result.data:
        return None

    row = result.data[0]
    return JobSettings(
        job_id=row["job_id"],
        text_model=row.get("text_model", ""),
        image_model=row.get("image_model", ""),
        stamp_model=row.get("stamp_model", ""),
        is_correction_mode=row.get("is_correction_mode", False),
    )
