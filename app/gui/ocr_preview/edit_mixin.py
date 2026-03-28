"""Миксин редактирования OCR результатов."""
from __future__ import annotations

import logging

from PySide6.QtWidgets import QMessageBox

logger = logging.getLogger(__name__)


class EditMixin:
    """Редактирование HTML и сохранение результатов."""

    def _toggle_edit_mode(self):
        """Переключение между режимами просмотра и редактирования"""
        if not self._current_block_id:
            return

        if self._is_editing:
            # Сохраняем и закрываем редактор
            self._save_all()
            self._is_editing = False
            self.editor_widget.hide()
            self.edit_save_btn.setText("✏️ Редактировать")
            self.edit_save_btn.setToolTip("Редактировать HTML")
        else:
            # Открываем редактор
            self._is_editing = True
            self.editor_widget.show()
            self.edit_save_btn.setText("💾 Сохранить")
            self.edit_save_btn.setToolTip("Сохранить изменения")

    def _on_text_changed(self):
        """Обработка изменения текста"""
        if not self._current_block_id or not self._is_editing:
            return

        self._is_modified = True

        # Обновляем preview
        new_html = self.html_edit.toPlainText()
        styled_html = self._apply_preview_styles(new_html)
        self.preview_edit.setHtml(styled_html)

    def _save_all(self):
        """Сохранить изменения в Supabase"""
        if not self._node_id or not self._current_block_id:
            return

        try:
            new_html = self.html_edit.toPlainText()

            # Обновляем данные в структуре {pages: [{blocks: [...]}]}
            for page in self._result_data.get("pages", []):
                for b in page.get("blocks", []):
                    if b.get("id") == self._current_block_id:
                        b["ocr_html"] = new_html
                        # Обновляем индекс
                        self._blocks_index[self._current_block_id] = b
                        break

            # Сохраняем в Supabase
            if self._node_id:
                try:
                    from app.annotation_db import AnnotationDBIO

                    AnnotationDBIO.save_to_db_raw(self._node_id, self._result_data)
                    logger.info(f"Saved to Supabase: node_id={self._node_id}")
                except AttributeError:
                    # save_to_db_raw не существует — используем TreeClient напрямую
                    try:
                        from app.tree_client import TreeClient

                        client = TreeClient()
                        client.save_annotation(self._node_id, self._result_data)
                        logger.info(f"Saved to Supabase via TreeClient: node_id={self._node_id}")
                    except Exception as e:
                        logger.error(f"Failed to save to Supabase: {e}")
                except Exception as e:
                    logger.error(f"Failed to save to Supabase: {e}")

            self._is_modified = False

            from app.gui.toast import show_toast

            show_toast(self.window(), "Сохранено")

            self.content_changed.emit(self._current_block_id, new_html)

        except Exception as e:
            logger.error(f"Failed to save: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")
