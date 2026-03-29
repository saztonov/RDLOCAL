"""OCR модуль с поддержкой LM Studio backends"""

from rd_core.ocr.base import OCRBackend
from rd_core.ocr.chandra import ChandraBackend
from rd_core.ocr.dummy import DummyOCRBackend
from rd_core.ocr.export_stats import ExportStats
from rd_core.ocr.factory import create_ocr_engine
from rd_core.ocr.html_generator import generate_html_from_pages
from rd_core.ocr.md import generate_md_from_pages, generate_md_from_result
from rd_core.ocr.qwen import QwenBackend
from rd_core.ocr.utils import image_to_base64, image_to_pdf_base64

__all__ = [
    # Backends
    "OCRBackend",
    "ChandraBackend",
    "QwenBackend",
    "DummyOCRBackend",
    # Utils
    "image_to_base64",
    "image_to_pdf_base64",
    "generate_html_from_pages",
    "generate_md_from_pages",
    "generate_md_from_result",
    "ExportStats",
    # Factory
    "create_ocr_engine",
]
