"""Тесты для rd_core/ocr/factory.py — создание OCR бэкендов."""

from rd_core.ocr import create_ocr_engine


class TestCreateOCREngine:
    def test_dummy_backend(self):
        engine = create_ocr_engine("dummy")
        assert engine is not None
        assert hasattr(engine, "recognize")

    def test_chandra_backend(self):
        """Chandra backend создаётся."""
        engine = create_ocr_engine("chandra")
        assert engine is not None
        assert hasattr(engine, "recognize")
        assert type(engine).__name__ == "ChandraBackend"

    def test_qwen_backend(self):
        """Qwen backend создаётся."""
        engine = create_ocr_engine("qwen")
        assert engine is not None
        assert hasattr(engine, "recognize")
        assert type(engine).__name__ == "QwenBackend"

    def test_unknown_backend_returns_dummy(self):
        """Неизвестный backend возвращает dummy."""
        engine = create_ocr_engine("nonexistent_engine")
        assert engine is not None

    def test_legacy_openrouter_returns_dummy(self):
        """Удалённый openrouter возвращает dummy."""
        engine = create_ocr_engine("openrouter")
        assert type(engine).__name__ == "DummyOCRBackend"

    def test_legacy_datalab_returns_dummy(self):
        """Удалённый datalab возвращает dummy."""
        engine = create_ocr_engine("datalab")
        assert type(engine).__name__ == "DummyOCRBackend"

    def test_dummy_recognize_returns_empty(self):
        engine = create_ocr_engine("dummy")
        result = engine.recognize(None)
        assert isinstance(result, str)

    def test_dummy_supports_pdf_false(self):
        engine = create_ocr_engine("dummy")
        assert engine.supports_pdf_input() is False
