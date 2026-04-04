"""
Двухпроходный алгоритм OCR — shim, делегирует в rd_core.pipeline.

Компоненты:
- pass1_crops.py - pass1_prepare_crops
- pass2_images.py - run_blocks_phase
- pass2_ocr_async.py - pass2_ocr_from_manifest_async
- cleanup.py - cleanup_manifest_files
"""
from rd_core.pipeline.cleanup import cleanup_manifest_files  # noqa: F401
from .pass1_crops import pass1_prepare_crops  # noqa: F401

__all__ = [
    "pass1_prepare_crops",
    "cleanup_manifest_files",
]
