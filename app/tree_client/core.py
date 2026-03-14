"""Базовый HTTP клиент для работы с Supabase."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import httpx
from httpx import Limits

logger = logging.getLogger(__name__)

# Глобальный пул соединений для Supabase
_tree_http_client: httpx.Client | None = None


def _get_tree_client() -> httpx.Client:
    """Получить или создать HTTP клиент с connection pooling"""
    global _tree_http_client
    if _tree_http_client is None:
        _tree_http_client = httpx.Client(
            limits=Limits(max_connections=10, max_keepalive_connections=5),
            timeout=10.0,  # Уменьшен с 30 до 10 сек для отзывчивости UI
        )
    return _tree_http_client


@dataclass
class TreeClientCore:
    """Базовый клиент с HTTP методами"""

    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_key: str = field(default_factory=lambda: os.getenv("SUPABASE_KEY", ""))
    timeout: float = 10.0  # Уменьшен с 30 до 10 сек для отзывчивости UI

    def _headers(self) -> dict:
        return {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self.supabase_url}/rest/v1{path}"
        try:
            client = _get_tree_client()
            resp = getattr(client, method)(
                url, headers=self._headers(), timeout=self.timeout, **kwargs
            )
            resp.raise_for_status()
            return resp
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.TimeoutException, httpx.NetworkError) as e:
            logger.error(f"Сетевая ошибка при запросе к Supabase {method} {path}: {e}")
            raise

    def is_available(self) -> bool:
        """Проверить доступность Supabase"""
        if not self.supabase_url or not self.supabase_key:
            return False
        try:
            self._request("get", "/tree_nodes?select=id&limit=1")
            return True
        except Exception as e:
            logger.debug(f"Supabase недоступен: {e}")
            return False
