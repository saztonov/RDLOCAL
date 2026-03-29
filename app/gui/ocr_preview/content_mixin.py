"""Миксин загрузки и форматирования контента OCR."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from rd_core.ocr.generator_common import extract_image_ocr_data, is_image_ocr_json
from rd_core.ocr.html_generator import _format_image_ocr_html

logger = logging.getLogger(__name__)


class ContentMixin:
    """Загрузка результатов и форматирование контента."""

    def load_from_annotation(self, ann_data: dict, node_id: Optional[str] = None):
        """Загрузить OCR данные из словаря аннотации.

        Args:
            ann_data: словарь аннотации (Document.to_dict())
            node_id: ID узла для сохранения изменений
        """
        self._result_data = ann_data
        self._node_id = node_id
        self._blocks_index: Dict[str, Dict] = {}

        if not ann_data:
            return

        try:
            # Индексируем блоки по ID из структуры {pages: [{blocks: [...]}]}
            blocks_count = 0
            for page in self._result_data.get("pages", []):
                for block in page.get("blocks", []):
                    block_id = block.get("id")
                    if block_id:
                        self._blocks_index[block_id] = block
                        blocks_count += 1

            logger.info(f"Loaded annotation data ({blocks_count} blocks)")
            self.title_label.setText(f"OCR Preview ({blocks_count} блоков)")
        except Exception as e:
            logger.error(f"Failed to load annotation data: {e}")
            self.title_label.setText("OCR Preview")

    def show_block(self, block_id: str):
        """Показать OCR результат для блока"""
        self._current_block_id = block_id
        self._is_modified = False
        self._is_editing = False

        # Сбрасываем в режим просмотра
        self.editor_widget.hide()
        self.edit_save_btn.setText("✏️ Редактировать")
        self.edit_save_btn.setToolTip("Редактировать HTML")
        self.edit_save_btn.setEnabled(False)

        # Обновляем ID блока
        self.block_id_label.setText(block_id if block_id else "")

        if not self._result_data or not block_id:
            self._show_placeholder()
            return

        # Ищем блок по индексу
        block_data = self._blocks_index.get(block_id)

        if not block_data:
            self.preview_edit.setHtml(
                '<p style="color: #888;">OCR результат для этого блока не найден</p>'
            )
            self.html_edit.clear()
            self.html_edit.setEnabled(False)
            self.stamp_group.hide()
            return

        block_type = block_data.get("block_type", "text")

        # Получаем HTML (ocr_html из result.json)
        html_content = block_data.get("ocr_html", "") or block_data.get("html", "")

        # Для IMAGE блоков: форматируем ocr_json если есть
        if block_type == "image":
            html_content = self._format_image_block(block_data, html_content)

        # Fallback: ocr_text если нет HTML
        if not html_content and block_data.get("ocr_text"):
            html_content = f"<pre>{block_data['ocr_text']}</pre>"

        # Обрабатываем штамп отдельно
        stamp_data = block_data.get("stamp_data")
        if stamp_data:
            self._show_stamp(stamp_data)
        else:
            self.stamp_group.hide()

        if not html_content:
            self.preview_edit.setHtml(
                '<p style="color: #888;">Пустой OCR результат</p>'
            )
            self.html_edit.clear()
            self.html_edit.setEnabled(False)
            return

        # Показываем HTML
        styled_html = self._apply_preview_styles(html_content)
        self.preview_edit.setHtml(styled_html)

        # Редактор (загружаем контент, но не показываем)
        self.html_edit.blockSignals(True)
        self.html_edit.setPlainText(html_content)
        self.html_edit.blockSignals(False)
        self.html_edit.setEnabled(True)

        # Включаем кнопку редактирования
        self.edit_save_btn.setEnabled(True)

        self.title_label.setText("OCR Preview")

    def _show_stamp(self, stamp_data: dict):
        """Показать данные штампа в отдельном блоке"""
        lines = []

        if stamp_data.get("document_code"):
            lines.append(f"<b>Шифр:</b> {stamp_data['document_code']}")

        if stamp_data.get("sheet_name"):
            lines.append(f"<b>Наименование:</b> {stamp_data['sheet_name']}")

        sheet_num = stamp_data.get("sheet_number", "")
        total = stamp_data.get("total_sheets", "")
        if sheet_num or total:
            lines.append(f"<b>Лист:</b> {sheet_num}/{total}")

        if stamp_data.get("stage"):
            lines.append(f"<b>Стадия:</b> {stamp_data['stage']}")

        if stamp_data.get("organization"):
            lines.append(f"<b>Организация:</b> {stamp_data['organization']}")

        if stamp_data.get("project_name"):
            lines.append(f"<b>Проект:</b> {stamp_data['project_name']}")

        signatures = stamp_data.get("signatures", [])
        if signatures:
            sig_parts = [
                f"{s.get('role', '')}: {s.get('surname', '')} ({s.get('date', '')})"
                for s in signatures
            ]
            lines.append(f"<b>Подписи:</b> {'; '.join(sig_parts)}")

        self.stamp_content.setText("<br>".join(lines))
        self.stamp_group.show()

    def _format_image_block(self, block_data: dict, html_content: str) -> str:
        """Форматировать IMAGE блок с crop link и OCR контентом.

        Приоритет контента:
        1. ocr_html (сохраняет ручное редактирование)
        2. HTML из ocr_json через общий _format_image_ocr_html
        3. Structured parse из ocr_text
        4. <pre> fallback
        """
        parts = []

        # Ссылка на кроп: crop_url приоритет, иначе image_file как file:///
        crop_link = self._build_crop_link(block_data)
        if crop_link:
            parts.append(crop_link)

        # Контент по приоритету
        content = ""

        # 1. ocr_html — приоритет (сохраняет ручное редактирование)
        if html_content:
            content = html_content

        # 2. ocr_json → общий formatter
        if not content:
            ocr_json = block_data.get("ocr_json")
            if ocr_json and isinstance(ocr_json, dict):
                if is_image_ocr_json(ocr_json):
                    content = _format_image_ocr_html(ocr_json)

        # 3. ocr_text → structured parse
        if not content and block_data.get("ocr_text"):
            ocr_text = block_data["ocr_text"]
            try:
                import json as json_module
                parsed = json_module.loads(ocr_text.strip())
                if isinstance(parsed, dict) and is_image_ocr_json(parsed):
                    content = _format_image_ocr_html(parsed)
            except (json_module.JSONDecodeError, AttributeError):
                pass

        # 4. Fallback: ocr_text как есть
        if not content and block_data.get("ocr_text"):
            content = f"<pre>{block_data['ocr_text']}</pre>"

        if content:
            parts.append(content)

        return "\n".join(parts) if parts else html_content

    @staticmethod
    def _build_crop_link(block_data: dict) -> str:
        """Построить HTML-ссылку на кроп (crop_url или image_file)."""
        crop_url = block_data.get("crop_url")
        if crop_url:
            return f'<p><a href="{crop_url}" target="_blank">📎 Открыть кроп</a></p>'

        image_file = block_data.get("image_file")
        if image_file:
            p = Path(image_file)
            try:
                file_url = p.as_uri()
            except ValueError:
                file_url = p.absolute().as_uri()
            return f'<p><a href="{file_url}" target="_blank">📎 Открыть кроп (локальный)</a></p>'

        return ""

    def _apply_preview_styles(self, html: str) -> str:
        """Добавить стили для preview (полноценный CSS для WebEngine)"""
        style = """
        <style>
            * { box-sizing: border-box; }
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                font-size: 13px;
                line-height: 1.5;
                color: #d4d4d4;
                background-color: #1e1e1e;
                margin: 8px;
                padding: 0;
            }
            table { border-collapse: collapse; width: 100%; margin: 12px 0; }
            th, td { border: 1px solid #444; padding: 6px 10px; text-align: left; vertical-align: top; }
            th { background-color: #2d2d2d; font-weight: 600; }
            tr:nth-child(even) { background-color: #252526; }
            tr:hover { background-color: #333; }
            h1, h2, h3, h4 { color: #569cd6; margin: 16px 0 8px 0; }
            h1 { font-size: 18px; border-bottom: 1px solid #444; padding-bottom: 4px; }
            h2 { font-size: 16px; }
            h3 { font-size: 14px; }
            h4 { font-size: 13px; }
            p { margin: 8px 0; }
            ul, ol { margin: 8px 0; padding-left: 24px; }
            li { margin: 4px 0; }
            pre {
                background: #252526;
                padding: 10px;
                border-radius: 4px;
                overflow-x: auto;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                white-space: pre-wrap;
                word-wrap: break-word;
            }
            a { color: #4fc3f7; text-decoration: none; }
            a:hover { text-decoration: underline; }
            img { max-width: 100%; height: auto; }
        </style>
        """
        return f"<!DOCTYPE html><html><head><meta charset='UTF-8'>{style}</head><body>{html}</body></html>"
