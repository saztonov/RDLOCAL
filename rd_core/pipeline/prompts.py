"""
Промпты для OCR — чистая логика без серверных зависимостей.

category_prompt_fn — injectable callback для получения промптов по категории.
Server передаёт свой storage_settings.get_category_prompt,
desktop передаёт None (промпты из block.prompt).
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Type alias для category prompt lookup
CategoryPromptFn = Optional[Callable[[Optional[str], Optional[str]], Optional[dict]]]


def get_image_block_prompt(
    block_prompt: Optional[dict],
    category_code: Optional[str] = None,
    engine: Optional[str] = None,
    category_prompt_fn: CategoryPromptFn = None,
) -> Optional[dict]:
    """
    Получить промпт для IMAGE/STAMP блока.
    Приоритет: block.prompt > category prompt (через callback)
    """
    if block_prompt and (block_prompt.get("system") or block_prompt.get("user")):
        return block_prompt

    if category_prompt_fn is not None:
        try:
            category_prompt = category_prompt_fn(category_code, engine)
            if category_prompt:
                return category_prompt
        except Exception as e:
            logger.warning(f"Не удалось получить промпт категории: {e}")

    return None


def fill_image_prompt_variables(
    prompt_data: Optional[dict],
    doc_name: str,
    page_index: int,
    block_id: str,
    category_code: Optional[str] = None,
    engine: Optional[str] = None,
    category_prompt_fn: CategoryPromptFn = None,
) -> dict:
    """
    Заполнить переменные в промпте для IMAGE/STAMP блока.

    Переменные:
        {DOC_NAME} - имя PDF документа
        {PAGE_NUM} - номер страницы (1-based)
        {BLOCK_ID} - ID блока
    """
    effective_prompt = get_image_block_prompt(
        prompt_data, category_code=category_code, engine=engine,
        category_prompt_fn=category_prompt_fn,
    )

    if not effective_prompt:
        return {
            "system": "",
            "user": "Опиши что изображено на картинке. Верни результат как JSON.",
        }

    result = {
        "system": effective_prompt.get("system", ""),
        "user": effective_prompt.get("user", ""),
    }

    variables = {
        "{DOC_NAME}": doc_name or "unknown",
        "{PAGE_NUM}": str(page_index + 1) if page_index is not None else "1",
        "{BLOCK_ID}": block_id or "",
    }

    for key, value in variables.items():
        result["system"] = result["system"].replace(key, value)
        result["user"] = result["user"].replace(key, value)

    return result


def build_text_prompt(block) -> dict:
    """Построить промпт для одного TEXT блока."""
    if block.prompt:
        return block.prompt
    return {
        "system": "You are an expert OCR system. Extract text accurately.",
        "user": "Распознай текст на изображении. Сохрани форматирование.",
    }
