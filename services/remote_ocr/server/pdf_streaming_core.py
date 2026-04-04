"""
Streaming обработка PDF — shim, делегирует в rd_core.pipeline.pdf_streaming_core.

Сохраняет обратную совместимость для серверного кода, подставляя DPI из settings.
"""
from __future__ import annotations

from typing import Dict, Tuple

from rd_core.pipeline.pdf_streaming_core import (  # noqa: F401
    DEFAULT_PDF_RENDER_DPI,
    MAX_IMAGE_PIXELS,
    StreamingPDFProcessor,
    get_page_dimensions_streaming,
)

from .settings import settings

# Константы из настроек (для обратной совместимости серверного кода)
PDF_RENDER_DPI = settings.pdf_render_dpi
PDF_RENDER_ZOOM = PDF_RENDER_DPI / 72.0
