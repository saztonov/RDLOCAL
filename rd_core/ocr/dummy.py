"""Dummy OCR Backend (заглушка)"""
from typing import Optional

from PIL import Image


class DummyOCRBackend:
    """Заглушка для OCR"""

    def supports_pdf_input(self) -> bool:
        """Заглушка не поддерживает PDF"""
        return False

    def recognize(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool | None = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        return "[OCR placeholder - OCR engine not configured]"
