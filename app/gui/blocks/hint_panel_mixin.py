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

    def _load_ocr_preview_data(self):
        """Загрузить OCR данные для preview из текущей аннотации"""
        ann_doc = getattr(self, "annotation_document", None)
        node_id = getattr(self, "_current_node_id", None)
        ann_data = ann_doc.to_dict() if ann_doc else None
        if ann_data:
            if hasattr(self, "ocr_preview") and self.ocr_preview:
                self.ocr_preview.load_from_annotation(ann_data, node_id)
            if hasattr(self, "ocr_preview_inline") and self.ocr_preview_inline:
                self.ocr_preview_inline.load_from_annotation(ann_data, node_id)
