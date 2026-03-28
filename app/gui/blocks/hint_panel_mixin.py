"""Миксин для управления OCR preview"""


class HintPanelMixin:
    """Миксин для управления OCR preview"""

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
