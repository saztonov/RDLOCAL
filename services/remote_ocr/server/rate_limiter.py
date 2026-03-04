"""Глобальный rate limiter для Datalab API и OpenRouter"""
from __future__ import annotations

import threading
import time

from .logging_config import get_logger

logger = get_logger(__name__)

# Глобальный семафор для ограничения ВСЕХ параллельных OCR запросов
_global_ocr_semaphore: threading.Semaphore | None = None
_global_ocr_lock = threading.Lock()


def get_global_ocr_semaphore(max_concurrent: int = 8) -> threading.Semaphore:
    """Глобальный семафор для всех OCR запросов (OpenRouter + Datalab)"""
    global _global_ocr_semaphore
    if _global_ocr_semaphore is None:
        with _global_ocr_lock:
            if _global_ocr_semaphore is None:
                _global_ocr_semaphore = threading.Semaphore(max_concurrent)
                logger.info(
                    f"Global OCR semaphore: {max_concurrent} concurrent requests"
                )
    return _global_ocr_semaphore


class DatalabRateLimiter:
    """
    Token Bucket rate limiter с ограничением параллельных запросов.

    Контролирует:
    - Максимум запросов в минуту (token bucket)
    - Максимум параллельных запросов (semaphore)
    """

    def __init__(self, max_requests_per_minute: int = 180, max_concurrent: int = 5):
        """
        Args:
            max_requests_per_minute: лимит запросов в минуту (с запасом от 200)
            max_concurrent: максимум параллельных запросов
        """
        self.max_requests = max_requests_per_minute
        self.period = 60.0  # секунд
        self.max_concurrent = max_concurrent

        # Token bucket
        self.tokens = float(max_requests_per_minute)
        self.last_refill = time.time()
        self._lock = threading.Lock()

        # Semaphore для ограничения параллельных запросов
        self._semaphore = threading.Semaphore(max_concurrent)

        # Статистика
        self._total_requests = 0
        self._total_waits = 0

        logger.info(
            f"DatalabRateLimiter: {max_requests_per_minute} req/min, "
            f"{max_concurrent} concurrent"
        )

    def _refill_tokens(self) -> None:
        """Пополнить токены на основе прошедшего времени"""
        now = time.time()
        elapsed = now - self.last_refill

        # Добавляем токены пропорционально прошедшему времени
        tokens_to_add = (elapsed / self.period) * self.max_requests
        self.tokens = min(self.max_requests, self.tokens + tokens_to_add)
        self.last_refill = now

    def acquire(self, timeout: float = 300.0) -> bool:
        """
        Получить разрешение на запрос к API.

        Args:
            timeout: максимальное время ожидания в секундах

        Returns:
            True если разрешение получено, False если таймаут
        """
        start_time = time.time()

        # Сначала ждём слот в semaphore (ограничение параллельных)
        if not self._semaphore.acquire(timeout=timeout):
            logger.warning("RateLimiter: таймаут ожидания semaphore")
            return False

        # Теперь ждём токен (ограничение по частоте)
        while True:
            with self._lock:
                self._refill_tokens()

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    self._total_requests += 1
                    return True

            # Проверяем таймаут
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                # Освобождаем semaphore если не смогли получить токен
                self._semaphore.release()
                logger.warning("RateLimiter: таймаут ожидания токена")
                return False

            # Ждём немного перед повторной проверкой
            wait_time = min(1.0, timeout - elapsed)
            self._total_waits += 1
            logger.debug(f"RateLimiter: ожидание токена ({self.tokens:.1f} tokens)")
            time.sleep(wait_time)

    def release(self) -> None:
        """Освободить слот после завершения запроса"""
        self._semaphore.release()

    def get_stats(self) -> dict:
        """Получить статистику использования"""
        with self._lock:
            return {
                "total_requests": self._total_requests,
                "total_waits": self._total_waits,
                "current_tokens": self.tokens,
                "max_requests_per_minute": self.max_requests,
                "max_concurrent": self.max_concurrent,
            }


# Глобальный экземпляр (инициализируется при импорте settings)
_global_limiter: DatalabRateLimiter | None = None
_limiter_lock = threading.Lock()


def get_datalab_limiter() -> DatalabRateLimiter:
    """Получить глобальный rate limiter (lazy initialization)"""
    global _global_limiter

    if _global_limiter is None:
        with _limiter_lock:
            if _global_limiter is None:
                from .settings import settings

                _global_limiter = DatalabRateLimiter(
                    max_requests_per_minute=settings.datalab_max_rpm,
                    max_concurrent=settings.datalab_max_concurrent,
                )

    return _global_limiter


# =============================================================================
# ASYNC RATE LIMITER
# =============================================================================

import asyncio


