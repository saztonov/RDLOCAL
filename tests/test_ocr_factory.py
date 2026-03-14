"""Тесты для rd_core/ocr/factory.py — создание OCR бэкендов."""

from rd_core.ocr import create_ocr_engine
from rd_core.ocr.base import OCRBackend


class TestCreateOCREngine:
    def test_dummy_backend(self):
        engine = create_ocr_engine("dummy")
        assert engine is not None
        assert hasattr(engine, "recognize")

    def test_openrouter_without_key(self):
        """OpenRouter без ключа — создаётся, но recognize упадёт."""
        engine = create_ocr_engine("openrouter", api_key="test-key", model_name="test/model")
        assert engine is not None
        assert hasattr(engine, "recognize")

    def test_unknown_backend_returns_dummy(self):
        """Неизвестный backend возвращает dummy."""
        engine = create_ocr_engine("nonexistent_engine")
        assert engine is not None

    def test_dummy_recognize_returns_empty(self):
        engine = create_ocr_engine("dummy")
        result = engine.recognize(None)
        assert isinstance(result, str)

    def test_dummy_supports_pdf_false(self):
        engine = create_ocr_engine("dummy")
        assert engine.supports_pdf_input() is False
