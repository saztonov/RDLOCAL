"""Миксин для обработки выбора блоков"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidgetItem

class BlockSelectionMixin:
    """Миксин для обработки выбора блоков"""

    def _on_block_selected(self, block_idx: int):
        """Обработка выбора блока"""
        if not self.annotation_document:
            self._hide_ocr_preview()
            return

        current_page_data = self._get_or_create_page(self.current_page)
        if not current_page_data or not (
            0 <= block_idx < len(current_page_data.blocks)
        ):
            self._hide_ocr_preview()
            return

        block = current_page_data.blocks[block_idx]

        # Показываем OCR preview для выбранного блока
        self._show_ocr_preview(block.id)

        self.blocks_tree_manager.select_block_in_tree(block_idx)

    def _on_blocks_selected(self, block_indices: list):
        """Обработка множественного выбора блоков"""
        if not self.annotation_document or not block_indices:
            return

        self.blocks_tree_manager.select_blocks_in_tree(block_indices)

    def _on_tree_block_clicked(self, item: QTreeWidgetItem, column: int):
        """Клик по блоку в дереве"""
        # Определяем, какое дерево было кликнуто
        tree = self.sender()
        if tree is None:
            tree = self.blocks_tree

        # Получаем все выбранные элементы
        selected_items = tree.selectedItems()

        # Фильтруем только блоки
        selected_blocks = []
        for sel_item in selected_items:
            sel_data = sel_item.data(0, Qt.UserRole)
            if (
                sel_data
                and isinstance(sel_data, dict)
                and sel_data.get("type") == "block"
            ):
                selected_blocks.append(sel_data)

        if not selected_blocks:
            return

        # Если выбрано несколько блоков на одной странице
        if len(selected_blocks) > 1:
            # Проверяем, что все блоки на одной странице
            page_num = selected_blocks[0]["page"]
            if all(b["page"] == page_num for b in selected_blocks):
                # Переключаем страницу если нужно
                if self.current_page != page_num:
                    self.navigation_manager.save_current_zoom()
                    self.current_page = page_num
                    self.navigation_manager.load_page_image(self.current_page)
                    self.navigation_manager.restore_zoom()

                current_page_data = self._get_or_create_page(self.current_page)
                self.page_viewer.set_blocks(
                    current_page_data.blocks if current_page_data else []
                )
                self.page_viewer.fit_to_view()

                # Выделяем все блоки
                block_indices = [b["idx"] for b in selected_blocks]
                self.page_viewer.selected_block_indices = block_indices
                self.page_viewer.selected_block_idx = None
                self.page_viewer._redraw_blocks()

                self._update_ui()
                return

        # Одиночное выделение
        data = item.data(0, Qt.UserRole)
        if not data or not isinstance(data, dict) or data.get("type") != "block":
            return

        page_num = data["page"]
        block_idx = data["idx"]

        if self.current_page != page_num:
            self.navigation_manager.save_current_zoom()

        self.current_page = page_num
        self.navigation_manager.load_page_image(self.current_page)
        self.navigation_manager.restore_zoom()

        current_page_data = self._get_or_create_page(self.current_page)
        self.page_viewer.set_blocks(
            current_page_data.blocks if current_page_data else []
        )
        self.page_viewer.fit_to_view()

        self.page_viewer.selected_block_idx = block_idx
        self.page_viewer.selected_block_indices = []
        self.page_viewer._redraw_blocks()

        self._update_ui()

        # Показываем OCR preview
        current_page_data = self._get_or_create_page(self.current_page)
        if current_page_data and 0 <= block_idx < len(current_page_data.blocks):
            block = current_page_data.blocks[block_idx]
            self._show_ocr_preview(block.id)
