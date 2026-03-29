"""Regression tests для защиты от bbox JSON-дампов в verification и экспорте.

Кейс: Chandra возвращает [{"label": "Table", "bbox": "30 31 985 1000"}]
при retry — чистый bbox-dump без полезного HTML. Этот JSON не должен
попадать в итоговые _ocr.html и _document.md.
"""

import json
import tempfile
from pathlib import Path

import pytest

from rd_core.ocr_result import is_suspicious_output


# ── Тестовые данные ──────────────────────────────────────────────────

BBOX_JSON_DUMP = '[{"label": "Table", "bbox": "30 31 985 1000"}]'

BBOX_JSON_MULTI = json.dumps([
    {"label": "Table", "bbox": "30 31 985 1000"},
    {"label": "Section-Header", "bbox": "100 10 500 50"},
])

VALID_HTML = (
    '<div data-bbox="318 21 627 42"><h2>Общие сведения и указания</h2></div>'
    '<div data-bbox="35 55 979 983"><ol><li>Проект устройства монолитных '
    'вертикальных конструкций Корпуса 14.1 в составе многоквартирного жилого '
    'дома.</li></ol></div>'
)

VALID_STRUCTURED_ARRAY = json.dumps([
    {"data-bbox": "318 21 627 42", "data-label": "Section-Header", "html": "<h2>Общие сведения</h2>"},
])

# Валидные UUID для тестовых блоков (get_block_armor_id требует hex-совместимый ID)
TEST_BLOCK_ID_1 = "a0b1c2d3-e4f5-6789-abcd-ef0123456789"
TEST_BLOCK_ID_2 = "b1c2d3e4-f5a6-7890-bcde-f01234567890"

MINIMAL_RESULT = {
    "pdf_path": "test.pdf",
    "pages": [
        {
            "page_number": 1,
            "blocks": [
                {
                    "id": TEST_BLOCK_ID_1,
                    "block_type": "text",
                    "ocr_html": BBOX_JSON_DUMP,
                    "ocr_text": BBOX_JSON_DUMP,
                    "created_at": "2026-03-29 21:03:40",
                },
            ],
        }
    ],
}


# ── is_suspicious_output детектирует bbox dump ────────────────────────

class TestSuspiciousOutputDetection:
    """is_suspicious_output должен ловить чистые bbox JSON-дампы."""

    def test_pure_bbox_json_is_suspicious(self):
        suspicious, reason = is_suspicious_output(BBOX_JSON_DUMP, BBOX_JSON_DUMP)
        assert suspicious, f"Expected suspicious, got reason={reason}"
        assert "layout-dump" in reason.lower() or "bbox" in reason.lower()

    def test_multi_element_bbox_json_is_suspicious(self):
        suspicious, _ = is_suspicious_output(BBOX_JSON_MULTI, BBOX_JSON_MULTI)
        assert suspicious

    def test_valid_html_is_not_suspicious(self):
        suspicious, _ = is_suspicious_output(VALID_HTML, VALID_HTML)
        assert not suspicious

    def test_structured_array_with_html_is_suspicious_but_has_content(self):
        """JSON array с html ключами — suspicious по формату, но содержит контент."""
        suspicious, reason = is_suspicious_output(
            VALID_STRUCTURED_ARRAY, VALID_STRUCTURED_ARRAY
        )
        # Это ловится как "JSON layout-dump (bbox с html ключами)"
        # Но в реальном коде _try_extract_structured_array() обработает это до suspicious check
        assert suspicious


# ── regenerate_html_from_result не пропускает JSON-dump ───────────────

class TestRegenerateHtmlDefensiveGuard:
    """regenerate_html_from_result() не должен выводить литеральный JSON в HTML."""

    def test_bbox_json_not_in_html_output(self):
        from services.remote_ocr.server.ocr_result_merger import (
            regenerate_html_from_result,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_ocr.html"
            regenerate_html_from_result(MINIMAL_RESULT, output_path, doc_name="test")

            html_content = output_path.read_text(encoding="utf-8")

            assert BBOX_JSON_DUMP not in html_content, (
                "Литеральный bbox JSON-dump не должен появляться в HTML экспорте"
            )
            assert '"label"' not in html_content or "data-label" in html_content, (
                "Сырой JSON с label ключом не должен быть в HTML"
            )

    def test_valid_html_preserved_in_output(self):
        """Блок с валидным HTML должен сохраняться."""
        from services.remote_ocr.server.ocr_result_merger import (
            regenerate_html_from_result,
        )

        result = {
            "pdf_path": "test.pdf",
            "pages": [
                {
                    "page_number": 1,
                    "blocks": [
                        {
                            "id": "b1c2d3e4-f5a6-7890-bcde-f01234567890",
                            "block_type": "text",
                            "ocr_html": VALID_HTML,
                            "ocr_text": VALID_HTML,
                            "created_at": "2026-03-29 21:03:40",
                        },
                    ],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_ocr.html"
            regenerate_html_from_result(result, output_path, doc_name="test")

            html_content = output_path.read_text(encoding="utf-8")
            assert "Общие сведения" in html_content


# ── generate_md_from_result не пропускает JSON-dump ───────────────────

class TestGenerateMdDefensiveGuard:
    """generate_md_from_result() не должен выводить литеральный JSON в MD."""

    def test_bbox_json_not_in_md_output(self):
        from rd_core.ocr.md.generator import generate_md_from_result

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_document.md"
            generate_md_from_result(MINIMAL_RESULT, output_path, doc_name="test")

            md_content = output_path.read_text(encoding="utf-8")

            assert BBOX_JSON_DUMP not in md_content, (
                "Литеральный bbox JSON-dump не должен появляться в MD экспорте"
            )
            assert '"bbox"' not in md_content, (
                "Сырой JSON с bbox ключом не должен быть в MD"
            )

    def test_valid_html_converted_to_md(self):
        """Блок с валидным HTML должен конвертироваться в markdown."""
        from rd_core.ocr.md.generator import generate_md_from_result

        result = {
            "pdf_path": "test.pdf",
            "pages": [
                {
                    "page_number": 1,
                    "blocks": [
                        {
                            "id": "b1c2d3e4-f5a6-7890-bcde-f01234567890",
                            "block_type": "text",
                            "ocr_html": VALID_HTML,
                            "ocr_text": VALID_HTML,
                            "created_at": "2026-03-29 21:03:40",
                        },
                    ],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_document.md"
            generate_md_from_result(result, output_path, doc_name="test")

            md_content = output_path.read_text(encoding="utf-8")
            assert "Общие сведения" in md_content
