"""Единая конфигурация R2 credentials для серверной части."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class R2Config:
    """Конфигурация подключения к Cloudflare R2."""

    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket_name: str


_cached_config: Optional[R2Config] = None


def get_r2_config() -> R2Config:
    """Получить R2 конфигурацию из env. Кешируется после первого вызова.

    Raises:
        ValueError: если обязательные credentials не заданы.
    """
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    account_id = os.getenv("R2_ACCOUNT_ID")
    endpoint_url = os.getenv("R2_ENDPOINT_URL")
    if not endpoint_url and account_id:
        endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

    access_key_id = os.getenv("R2_ACCESS_KEY_ID")
    secret_access_key = os.getenv("R2_SECRET_ACCESS_KEY")
    bucket_name = os.getenv("R2_BUCKET_NAME", "rd1")

    if not all([endpoint_url, access_key_id, secret_access_key]):
        raise ValueError(
            "R2 credentials not configured. "
            "Set R2_ACCOUNT_ID (or R2_ENDPOINT_URL), R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY"
        )

    _cached_config = R2Config(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        bucket_name=bucket_name,
    )
    return _cached_config
