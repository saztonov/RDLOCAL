"""PASS 2 images — shim, делегирует в rd_core.pipeline.pass2_images.

Подставляет серверный category_prompt_fn из storage_settings.
"""
from __future__ import annotations

import asyncio
from typing import List, Optional

from rd_core.pipeline.manifest_models import CropManifestEntry
from rd_core.pipeline.pass2_images import run_blocks_phase as _run_blocks_phase
from rd_core.pipeline.pass2_shared import Pass2Context


def _get_category_prompt_fn():
    """Lazy import серверного category prompt lookup."""
    try:
        from ..storage_settings import get_category_prompt
        return get_category_prompt
    except Exception:
        return None


async def run_blocks_phase(
    block_entries: List[CropManifestEntry],
    blocks: List,
    text_backend,
    image_backend,
    stamp_backend,
    ctx: Pass2Context,
    max_workers: int = 1,
) -> None:
    """Server wrapper: подставляет category_prompt_fn."""
    await _run_blocks_phase(
        block_entries, blocks, text_backend, image_backend, stamp_backend, ctx,
        max_workers=max_workers,
        category_prompt_fn=_get_category_prompt_fn(),
    )
