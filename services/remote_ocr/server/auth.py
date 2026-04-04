"""Авторизация для web API — Bearer token."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .logging_config import get_logger

logger = get_logger(__name__)

_security = HTTPBearer(auto_error=False)

# API ключ загружается из .env
_API_KEY: Optional[str] = None


def _get_api_key() -> Optional[str]:
    global _API_KEY
    if _API_KEY is None:
        _API_KEY = os.environ.get("WEB_API_KEY", os.environ.get("AUTH_SECRET", ""))
    return _API_KEY or None


def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> str:
    """Dependency для защищённых эндпоинтов.

    Проверяет Bearer token из заголовка Authorization.
    Если WEB_API_KEY не задан в .env — авторизация отключена (dev режим).

    Returns:
        Идентификатор клиента (token или "anonymous").
    """
    api_key = _get_api_key()

    # Если ключ не настроен — пропускаем (dev mode)
    if not api_key:
        return "anonymous"

    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials.credentials != api_key:
        logger.warning(
            "Invalid API key attempt",
            extra={"event": "auth_failed"},
        )
        raise HTTPException(
            status_code=403,
            detail="Invalid API key",
        )

    return "authenticated"
