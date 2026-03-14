"""OCR модуль с поддержкой различных backends"""

from rd_core.ocr.async_base import AsyncOCRBackend
from rd_core.ocr.base import OCRBackend
from rd_core.ocr.chandra import ChandraBackend
from rd_core.ocr.chandra_async import AsyncChandraBackend
from rd_core.ocr.datalab import DatalabOCRBackend
from rd_core.ocr.datalab_async import AsyncDatalabOCRBackend
from rd_core.ocr.dummy import DummyOCRBackend
from rd_core.ocr.dummy_async import AsyncDummyOCRBackend
from rd_core.ocr.factory import create_async_ocr_engine, create_ocr_engine
from rd_core.ocr.html_generator import generate_html_from_pages
from rd_core.ocr.md import generate_md_from_pages, generate_md_from_result
from rd_core.ocr.openrouter import OpenRouterBackend
from rd_core.ocr.openrouter_async import AsyncOpenRouterBackend
from rd_core.ocr.utils import image_to_base64, image_to_pdf_base64

__all__ = [
    # Sync backends
    "OCRBackend",
    "OpenRouterBackend",
    "DatalabOCRBackend",
    "ChandraBackend",
    "DummyOCRBackend",
    # Async backends
    "AsyncOCRBackend",
    "AsyncOpenRouterBackend",
    "AsyncDatalabOCRBackend",
    "AsyncChandraBackend",
    "AsyncDummyOCRBackend",
    # Utils
    "image_to_base64",
    "image_to_pdf_base64",
    "generate_html_from_pages",
    "generate_md_from_pages",
    "generate_md_from_result",
    # Factories
    "create_ocr_engine",
    "create_async_ocr_engine",
]
