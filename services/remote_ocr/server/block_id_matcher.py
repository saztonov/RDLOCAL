"""Re-export shim. Canonical location: rd_core.ocr.block_id_matcher"""
from rd_core.ocr.block_id_matcher import (  # noqa: F401
    ARMOR_BLOCK_MARKER_RE,
    BLOCK_MARKER_RE,
    UUID_LIKE_RE,
    OCR_REPLACEMENTS,
    extract_uuid_candidates,
    normalize_uuid_text,
    match_armor_code,
    match_uuid,
)
