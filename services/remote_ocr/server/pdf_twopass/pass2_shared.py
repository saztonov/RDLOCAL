"""Shim — делегирует в rd_core.pipeline.pass2_shared.

Подставляет серверный rate_limiter.
"""
from rd_core.pipeline.pass2_shared import (  # noqa: F401
    CANCELLED_SENTINEL,
    CHECKPOINT_SAVE_INTERVAL,
    DEADLINE_RESERVE,
    AsyncRateLimiter,
    NoOpRateLimiter,
    Pass2Context,
    cancellable_recognize,
    drain_queue,
    should_retry_ocr,
)
