"""Обработка результатов OCR"""

import logging

from PySide6.QtCore import Qt

logger = logging.getLogger(__name__)


class ResultHandlerMixin:
    """Миксин для обработки результатов OCR"""

    def _refresh_document_in_tree(self):
        """Обновить узел документа в дереве проектов"""
        node_id = getattr(self.main_window, "_current_node_id", None)
        if not node_id:
            return

        if not hasattr(self.main_window, "project_tree_widget"):
            return

        tree = self.main_window.project_tree_widget
        item = tree._node_map.get(node_id)
        if not item:
            return

        node = item.data(0, Qt.UserRole)
        if not node:
            return

        # Инвалидируем кэш метаданных R2 для этого документа
        try:
            from rd_core.r2_metadata_cache import get_metadata_cache

            r2_key = getattr(node, "r2_key", None)
            if r2_key:
                from pathlib import PurePosixPath

                prefix = str(PurePosixPath(r2_key).parent) + "/"
                get_metadata_cache().invalidate_prefix(prefix)
                logger.debug(f"Invalidated R2 metadata cache for prefix: {prefix}")
        except Exception as e:
            logger.warning(f"Failed to invalidate R2 metadata cache: {e}")

        logger.info(f"Refreshed document in tree: {node_id}")

    def _reload_annotation_from_result(self, extract_dir: str):
        """Обновить ocr_text в блоках из результата OCR (загрузка из Supabase)"""
        try:
            from app.annotation_db import AnnotationDBIO

            node_id = getattr(self.main_window, "_current_node_id", None)
            if not node_id:
                return

            loaded_doc = AnnotationDBIO.load_from_db(node_id)
            if not loaded_doc:
                logger.warning(f"Не удалось загрузить аннотацию из Supabase: {node_id}")
                return

            current_doc = self.main_window.annotation_document
            if not current_doc:
                return

            # Собираем ocr_text по ID блоков из результата OCR
            ocr_results = {}
            for page in loaded_doc.pages:
                for block in page.blocks:
                    if block.ocr_text:
                        ocr_results[block.id] = block.ocr_text

            # Обновляем только ocr_text в существующих блоках
            updated_count = 0
            for page in current_doc.pages:
                for block in page.blocks:
                    if block.id in ocr_results:
                        block.ocr_text = ocr_results[block.id]
                        # Снимаем флаг корректировки после успешного OCR
                        if block.is_correction:
                            block.is_correction = False
                        updated_count += 1

            self.main_window._render_current_page()
            if (
                hasattr(self.main_window, "blocks_tree_manager")
                and self.main_window.blocks_tree_manager
            ):
                self.main_window.blocks_tree_manager.update_blocks_tree()

            # Триггерим авто-сохранение с обновлёнными ocr_text
            if updated_count > 0:
                self.main_window._auto_save_annotation()

            # Перезагружаем OCR preview данные
            if hasattr(self.main_window, "_load_ocr_preview_data"):
                self.main_window._load_ocr_preview_data()

            # Обновляем OCR preview для текущего выбранного блока
            for preview_attr in ("ocr_preview", "ocr_preview_inline"):
                preview = getattr(self.main_window, preview_attr, None)
                if preview and getattr(preview, "_current_block_id", None):
                    preview.show_block(preview._current_block_id)

            logger.info(f"OCR результаты применены: {updated_count} блоков обновлено")
        except Exception as e:
            logger.error(f"Ошибка применения OCR результатов: {e}")
