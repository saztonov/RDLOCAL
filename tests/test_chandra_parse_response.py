"""Тесты для парсинга ответов Chandra (LM Studio) — реальные формы из логов 2026-03-21.

Покрывает:
- Пустой content + HTML в reasoning_content
- reasoning-проза перед HTML в reasoning_content
- list-формат content
- <think> теги в reasoning_content
- Нормальный content (не затронут)
- strip_untagged_reasoning safety net
"""
import pytest

from rd_core.ocr._chandra_common import (
    _normalize_chandra_response,
    _strip_reasoning_before_html,
    parse_response,
)
from rd_core.ocr.utils import strip_untagged_reasoning
from rd_core.ocr_result import is_error


# ── Fixtures: реальные формы ответов из логов 2026-03-21 ──────────


def _make_response(content="", reasoning_content=None, tool_calls=None):
    """Вспомогательный builder для response_json."""
    message = {"role": "assistant", "content": content, "tool_calls": tool_calls or []}
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    return {
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


# ── Тесты parse_response ─────────────────────────────────────────


class TestParseResponseNormalContent:
    """Нормальный content — используется как есть."""

    def test_normal_html_content(self):
        resp = _make_response(content='<div data-bbox="0 1 223 14">OCR text</div>')
        result = parse_response(resp)
        assert "OCR text" in result
        assert not is_error(result)

    def test_content_preferred_over_reasoning(self):
        resp = _make_response(
            content='<p>Actual OCR</p>',
            reasoning_content="Some reasoning that should be ignored",
        )
        result = parse_response(resp)
        assert "<p>Actual OCR</p>" in result
        assert "reasoning" not in result


class TestParseResponseReasoningFallback:
    """Пустой content + reasoning_content — fallback с очисткой."""

    def test_empty_content_html_in_reasoning(self):
        """Блок 6KWD-EYLA-RWW из логов: content пуст, HTML в reasoning_content."""
        resp = _make_response(
            content="",
            reasoning_content=(
                '<div data-bbox="0 1 223 14" data-label="Page-Header">'
                "BLOCK:6KWD-EYLA-RWW</div>"
            ),
        )
        result = parse_response(resp)
        assert result.startswith("<div")
        assert "6KWD-EYLA-RWW" in result
        assert not is_error(result)

    def test_reasoning_prose_before_html_block_96KM(self):
        """Блок 96KM-9FCD-EFH из логов: reasoning-проза + HTML в reasoning_content."""
        reasoning = (
            "The user wants me to extract data from a table, likely a technical "
            "specification or change log (Stage P document). The table has 5 columns: "
            "'Разрешение' (Approval), 'Обозначение' (Designation).\n\n"
            '<div data-bbox="17 9 200 38" data-label="Page-Header">\n'
            "<p>BLOCK: 96KM-9FCD-EFH</p>\n</div>"
        )
        resp = _make_response(content="", reasoning_content=reasoning)
        result = parse_response(resp)
        assert result.startswith("<div")
        assert "The user wants" not in result
        assert "96KM-9FCD-EFH" in result

    def test_reasoning_prose_before_html_block_PDRQ(self):
        """Блок PDRQ-JP4E-URR из логов: reasoning-проза + HTML в reasoning_content."""
        reasoning = (
            "The user wants me to act as a specialist OCR system for Russian "
            "construction documentation, specifically GOST, SNiP, SP, TU. "
            "I need to process technical specifications.\n\n"
            "The input text is:\n"
            '<p>Общие сведения и указания</p>'
        )
        resp = _make_response(content="", reasoning_content=reasoning)
        result = parse_response(resp)
        assert result.startswith("<p>")
        assert "The user wants" not in result
        assert "Общие сведения" in result

    def test_think_tags_in_reasoning(self):
        """<think> теги в reasoning_content очищаются."""
        resp = _make_response(
            content="",
            reasoning_content='<think>Planning the OCR...</think><p>OCR result</p>',
        )
        result = parse_response(resp)
        assert "Planning" not in result
        assert "<p>OCR result</p>" in result

    def test_reasoning_only_no_html(self):
        """reasoning без HTML — теперь возвращает ошибку (reasoning отброшен)."""
        resp = _make_response(
            content="",
            reasoning_content="Просто текст без HTML разметки с данными чертежа",
        )
        result = parse_response(resp)
        assert is_error(result)


class TestParseResponseListContent:
    """content как list — нормализуется в строку."""

    def test_list_content_text_item(self):
        resp = _make_response()
        resp["choices"][0]["message"]["content"] = [
            {"type": "text", "text": "<p>OCR text from list</p>"}
        ]
        result = parse_response(resp)
        assert "<p>OCR text from list</p>" in result

    def test_list_content_multiple_items(self):
        resp = _make_response()
        resp["choices"][0]["message"]["content"] = [
            {"type": "text", "text": "<p>Part 1</p>"},
            {"type": "text", "text": "<p>Part 2</p>"},
        ]
        result = parse_response(resp)
        assert "Part 1" in result
        assert "Part 2" in result


class TestParseResponseEdgeCases:
    """Граничные случаи."""

    def test_empty_content_empty_reasoning(self):
        resp = _make_response(content="", reasoning_content="")
        result = parse_response(resp)
        assert is_error(result)

    def test_no_choices(self):
        result = parse_response({"error": "model_not_found"})
        assert is_error(result)

    def test_none_content(self):
        resp = _make_response()
        resp["choices"][0]["message"]["content"] = None
        resp["choices"][0]["message"]["reasoning_content"] = "<p>Fallback</p>"
        result = parse_response(resp)
        assert "<p>Fallback</p>" in result


# ── Тесты _strip_reasoning_before_html ────────────────────────────


class TestStripReasoningBeforeHtml:
    def test_html_at_start(self):
        text = '<div data-bbox="0 0 100 100">Text</div>'
        result, stripped = _strip_reasoning_before_html(text)
        assert result == text
        assert stripped == 0

    def test_reasoning_then_html(self):
        text = "Some reasoning text\n\n<p>OCR content</p>"
        result, stripped = _strip_reasoning_before_html(text)
        assert result.startswith("<p>")
        assert stripped > 0

    def test_no_html(self):
        text = "Just plain text without any HTML tags"
        result, stripped = _strip_reasoning_before_html(text)
        assert result == ""
        assert stripped == len(text)

    def test_empty_string(self):
        result, stripped = _strip_reasoning_before_html("")
        assert result == ""
        assert stripped == 0

    def test_table_tag(self):
        text = 'Reasoning...\n<table border="1"><tr><td>Cell</td></tr></table>'
        result, stripped = _strip_reasoning_before_html(text)
        assert result.startswith("<table")

    def test_whitespace_before_html(self):
        text = "  <div>Content</div>"
        result, stripped = _strip_reasoning_before_html(text)
        assert "<div>" in result
        assert stripped == 0  # starts with < after lstrip


# ── Тесты _normalize_chandra_response ─────────────────────────────


class TestNormalizeChandraResponse:
    def test_content_string(self):
        msg = {"content": "<p>Text</p>"}
        text, source = _normalize_chandra_response(msg)
        assert text == "<p>Text</p>"
        assert source == "content"

    def test_content_list(self):
        msg = {"content": [{"type": "text", "text": "<p>List text</p>"}]}
        text, source = _normalize_chandra_response(msg)
        assert "<p>List text</p>" in text
        assert source == "content"

    def test_reasoning_fallback(self):
        msg = {"content": "", "reasoning_content": "<div>OCR</div>"}
        text, source = _normalize_chandra_response(msg)
        assert "<div>OCR</div>" in text
        assert source == "reasoning_content"

    def test_reasoning_with_prose(self):
        msg = {
            "content": "",
            "reasoning_content": "The user wants...\n\n<p>Real OCR</p>",
        }
        text, source = _normalize_chandra_response(msg)
        assert text.startswith("<p>")
        assert source == "reasoning_content"

    def test_empty_everything(self):
        msg = {"content": "", "reasoning_content": ""}
        text, source = _normalize_chandra_response(msg)
        assert text == ""
        assert source == "empty"


# ── Тесты strip_untagged_reasoning safety net ─────────────────────


class TestStripUntaggedReasoningSafetyNet:
    """Проверяем что расширенные паттерны в _REASONING_PREFIX_RE работают."""

    def test_the_user_pattern(self):
        text = 'The user wants me to...\n\n<p>OCR</p>'
        result = strip_untagged_reasoning(text)
        assert result.startswith("<p>")

    def test_the_image_pattern(self):
        text = 'The image shows a table with...\n\n<table><tr><td>X</td></tr></table>'
        result = strip_untagged_reasoning(text)
        assert result.startswith("<table>")

    def test_this_is_pattern(self):
        text = 'This is a document page with...\n<div>Content</div>'
        result = strip_untagged_reasoning(text)
        assert result.startswith("<div>")

    def test_html_at_start_untouched(self):
        text = '<p>Already HTML</p>'
        result = strip_untagged_reasoning(text)
        assert result == text

    def test_non_reasoning_text_untouched(self):
        text = 'Какой-то обычный OCR текст без HTML'
        result = strip_untagged_reasoning(text)
        assert result == text

    def test_pure_reasoning_no_html_returns_empty(self):
        """Reasoning prefix + no HTML → empty string."""
        text = "The user wants me to process a table...\nI need to identify columns."
        result = strip_untagged_reasoning(text)
        assert result == ""


# ── Тесты structured output ──────────────────────────────────────


class TestStructuredOutput:
    """Тесты structured output (response_format json_schema)."""

    def test_structured_json_content(self):
        """Structured JSON в content — извлекаем ocr_html."""
        resp = _make_response(
            content='{"ocr_html": "<p>OCR result</p>"}',
        )
        result = parse_response(resp)
        assert "<p>OCR result</p>" in result
        assert not is_error(result)

    def test_structured_json_with_whitespace(self):
        resp = _make_response(
            content='  {"ocr_html": "<table><tr><td>Cell</td></tr></table>"}  ',
        )
        result = parse_response(resp)
        assert "<table>" in result

    def test_invalid_json_falls_through(self):
        """Невалидный JSON — fallback на обычный парсинг."""
        resp = _make_response(
            content='<p>Regular HTML content</p>',
        )
        result = parse_response(resp)
        assert "<p>Regular HTML content</p>" in result

    def test_structured_json_empty_html(self):
        """JSON с пустым ocr_html — content не парсится как structured,
        но проходит как обычный текст (JSON строка)."""
        resp = _make_response(
            content='{"ocr_html": ""}',
        )
        result = parse_response(resp)
        # Пустой ocr_html не извлекается structured парсером,
        # content проходит как обычный текст
        assert not is_error(result)


# ── Тесты предотвращения утечки reasoning ─────────────────────────


class TestReasoningLeakPrevention:
    """Тесты предотвращения утечки reasoning (issue 9VMW-X3JY-UD4)."""

    def test_pure_reasoning_in_reasoning_content_returns_error(self):
        """Чистый reasoning в reasoning_content → ошибка."""
        resp = _make_response(
            content="",
            reasoning_content=(
                "The user wants me to process a table from Russian "
                "construction documentation. I need to identify...\n"
                "I will now generate the HTML table based on this analysis."
            ),
        )
        result = parse_response(resp)
        assert is_error(result)

    def test_pure_reasoning_in_content_falls_through(self):
        """Reasoning в content без HTML → fallback на reasoning_content."""
        resp = _make_response(
            content="The user wants me to analyze this document...",
            reasoning_content="<p>Actual OCR result</p>",
        )
        result = parse_response(resp)
        assert "<p>Actual OCR result</p>" in result
        assert "The user wants" not in result

    def test_content_with_reasoning_prefix_and_html(self):
        """Content с reasoning + HTML — reasoning обрезается."""
        resp = _make_response(
            content="The user wants me to...\n\n<p>OCR text</p>",
        )
        result = parse_response(resp)
        assert result.startswith("<p>")
        assert "The user wants" not in result

    def test_reasoning_content_with_trailing_div(self):
        """reasoning + одинокий </div> — не считать OCR."""
        resp = _make_response(
            content="",
            reasoning_content=(
                "The user wants me to analyze this table.\n"
                "I will now generate the HTML.\n</div>"
            ),
        )
        result = parse_response(resp)
        assert is_error(result)


# ── Тесты reasoning-detection в is_suspicious_output ──────────────


class TestSuspiciousReasoningDetection:
    """Тесты reasoning-detection в is_suspicious_output."""

    def test_reasoning_prose_is_suspicious(self):
        from rd_core.ocr_result import is_suspicious_output
        text = (
            "The user wants me to process a table from Russian construction documentation. "
            "I need to identify the document type.\n"
            "I will now generate the HTML table based on this analysis."
        )
        suspicious, reason = is_suspicious_output(text)
        assert suspicious
        assert "reasoning" in reason.lower()

    def test_normal_russian_text_not_suspicious(self):
        from rd_core.ocr_result import is_suspicious_output
        text = "Общие сведения и указания по рабочей документации"
        suspicious, _ = is_suspicious_output(text)
        assert not suspicious

    def test_normal_html_not_suspicious(self):
        from rd_core.ocr_result import is_suspicious_output
        text = (
            '<div data-bbox="0 0 100 50"><p>Общие сведения и указания по рабочей '
            'документации. Многоквартирный жилой дом со встроенными помещениями.</p></div>'
        )
        suspicious, _ = is_suspicious_output(text)
        assert not suspicious
