"""Фабрика для создания OCR движков"""
import logging

from rd_core.ocr.async_base import AsyncOCRBackend
from rd_core.ocr.base import OCRBackend

logger = logging.getLogger(__name__)


def create_ocr_engine(backend: str = "dummy", **kwargs) -> OCRBackend:
    """
    Фабрика для создания OCR движка

    Args:
        backend: тип движка ('openrouter', 'datalab' или 'dummy')
        **kwargs: дополнительные параметры для движка

    Returns:
        Экземпляр OCR движка
    """
    if backend == "openrouter":
        from rd_core.ocr.openrouter import OpenRouterBackend

        return OpenRouterBackend(**kwargs)
    elif backend == "datalab":
        from rd_core.ocr.datalab import DatalabOCRBackend

        return DatalabOCRBackend(**kwargs)
    elif backend == "chandra":
        from rd_core.ocr.chandra import ChandraBackend

        return ChandraBackend(**kwargs)
    elif backend == "qwen":
        from rd_core.ocr.qwen import QwenBackend

        return QwenBackend(**kwargs)
    elif backend == "dummy":
        from rd_core.ocr.dummy import DummyOCRBackend

        return DummyOCRBackend()
    else:
        logger.warning(f"Неизвестный backend '{backend}', используется dummy")
        from rd_core.ocr.dummy import DummyOCRBackend

        return DummyOCRBackend()


def create_async_ocr_engine(backend: str = "dummy", **kwargs) -> AsyncOCRBackend:
    """
    Фабрика для создания асинхронного OCR движка

    Args:
        backend: тип движка ('openrouter', 'datalab' или 'dummy')
        **kwargs: дополнительные параметры для движка

    Returns:
        Экземпляр асинхронного OCR движка
    """
    if backend == "openrouter":
        from rd_core.ocr.openrouter_async import AsyncOpenRouterBackend

        return AsyncOpenRouterBackend(**kwargs)
    elif backend == "datalab":
        from rd_core.ocr.datalab_async import AsyncDatalabOCRBackend

        return AsyncDatalabOCRBackend(**kwargs)
    elif backend == "chandra":
        from rd_core.ocr.chandra_async import AsyncChandraBackend

        return AsyncChandraBackend(**kwargs)
    elif backend == "qwen":
        from rd_core.ocr.qwen_async import AsyncQwenBackend

        return AsyncQwenBackend(**kwargs)
    elif backend == "dummy":
        from rd_core.ocr.dummy_async import AsyncDummyOCRBackend

        return AsyncDummyOCRBackend()
    else:
        logger.warning(f"Неизвестный async backend '{backend}', используется dummy")
        from rd_core.ocr.dummy_async import AsyncDummyOCRBackend

        return AsyncDummyOCRBackend()
