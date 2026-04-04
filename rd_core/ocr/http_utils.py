"""Общие HTTP-утилиты для OCR бэкендов (sync)."""
import os
from typing import Optional, Tuple

import httpx


def get_lmstudio_api_key() -> Optional[str]:
    """Прочитать Bearer token для LM Studio из env (LMSTUDIO_API_KEY)."""
    key = os.getenv("LMSTUDIO_API_KEY", "").strip()
    return key or None


def create_http_client(
    api_key: Optional[str] = None,
    total_retries: int = 3,
    timeout: float = 90.0,
    preload_mode: bool = False,
) -> httpx.Client:
    """Создать httpx.Client с retry и connection pooling.

    Args:
        api_key: Bearer token для Authorization header
        total_retries: количество повторов при транспортных ошибках
        timeout: таймаут запроса в секундах
        preload_mode: умеренный timeout для preload-операций
    """
    if preload_mode:
        timeout = 120.0
        total_retries = 2

    transport = httpx.HTTPTransport(
        retries=total_retries,
        http2=False,
    )

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    client = httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(timeout, connect=10.0),
        headers=headers,
        follow_redirects=True,
    )
    return client


# Backward compatibility alias
def create_retry_session(
    api_key: Optional[str] = None,
    total_retries: int = 3,
    backoff_factor: float = 0.5,
    status_forcelist: tuple = (502, 503, 504),
    preload_mode: bool = False,
) -> httpx.Client:
    """Alias для обратной совместимости. Возвращает httpx.Client."""
    return create_http_client(
        api_key=api_key,
        total_retries=total_retries,
        preload_mode=preload_mode,
    )
