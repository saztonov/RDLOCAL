"""Промпты для OCR воркера — делегирует в rd_core.pipeline.prompts."""

from typing import Dict, Optional

from rd_core.pipeline.prompts import (
    build_text_prompt,  # noqa: F401
    fill_image_prompt_variables as _fill_image_prompt_variables,
    get_image_block_prompt as _get_image_block_prompt,
)

from .logging_config import get_logger

logger = get_logger(__name__)


def _get_category_prompt_fn():
    """Lazy import storage_settings для серверного контекста."""
    try:
        from .storage_settings import get_category_prompt
        return get_category_prompt
    except Exception:
        return None


def get_image_block_prompt(
    block_prompt: Optional[dict],
    category_code: Optional[str] = None,
    engine: Optional[str] = None,
) -> Optional[dict]:
    """Server wrapper: подставляет category_prompt_fn из storage_settings."""
    return _get_image_block_prompt(
        block_prompt,
        category_code=category_code,
        engine=engine,
        category_prompt_fn=_get_category_prompt_fn(),
    )


def fill_image_prompt_variables(
    prompt_data: Optional[dict],
    doc_name: str,
    page_index: int,
    block_id: str,
    category_code: Optional[str] = None,
    engine: Optional[str] = None,
) -> dict:
    """Server wrapper: подставляет category_prompt_fn из storage_settings."""
    return _fill_image_prompt_variables(
        prompt_data,
        doc_name=doc_name,
        page_index=page_index,
        block_id=block_id,
        category_code=category_code,
        engine=engine,
        category_prompt_fn=_get_category_prompt_fn(),
    )
