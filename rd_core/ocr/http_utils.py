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
    ngrok_mode: bool = False,
) -> requests.Session:
    """Создать requests.Session с retry и connection pooling.

    Args:
        ngrok_mode: расширенный retry для нестабильного ngrok tunnel
                    (6 попыток, backoff до ~2 мин, включая 404)
    """
    if ngrok_mode:
        total_retries = 6
        backoff_factor = 2.0
        status_forcelist = (404, 429, 500, 502, 503, 504)

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
    # Обход ngrok free tier browser interstitial
    session.headers.update({"ngrok-skip-browser-warning": "true"})
    if auth:
        session.auth = auth
    return session


def create_async_client(
    timeout: float = 120.0,
    connect_timeout: float = 30.0,
    auth: Optional[Tuple[str, str]] = None,
    max_connections: int = 10,
    max_keepalive: int = 5,
    ngrok_mode: bool = False,
):
    """Создать httpx.AsyncClient с retry и connection pooling.

    Args:
        ngrok_mode: расширенный retry для нестабильного ngrok tunnel
    """
    import httpx

    retries = 5 if ngrok_mode else 3

    transport = httpx.AsyncHTTPTransport(
        retries=retries,
        limits=httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive,
            keepalive_expiry=30.0,
        ),
    )
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(timeout, connect=connect_timeout),
        auth=auth,
        # Обход ngrok free tier browser interstitial
        headers={"ngrok-skip-browser-warning": "true"},
    )
