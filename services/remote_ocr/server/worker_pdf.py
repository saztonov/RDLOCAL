"""PDF-утилиты для OCR воркера"""

from typing import Dict

from .logging_config import get_logger

logger = get_logger(__name__)

# Кэш размеров страниц PDF
_page_size_cache: Dict[str, Dict[int, tuple]] = {}


def clear_page_size_cache(pdf_path: str = None):
    """Очистить кэш размеров страниц"""
    global _page_size_cache
    if pdf_path:
        _page_size_cache.pop(pdf_path, None)
    else:
        _page_size_cache.clear()


def get_pdf_page_size(pdf_path: str, page_index: int) -> tuple:
    """Получить размер страницы PDF (с кэшированием)"""
    global _page_size_cache

    if pdf_path in _page_size_cache:
        if page_index in _page_size_cache[pdf_path]:
            return _page_size_cache[pdf_path][page_index]
    else:
        _page_size_cache[pdf_path] = {}

    try:
        from rd_core.pdf_utils import get_pdf_page_size as rd_get_page_size

        size = rd_get_page_size(pdf_path, page_index)
        if size:
            _page_size_cache[pdf_path][page_index] = size
            return size
    except Exception as e:
        logger.warning(f"Ошибка получения размера страницы {page_index}: {e}")

    return (595.0, 842.0)  # A4 по умолчанию


