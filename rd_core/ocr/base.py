"""Базовый интерфейс для OCR движков"""
from typing import Optional, Protocol

from PIL import Image


class OCRBackend(Protocol):
    """
    Интерфейс для OCR-движков
    """

    def recognize(
        self,
        image: Optional[Image.Image],
        prompt: Optional[dict] = None,
        json_mode: bool | None = None,
        pdf_file_path: Optional[str] = None,
    ) -> str:
        """
        Распознать текст на изображении или PDF

        Args:
            image: изображение для распознавания (опционально если передан pdf_file_path)
            prompt: dict с ключами 'system' и 'user' (опционально)
            json_mode: принудительный JSON режим вывода
            pdf_file_path: путь к PDF файлу для моделей с поддержкой PDF

        Returns:
            Распознанный текст
        """
        ...

    def supports_pdf_input(self) -> bool:
        """
        Проверяет, поддерживает ли бэкенд прямой ввод PDF файлов

        Returns:
            True если поддерживает PDF, False иначе
        """
        ...
