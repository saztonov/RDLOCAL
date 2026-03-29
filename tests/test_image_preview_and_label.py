"""Тесты для IMAGE preview formatter и label anchor helper."""
import pytest


# ── IMAGE preview formatter ──────────────────────────────────────────


class TestFormatImageBlock:
    """Тесты для ContentMixin._format_image_block (без Qt)."""

    def _make_mixin(self):
        """Создать минимальный ContentMixin для тестирования."""
        from app.gui.ocr_preview.content_mixin import ContentMixin

        mixin = ContentMixin.__new__(ContentMixin)
        return mixin

    def test_flat_image_json_renders_html(self):
        """Плоский IMAGE JSON рендерится как HTML, не как raw JSON."""
        mixin = self._make_mixin()
        block_data = {
            "block_type": "image",
            "ocr_json": {
                "fragment_type": "План",
                "content_summary": "План 1 этажа",
                "detailed_description": "Вид сверху",
            },
        }
        result = mixin._format_image_block(block_data, "")
        assert "[ИЗОБРАЖЕНИЕ]" in result
        assert "План 1 этажа" in result
        assert "{" not in result  # Нет raw JSON

    def test_analysis_wrapped_json_renders_html(self):
        """analysis-wrapped JSON рендерится так же."""
        mixin = self._make_mixin()
        block_data = {
            "block_type": "image",
            "ocr_json": {
                "analysis": {
                    "fragment_type": "Разрез",
                    "content_summary": "Разрез 1-1",
                    "detailed_description": "Поперечный разрез",
                }
            },
        }
        result = mixin._format_image_block(block_data, "")
        assert "[ИЗОБРАЖЕНИЕ]" in result
        assert "Разрез 1-1" in result

    def test_crop_url_priority_over_image_file(self):
        """crop_url используется приоритетно."""
        mixin = self._make_mixin()
        block_data = {
            "block_type": "image",
            "crop_url": "https://example.com/crop.pdf",
            "image_file": "/local/crop.png",
            "ocr_json": {"fragment_type": "План", "content_summary": "x"},
        }
        result = mixin._format_image_block(block_data, "")
        assert "https://example.com/crop.pdf" in result
        assert "file:///" not in result

    def test_image_file_fallback(self):
        """image_file используется если нет crop_url."""
        mixin = self._make_mixin()
        block_data = {
            "block_type": "image",
            "image_file": "/tmp/crop.png",
            "ocr_json": {"fragment_type": "План", "content_summary": "x"},
        }
        result = mixin._format_image_block(block_data, "")
        assert "file:///" in result
        assert "Открыть кроп" in result

    def test_crop_link_added_once(self):
        """Crop-link добавляется ровно один раз."""
        mixin = self._make_mixin()
        block_data = {
            "block_type": "image",
            "crop_url": "https://example.com/crop.pdf",
            "ocr_json": {"fragment_type": "План", "content_summary": "x"},
        }
        result = mixin._format_image_block(block_data, "")
        assert result.count("Открыть кроп") == 1

    def test_ocr_html_priority_over_ocr_json(self):
        """ocr_html приоритетнее ocr_json (ручное редактирование не теряется)."""
        mixin = self._make_mixin()
        edited_html = "<p>Пользовательское описание</p>"
        block_data = {
            "block_type": "image",
            "ocr_json": {"fragment_type": "План", "content_summary": "авто"},
        }
        result = mixin._format_image_block(block_data, edited_html)
        assert "Пользовательское описание" in result
        assert "авто" not in result

    def test_no_data_returns_empty(self):
        """Без данных возвращается пустой контент."""
        mixin = self._make_mixin()
        block_data = {"block_type": "image"}
        result = mixin._format_image_block(block_data, "")
        assert result == ""


# ── Label anchor helper ──────────────────────────────────────────────


class TestPolygonLabelAnchor:
    """Тесты для BlockRenderingMixin._polygon_label_anchor."""

    def _anchor(self, points, text_w=20.0, inset=5.0):
        from app.gui.page_viewer_blocks import BlockRenderingMixin

        return BlockRenderingMixin._polygon_label_anchor(points, text_w, inset)

    def test_rectangle_polygon_inside(self):
        """Номер прямоугольного полигона остаётся внутри блока."""
        # Прямоугольник 0,0 → 200,100
        points = [(0, 0), (200, 0), (200, 100), (0, 100)]
        x, y = self._anchor(points, text_w=20, inset=5)
        assert x < 200, "Метка должна быть внутри правой границы"
        assert x >= 0, "Метка не должна выходить за левую границу"
        assert y >= 0, "Метка не должна выходить за верхнюю границу"

    def test_polygon_with_far_right_bottom(self):
        """Полигон с дальним правым нижним ребром — номер у верхнего контура, не у bbox x2."""
        # Полигон: верхняя часть узкая (0-100), нижняя расширяется до 400
        points = [(0, 0), (100, 0), (400, 200), (300, 200), (0, 100)]
        x, y = self._anchor(points, text_w=20, inset=5)
        # Метка не должна привязываться к bbox x2=400
        assert x < 100, f"Метка x={x} не должна уезжать к правому краю bbox"

    def test_simple_triangle(self):
        """Треугольник — номер у верхней вершины."""
        points = [(100, 0), (200, 200), (0, 200)]
        x, y = self._anchor(points, text_w=15, inset=5)
        # Должен быть рядом с верхней вершиной (100, 0)
        assert y < 50, f"Метка y={y} должна быть у верхней границы"

    def test_zoom_consistency(self):
        """Разный inset (из-за zoom) не ломает относительное расположение."""
        points = [(0, 0), (200, 0), (200, 100), (0, 100)]
        x1, y1 = self._anchor(points, text_w=20, inset=5)
        x2, y2 = self._anchor(points, text_w=20, inset=10)
        # С большим inset метка чуть глубже внутрь
        assert x2 < x1, "С большим inset метка должна быть левее"
        assert y2 > y1, "С большим inset метка должна быть ниже"
