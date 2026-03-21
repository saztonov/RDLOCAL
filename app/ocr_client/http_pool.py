"""HTTP connection pooling для Remote OCR клиента"""
from __future__ import annotations

import httpx
from httpx import Limits

# Глобальный пул соединений для Remote OCR
_remote_ocr_http_client: httpx.Client | None = None
_remote_ocr_base_url: str | None = None


def get_remote_ocr_client(base_url: str, timeout: float = 120.0) -> httpx.Client:
    """Получить или создать HTTP клиент с connection pooling"""
    global _remote_ocr_http_client, _remote_ocr_base_url
    if _remote_ocr_http_client is None or _remote_ocr_base_url != base_url:
        if _remote_ocr_http_client is not None:
            try:
                _remote_ocr_http_client.close()
            except Exception:
                pass
        _remote_ocr_http_client = httpx.Client(
            base_url=base_url,
            limits=Limits(max_connections=10, max_keepalive_connections=5),
            timeout=httpx.Timeout(connect=5.0, read=timeout, write=timeout, pool=5.0),
        )
        _remote_ocr_base_url = base_url
    return _remote_ocr_http_client
