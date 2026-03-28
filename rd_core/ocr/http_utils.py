"""Общие HTTP-утилиты для OCR бэкендов (sync)."""
from typing import Optional, Tuple

import httpx


def create_http_client(
    auth: Optional[Tuple[str, str]] = None,
    total_retries: int = 3,
    timeout: float = 90.0,
    preload_mode: bool = False,
) -> httpx.Client:
    """Создать httpx.Client с retry и connection pooling.

    Args:
        auth: (username, password) basic auth
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

    client = httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(timeout, connect=10.0),
        auth=auth,
        follow_redirects=True,
    )
    return client


# Backward compatibility alias
def create_retry_session(
    auth: Optional[Tuple[str, str]] = None,
    total_retries: int = 3,
    backoff_factor: float = 0.5,
    status_forcelist: tuple = (502, 503, 504),
    preload_mode: bool = False,
) -> httpx.Client:
    """Alias для обратной совместимости. Возвращает httpx.Client."""
    return create_http_client(
        auth=auth,
        total_retries=total_retries,
        preload_mode=preload_mode,
    )
