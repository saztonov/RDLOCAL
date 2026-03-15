"""Тесты для rd_core.ocr_result — единого модуля OCR-статусов."""
import pytest

from rd_core.ocr_result import (
    ERROR_PREFIX,
    NON_RETRIABLE_PREFIX,
    OCRStatus,
    get_status,
    is_any_error,
    is_error,
    is_non_retriable,
    is_success,
    make_error,
    make_non_retriable,
    needs_ocr,
)


class TestMakeError:
    def test_make_error_format(self):
        assert make_error("timeout") == "[Ошибка: timeout]"

    def test_make_non_retriable_format(self):
        assert make_non_retriable("контекст превышен") == "[НеПовторяемая ошибка: контекст превышен]"

    def test_make_error_starts_with_prefix(self):
        result = make_error("API 429")
        assert result.startswith(ERROR_PREFIX)

    def test_make_non_retriable_starts_with_prefix(self):
        result = make_non_retriable("невалидные координаты")
        assert result.startswith(NON_RETRIABLE_PREFIX)


class TestIsError:
    def test_error_text(self):
        assert is_error("[Ошибка: timeout]") is True

    def test_non_retriable_not_error(self):
        assert is_error("[НеПовторяемая ошибка: x]") is False

    def test_success_text(self):
        assert is_error("Some OCR text") is False

    def test_none(self):
        assert is_error(None) is False

    def test_empty(self):
        assert is_error("") is False

    def test_make_error_roundtrip(self):
        assert is_error(make_error("test")) is True


class TestIsNonRetriable:
    def test_non_retriable_text(self):
        assert is_non_retriable("[НеПовторяемая ошибка: контекст превышен]") is True

    def test_error_not_non_retriable(self):
        assert is_non_retriable("[Ошибка: timeout]") is False

    def test_success_text(self):
        assert is_non_retriable("Some text") is False

    def test_none(self):
        assert is_non_retriable(None) is False

    def test_make_non_retriable_roundtrip(self):
        assert is_non_retriable(make_non_retriable("test")) is True


class TestIsAnyError:
    def test_error(self):
        assert is_any_error(make_error("x")) is True

    def test_non_retriable(self):
        assert is_any_error(make_non_retriable("x")) is True

    def test_success(self):
        assert is_any_error("text") is False

    def test_none(self):
        assert is_any_error(None) is False


class TestIsSuccess:
    def test_normal_text(self):
        assert is_success("Распознанный текст") is True

    def test_error_text(self):
        assert is_success(make_error("x")) is False

    def test_non_retriable(self):
        assert is_success(make_non_retriable("x")) is False

    def test_none(self):
        assert is_success(None) is False

    def test_empty(self):
        assert is_success("") is False

    def test_whitespace_only(self):
        assert is_success("   ") is False


class TestGetStatus:
    def test_success(self):
        assert get_status("text") == OCRStatus.SUCCESS

    def test_error(self):
        assert get_status(make_error("x")) == OCRStatus.ERROR

    def test_non_retriable(self):
        assert get_status(make_non_retriable("x")) == OCRStatus.ERROR

    def test_none(self):
        assert get_status(None) == OCRStatus.NOT_RECOGNIZED

    def test_empty(self):
        assert get_status("") == OCRStatus.NOT_RECOGNIZED

    def test_whitespace(self):
        assert get_status("  ") == OCRStatus.NOT_RECOGNIZED


class TestNeedsOcr:
    def test_no_text(self):
        block = type("B", (), {"ocr_text": None, "is_correction": False})()
        assert needs_ocr(block) is True

    def test_success_text(self):
        block = type("B", (), {"ocr_text": "recognized", "is_correction": False})()
        assert needs_ocr(block) is False

    def test_error_text(self):
        block = type("B", (), {"ocr_text": make_error("x"), "is_correction": False})()
        assert needs_ocr(block) is True

    def test_is_correction(self):
        block = type("B", (), {"ocr_text": "recognized", "is_correction": True})()
        assert needs_ocr(block) is True

    def test_no_is_correction_attr(self):
        block = type("B", (), {"ocr_text": None})()
        assert needs_ocr(block) is True


class TestShimCompatibility:
    """Проверяем что shim-модули re-экспортируют корректно."""

    def test_ocr_block_status_shim(self):
        from rd_core.ocr_block_status import OCRStatus as ShimStatus
        from rd_core.ocr_block_status import get_ocr_status, needs_ocr as shim_needs_ocr

        assert ShimStatus is OCRStatus
        assert get_ocr_status("text") == OCRStatus.SUCCESS
        assert get_ocr_status(None) == OCRStatus.NOT_RECOGNIZED
        assert shim_needs_ocr is needs_ocr