class AsyncTokenBucket:
    """Асинхронный Token Bucket для контроля частоты запросов"""

    def __init__(self, max_tokens: int, refill_period: float = 60.0):
        """
        Args:
            max_tokens: максимум токенов (запросов в период)
            refill_period: период пополнения в секундах
        """
        self.max_tokens = max_tokens
        self.refill_period = refill_period
        self.tokens = float(max_tokens)
        self.last_refill = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self, timeout: float = 300.0) -> bool:
        """
        Асинхронно получить токен.

        Args:
            timeout: максимальное время ожидания

        Returns:
            True если токен получен, False если таймаут
        """
        start_time = time.time()

        while True:
            async with self._lock:
                self._refill_tokens()

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True

            # Проверяем таймаут
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                logger.warning("AsyncTokenBucket: таймаут ожидания токена")
                return False

            # Ждём асинхронно
            wait_time = min(0.5, timeout - elapsed)
            await asyncio.sleep(wait_time)

    def _refill_tokens(self) -> None:
        """Пополнить токены на основе прошедшего времени"""
        now = time.time()
        elapsed = now - self.last_refill
        tokens_to_add = (elapsed / self.refill_period) * self.max_tokens
        self.tokens = min(self.max_tokens, self.tokens + tokens_to_add)
        self.last_refill = now


class UnifiedAsyncRateLimiter:
    """
    Унифицированный асинхронный rate limiter.

    Объединяет:
    - Semaphore для ограничения параллельных запросов
    - Token Bucket для контроля частоты запросов

    Убирает проблему двойного семафора (global + per-backend).
    """

    def __init__(
        self,
        max_concurrent: int = 8,
        max_requests_per_minute: int = 180,
        name: str = "default",
    ):
        """
        Args:
            max_concurrent: максимум параллельных запросов
            max_requests_per_minute: максимум запросов в минуту
            name: имя для логирования
        """
        self.name = name
        self.max_concurrent = max_concurrent
        self.max_rpm = max_requests_per_minute

        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._token_bucket = AsyncTokenBucket(max_requests_per_minute)

        # Статистика
        self._total_requests = 0
        self._total_waits = 0
        self._active_requests = 0
        self._stats_lock = asyncio.Lock()

        logger.info(
            f"UnifiedAsyncRateLimiter '{name}': {max_concurrent} concurrent, "
            f"{max_requests_per_minute} req/min"
        )

    async def acquire_async(self, timeout: float = 300.0) -> bool:
        """
        Асинхронно получить разрешение на запрос.

        Args:
            timeout: максимальное время ожидания

        Returns:
            True если разрешение получено, False если таймаут
        """
        start_time = time.time()

        # 1. Ждём слот в semaphore
        try:
            acquired = await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"RateLimiter '{self.name}': таймаут ожидания semaphore")
            return False

        if not acquired:
            return False

        # 2. Ждём токен
        remaining_timeout = timeout - (time.time() - start_time)
        if remaining_timeout <= 0:
            self._semaphore.release()
            return False

        token_acquired = await self._token_bucket.acquire(timeout=remaining_timeout)
        if not token_acquired:
            self._semaphore.release()
            logger.warning(f"RateLimiter '{self.name}': таймаут ожидания токена")
            return False

        # Статистика
        async with self._stats_lock:
            self._total_requests += 1
            self._active_requests += 1

        return True

    async def release_async(self) -> None:
        """Асинхронно освободить слот"""
        self._semaphore.release()

        async with self._stats_lock:
            self._active_requests = max(0, self._active_requests - 1)

    # Sync-compatible методы для обратной совместимости
    def acquire(self, timeout: float = 300.0) -> bool:
        """Синхронный acquire (для обратной совместимости)"""
        try:
            loop = asyncio.get_running_loop()
            # Если уже есть event loop, используем его
            return asyncio.run_coroutine_threadsafe(
                self.acquire_async(timeout), loop
            ).result(timeout=timeout)
        except RuntimeError:
            # Нет running loop, создаём новый
            return asyncio.run(self.acquire_async(timeout))

    def release(self) -> None:
        """Синхронный release (для обратной совместимости)"""
        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(self.release_async(), loop)
        except RuntimeError:
            asyncio.run(self.release_async())

    async def get_stats_async(self) -> dict:
        """Получить статистику асинхронно"""
        async with self._stats_lock:
            return {
                "name": self.name,
                "total_requests": self._total_requests,
                "active_requests": self._active_requests,
                "current_tokens": self._token_bucket.tokens,
                "max_concurrent": self.max_concurrent,
                "max_rpm": self.max_rpm,
            }


# Глобальный async rate limiter
_global_async_limiter: UnifiedAsyncRateLimiter | None = None
_async_limiter_lock = threading.Lock()


def get_unified_async_limiter() -> UnifiedAsyncRateLimiter:
    """Получить глобальный async rate limiter"""
    global _global_async_limiter

    if _global_async_limiter is None:
        with _async_limiter_lock:
            if _global_async_limiter is None:
                from .settings import settings

                _global_async_limiter = UnifiedAsyncRateLimiter(
                    max_concurrent=settings.max_global_ocr_requests,
                    max_requests_per_minute=settings.datalab_max_rpm,
                    name="global_ocr",
                )

    return _global_async_limiter


def reset_async_limiter():
    """Сбросить глобальный async rate limiter.

    Вызывать перед каждым asyncio.run() в Celery worker,
    т.к. asyncio.Semaphore/Lock привязаны к конкретному event loop.
    """
    global _global_async_limiter
    with _async_limiter_lock:
        _global_async_limiter = None
