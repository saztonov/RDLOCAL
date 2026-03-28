"""
Migration helpers for annotation format conversion.

Extracted from annotation_io.py -- works with dict data only, no file I/O.
"""

from rd_core.annotation_io import (
    detect_annotation_version,
    is_flat_format,
    migrate_annotation_data,
    migrate_flat_to_structured,
)

__all__ = [
    "is_flat_format",
    "migrate_flat_to_structured",
    "detect_annotation_version",
    "migrate_annotation_data",
]
