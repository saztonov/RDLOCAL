"""
Двухпроходный алгоритм OCR с минимальным потреблением памяти.

PASS 1: Подготовка per-block кропов → сохранение на диск
PASS 2: Последовательный per-block OCR (async)

Компоненты:
- pass1_crops.py - pass1_prepare_crops
- pass2_images.py - run_blocks_phase
- pass2_ocr_async.py - pass2_ocr_from_manifest_async
- cleanup.py - cleanup_manifest_files
"""
from .cleanup import cleanup_manifest_files
from .pass1_crops import pass1_prepare_crops

__all__ = [
    "pass1_prepare_crops",
    "cleanup_manifest_files",
]
