"""PASS 2 async — shim, делегирует в rd_core.pipeline.pass2_ocr_async.

Подставляет PhaseConcurrency из settings и серверный rate_limiter.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, List, Optional

from rd_core.pipeline.checkpoint_models import OCRCheckpoint
from rd_core.pipeline.manifest_models import TwoPassManifest
from rd_core.pipeline.pass2_ocr_async import (
    PhaseConcurrency,
    pass2_ocr_from_manifest_async as _pass2_async,
    pass2_ocr_from_manifest_sync_wrapper as _pass2_sync,
)

from ..rate_limiter import get_unified_async_limiter
from ..settings import settings


def _get_category_prompt_fn():
    try:
        from ..storage_settings import get_category_prompt
        return get_category_prompt
    except Exception:
        return None


def _make_phase_concurrency() -> PhaseConcurrency:
    return PhaseConcurrency(
        text_max_concurrent=min(settings.text_max_concurrent, settings.chandra_max_concurrent),
        stamp_max_concurrent=settings.stamp_max_concurrent,
        image_max_concurrent=settings.image_max_concurrent,
    )


async def pass2_ocr_from_manifest_async(
    manifest: TwoPassManifest,
    blocks: List,
    text_backend,
    image_backend,
    stamp_backend,
    pdf_path: str,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    check_paused: Optional[Callable[[], bool]] = None,
    max_concurrent: Optional[int] = None,
    checkpoint: Optional[OCRCheckpoint] = None,
    work_dir: Optional[Path] = None,
    deadline: Optional[float] = None,
    before_image_phase: Optional[Callable[[], None]] = None,
    before_stamp_phase: Optional[Callable[[], None]] = None,
) -> None:
    """Server wrapper: подставляет PhaseConcurrency, rate_limiter, category_prompt_fn."""
    await _pass2_async(
        manifest=manifest,
        blocks=blocks,
        text_backend=text_backend,
        image_backend=image_backend,
        stamp_backend=stamp_backend,
        pdf_path=pdf_path,
        on_progress=on_progress,
        check_paused=check_paused,
        max_concurrent=max_concurrent,
        checkpoint=checkpoint,
        work_dir=work_dir,
        deadline=deadline,
        before_image_phase=before_image_phase,
        before_stamp_phase=before_stamp_phase,
        phase_concurrency=_make_phase_concurrency(),
        rate_limiter=get_unified_async_limiter(),
        category_prompt_fn=_get_category_prompt_fn(),
    )


def pass2_ocr_from_manifest_sync_wrapper(
    manifest: TwoPassManifest,
    blocks: List,
    text_backend,
    image_backend,
    stamp_backend,
    pdf_path: str,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    check_paused: Optional[Callable[[], bool]] = None,
    checkpoint: Optional[OCRCheckpoint] = None,
    work_dir: Optional[Path] = None,
    before_image_phase: Optional[Callable[[], None]] = None,
    before_stamp_phase: Optional[Callable[[], None]] = None,
) -> None:
    """Server wrapper sync version."""
    _pass2_sync(
        manifest=manifest,
        blocks=blocks,
        text_backend=text_backend,
        image_backend=image_backend,
        stamp_backend=stamp_backend,
        pdf_path=pdf_path,
        on_progress=on_progress,
        check_paused=check_paused,
        checkpoint=checkpoint,
        work_dir=work_dir,
        before_image_phase=before_image_phase,
        before_stamp_phase=before_stamp_phase,
        phase_concurrency=_make_phase_concurrency(),
        rate_limiter=get_unified_async_limiter(),
        category_prompt_fn=_get_category_prompt_fn(),
    )
