"""Общие утилиты для routes"""
from typing import Optional

from fastapi import Header, HTTPException

from services.remote_ocr.server.settings import settings


def check_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")) -> None:
    """Проверить API ключ если он задан в настройках"""
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")



def get_r2_storage():
    """Получить R2 Storage клиент (async-обёртка)"""
    from services.remote_ocr.server.task_helpers import (
        get_r2_storage as _get_r2_storage,
    )

    return _get_r2_storage()


def get_r2_sync_client():
    """Получить синхронный boto3 клиент для прямых операций (put_object и т.д.)"""
    import boto3
    from botocore.config import Config

    from services.remote_ocr.server.r2_config import get_r2_config

    cfg = get_r2_config()
    client = boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        region_name="auto",
        config=Config(retries={"max_attempts": 3}),
    )
    return client, cfg.bucket_name


def get_file_icon(file_type: str) -> str:
    """Получить иконку для типа файла"""
    icons = {
        "pdf": "📄",
        "blocks": "📋",
        "annotation": "📋",
        "result_md": "📝",
        "result_zip": "📦",
        "crop": "🖼️",
    }
    return icons.get(file_type, "📄")
