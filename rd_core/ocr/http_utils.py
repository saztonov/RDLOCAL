"""Общие HTTP-утилиты для OCR бэкендов (sync и async)."""
from typing import Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def create_retry_session(
    auth: Optional[Tuple[str, str]] = None,
    total_retries: int = 3,
    backoff_factor: float = 0.5,
    status_forcelist: tuple = (502, 503, 504),
    preload_mode: bool = False,
) -> requests.Session:
    """Создать requests.Session с retry и connection pooling.

    Args:
        preload_mode: умеренный retry для preload-операций
                      (2 попытки, backoff ~3с, без 404)
    """
    if preload_mode:
        total_retries = 2
        backoff_factor = 1.0
        status_forcelist = (502, 503, 504)

    session = requests.Session()
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    if auth:
        session.auth = auth
    return session
