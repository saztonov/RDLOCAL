"""OCR модуль с поддержкой различных backends"""

from rd_core.ocr.base import OCRBackend
from rd_core.ocr.chandra import ChandraBackend
from rd_core.ocr.datalab import DatalabOCRBackend
from rd_core.ocr.dummy import DummyOCRBackend
from rd_core.ocr.factory import create_ocr_engine
from rd_core.ocr.html_generator import generate_html_from_pages
from rd_core.ocr.md import generate_md_from_pages, generate_md_from_result
from rd_core.ocr.openrouter import OpenRouterBackend
from rd_core.ocr.utils import image_to_base64, image_to_pdf_base64

__all__ = [
    # Backends
    "OCRBackend",
    "OpenRouterBackend",
    "DatalabOCRBackend",
    "ChandraBackend",
    "DummyOCRBackend",
    # Utils
    "image_to_base64",
    "image_to_pdf_base64",
    "generate_html_from_pages",
    "generate_md_from_pages",
    "generate_md_from_result",
    # Factory
    "create_ocr_engine",
]
