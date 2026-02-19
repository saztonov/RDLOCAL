"""Миксин для управления панелью подсказки и OCR preview"""


class HintPanelMixin:
    """Миксин для управления hint panel и OCR preview"""

    def _show_hint_panel(self, block):
        """Активировать панель подсказки для блока"""
        if hasattr(self, "hint_group"):
            self._selected_image_block = block
            self.hint_edit.blockSignals(True)
            self.hint_edit.setPlainText(block.hint or "")
            self.hint_edit.blockSignals(False)
            # В режиме read_only панель подсказки активна, но только для чтения
            is_read_only = (
                self.page_viewer.read_only if hasattr(self, "page_viewer") else False
            )
            self.hint_edit.setReadOnly(is_read_only)
            self.hint_group.setEnabled(True)

    def _hide_hint_panel(self):
        """Деактивировать панель подсказки"""
        if hasattr(self, "hint_group"):
            self._selected_image_block = None
            self.hint_edit.blockSignals(True)
            self.hint_edit.clear()
            self.hint_edit.blockSignals(False)
            self.hint_group.setEnabled(False)

    def _show_ocr_preview(self, block_id: str):
        """Показать OCR preview для блока"""
        if hasattr(self, "ocr_preview") and self.ocr_preview:
            self.ocr_preview.show_block(block_id)
        if hasattr(self, "ocr_preview_inline") and self.ocr_preview_inline:
            self.ocr_preview_inline.show_block(block_id)

    def _hide_ocr_preview(self):
        """Скрыть OCR preview"""
        if hasattr(self, "ocr_preview") and self.ocr_preview:
            self.ocr_preview.clear()
        if hasattr(self, "ocr_preview_inline") and self.ocr_preview_inline:
            self.ocr_preview_inline.clear()

    def _load_ocr_result_file(self):
        """Загрузить _result.json для текущего PDF"""
        pdf_path = getattr(self, "_current_pdf_path", None)
        r2_key = getattr(self, "_current_r2_key", None)
        if pdf_path:
            if hasattr(self, "ocr_preview") and self.ocr_preview:
                self.ocr_preview.load_result_file(pdf_path, r2_key)
            if hasattr(self, "ocr_preview_inline") and self.ocr_preview_inline:
                self.ocr_preview_inline.load_result_file(pdf_path, r2_key)
