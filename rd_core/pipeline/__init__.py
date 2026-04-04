"""
Two-pass OCR pipeline — shared between desktop and server.

PASS 1: per-block crop extraction -> save to disk
PASS 2: per-block async OCR recognition

Public API:
- pass1_prepare_crops
- pass2_ocr_from_manifest_async
- cleanup_manifest_files
- copy_crops_to_final
"""
from .cleanup import cleanup_manifest_files, copy_crops_to_final
from .pass1_crops import pass1_prepare_crops

__all__ = [
    "pass1_prepare_crops",
    "cleanup_manifest_files",
    "copy_crops_to_final",
]
