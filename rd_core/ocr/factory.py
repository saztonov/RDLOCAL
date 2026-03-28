"""Фабрика для создания OCR движков"""
import logging

from rd_core.ocr.base import OCRBackend

logger = logging.getLogger(__name__)


def create_ocr_engine(backend: str = "dummy", **kwargs) -> OCRBackend:
    """
    Фабрика для создания OCR движка

    Args:
        backend: тип движка ('chandra', 'qwen' или 'dummy')
        **kwargs: дополнительные параметры для движка

    Returns:
        Экземпляр OCR движка
    """
    if backend == "chandra":
        from rd_core.ocr.chandra import ChandraBackend

        logger.info("Создан OCR бэкенд", extra={
            "event": "ocr_backend_created", "backend": "chandra",
        })
        return ChandraBackend(**kwargs)
    elif backend == "qwen":
        from rd_core.ocr.qwen import QwenBackend

        logger.info("Создан OCR бэкенд", extra={
            "event": "ocr_backend_created", "backend": "qwen",
        })
        return QwenBackend(**kwargs)
    elif backend == "dummy":
        from rd_core.ocr.dummy import DummyOCRBackend

        return DummyOCRBackend()
    else:
        logger.warning(f"Неизвестный backend '{backend}', используется dummy")
        from rd_core.ocr.dummy import DummyOCRBackend

        return DummyOCRBackend()
