"""PASS 1 — shim, делегирует в rd_core.pipeline.pass1_crops.

Подставляет crop_png_compress из серверного settings.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from rd_core.pipeline.manifest_models import TwoPassManifest
from rd_core.pipeline.pass1_crops import pass1_prepare_crops as _pass1_prepare_crops

from ..settings import settings


def pass1_prepare_crops(
    pdf_path: str,
    blocks: List,
    crops_dir: str,
    padding: int = 5,
    save_image_crops_as_pdf: bool = True,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> TwoPassManifest:
    """Server wrapper: подставляет crop_png_compress из settings."""
    return _pass1_prepare_crops(
        pdf_path,
        blocks,
        crops_dir,
        padding=padding,
        save_image_crops_as_pdf=save_image_crops_as_pdf,
        on_progress=on_progress,
        should_stop=should_stop,
        crop_png_compress=settings.crop_png_compress,
    )
