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
    model_name = kwargs.get("model_name", "")
    mode = kwargs.get("mode", "")

    if backend == "openrouter":
        from rd_core.ocr.openrouter import OpenRouterBackend

        logger.info("Создан OCR бэкенд", extra={
            "event": "ocr_backend_created", "backend": "openrouter", "model_name": model_name,
        })
        return OpenRouterBackend(**kwargs)
    elif backend == "datalab":
        from rd_core.ocr.datalab import DatalabOCRBackend

        logger.info("Создан OCR бэкенд", extra={
            "event": "ocr_backend_created", "backend": "datalab",
        })
        return DatalabOCRBackend(**kwargs)
    elif backend == "chandra":
        from rd_core.ocr.chandra import ChandraBackend

        logger.info("Создан OCR бэкенд", extra={
            "event": "ocr_backend_created", "backend": "chandra",
        })
        return ChandraBackend(**kwargs)
    elif backend == "qwen":
        from rd_core.ocr.qwen import QwenBackend

        logger.info("Создан OCR бэкенд", extra={
            "event": "ocr_backend_created", "backend": "qwen", "model_name": f"qwen-{mode}" if mode else "qwen",
        })
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
    model_name = kwargs.get("model_name", "")
    mode = kwargs.get("mode", "")

    if backend == "openrouter":
        from rd_core.ocr.openrouter_async import AsyncOpenRouterBackend

        logger.info("Создан async OCR бэкенд", extra={
            "event": "ocr_backend_created", "backend": "openrouter", "model_name": model_name,
        })
        return AsyncOpenRouterBackend(**kwargs)
    elif backend == "datalab":
        from rd_core.ocr.datalab_async import AsyncDatalabOCRBackend

        logger.info("Создан async OCR бэкенд", extra={
            "event": "ocr_backend_created", "backend": "datalab",
        })
        return AsyncDatalabOCRBackend(**kwargs)
    elif backend == "chandra":
        from rd_core.ocr.chandra_async import AsyncChandraBackend

        logger.info("Создан async OCR бэкенд", extra={
            "event": "ocr_backend_created", "backend": "chandra",
        })
        return AsyncChandraBackend(**kwargs)
    elif backend == "qwen":
        from rd_core.ocr.qwen_async import AsyncQwenBackend

        logger.info("Создан async OCR бэкенд", extra={
            "event": "ocr_backend_created", "backend": "qwen", "model_name": f"qwen-{mode}" if mode else "qwen",
        })
        return AsyncQwenBackend(**kwargs)
    elif backend == "dummy":
        from rd_core.ocr.dummy_async import AsyncDummyOCRBackend

        return AsyncDummyOCRBackend()
    else:
        logger.warning(f"Неизвестный async backend '{backend}', используется dummy")
        from rd_core.ocr.dummy_async import AsyncDummyOCRBackend

        return AsyncDummyOCRBackend()
