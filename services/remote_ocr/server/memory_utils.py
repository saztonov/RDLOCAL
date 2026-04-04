"""Shim — делегирует в rd_core.pipeline.memory_utils."""
from rd_core.pipeline.memory_utils import (  # noqa: F401
    force_gc,
    get_memory_details,
    get_memory_mb,
    get_object_size_mb,
    get_pil_image_size_mb,
    log_memory,
    log_memory_delta,
    log_pil_images_summary,
)
