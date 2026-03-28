"""
BlocksTreeManager для MainWindow
Управление деревом блоков
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QTreeWidget,
    QTreeWidgetItem,
)

from rd_core.models import BlockType
from .context_menu_mixin import ContextMenuMixin
from ..view_state_manager import ViewStateManager

logger = logging.getLogger(__name__)


class BlocksTreeManager(ContextMenuMixin):
    """Управление деревом блоков"""

    def __init__(self, parent, blocks_tree: QTreeWidget):
        self.parent = parent
        self.blocks_tree = blocks_tree
        self._view_state_manager = None

    @property
    def view_state(self) -> ViewStateManager:
        """Ленивая инициализация ViewStateManager."""
        if self._view_state_manager is None:
            self._view_state_manager = ViewStateManager(self.parent.page_viewer)
        return self._view_state_manager

    _CATEGORY_NAMES = {"default": "По умолчанию", "stamp": "Штамп"}

    def _get_category_name(self, category_id: str, category_code: str = None) -> str:
        """Получить название категории по коду или ID"""
        code = category_code or category_id or ""
        return self._CATEGORY_NAMES.get(code, code)

    def update_blocks_tree(self):
        """Обновить дерево блоков со всех страниц, группировка по страницам"""
        self.blocks_tree.clear()

        if not self.parent.annotation_document:
            return

        for page in self.parent.annotation_document.pages:
            page_num = page.page_number
            if not page.blocks:
                continue

            page_item = QTreeWidgetItem(self.blocks_tree)
            page_item.setText(0, f"Страница {page_num + 1}")
            page_item.setData(0, Qt.UserRole, {"type": "page", "page": page_num})
            page_item.setExpanded(page_num == self.parent.current_page)

            for idx, block in enumerate(page.blocks):
                block_item = QTreeWidgetItem(page_item)
                # Добавляем индикаторы
                indicators = ""
                # Индикатор связи
                if block.linked_block_id:
                    indicators += " 🔗"
                block_item.setText(0, f"Блок {idx + 1}{indicators}")
                block_item.setText(1, block.block_type.value)
                # Колонка Категория (для IMAGE блоков)
                cat_name = (
                    self._get_category_name(block.category_id, block.category_code)
                    if block.block_type == BlockType.IMAGE
                    else ""
                )
                block_item.setText(2, cat_name)
                # Tooltip
                tooltip_parts = []
                if block.linked_block_id:
                    tooltip_parts.append("🔗 Связан с другим блоком")
                if tooltip_parts:
                    block_item.setToolTip(0, "\n".join(tooltip_parts))
                block_item.setData(
                    0, Qt.UserRole, {"type": "block", "page": page_num, "idx": idx}
                )
                block_item.setData(0, Qt.UserRole + 1, idx)

    def select_block_in_tree(self, block_idx: int):
        """Выделить блок в дереве"""
        for i in range(self.blocks_tree.topLevelItemCount()):
            page_item = self.blocks_tree.topLevelItem(i)
            page_data = page_item.data(0, Qt.UserRole)
            if not page_data or page_data.get("page") != self.parent.current_page:
                continue

            for j in range(page_item.childCount()):
                block_item = page_item.child(j)
                data = block_item.data(0, Qt.UserRole)
                if (
                    data
                    and data.get("idx") == block_idx
                    and data.get("page") == self.parent.current_page
                ):
                    self.blocks_tree.setCurrentItem(block_item)
                    return

    def select_blocks_in_tree(self, block_indices: list):
        """Выделить несколько блоков в дереве"""
        # Очищаем текущее выделение
        self.blocks_tree.clearSelection()

        for i in range(self.blocks_tree.topLevelItemCount()):
            page_item = self.blocks_tree.topLevelItem(i)
            page_data = page_item.data(0, Qt.UserRole)
            if not page_data or page_data.get("page") != self.parent.current_page:
                continue

            for j in range(page_item.childCount()):
                block_item = page_item.child(j)
                data = block_item.data(0, Qt.UserRole)
                if (
                    data
                    and data.get("idx") in block_indices
                    and data.get("page") == self.parent.current_page
                ):
                    block_item.setSelected(True)

    def _get_block(self, data: dict):
        """Получить блок по данным"""
        if not self.parent.annotation_document:
            return None

        page_num = data["page"]
        block_idx = data["idx"]

        if page_num < len(self.parent.annotation_document.pages):
            page = self.parent.annotation_document.pages[page_num]
            if block_idx < len(page.blocks):
                return page.blocks[block_idx]
        return None

    def apply_type_to_blocks(self, blocks_data: list, block_type: BlockType):
        """Применить тип к нескольким блокам"""
        if not self.parent.annotation_document:
            return

        with self.view_state.preserve():
            for data in blocks_data:
                page_num = data["page"]
                block_idx = data["idx"]

                if page_num < len(self.parent.annotation_document.pages):
                    page = self.parent.annotation_document.pages[page_num]
                    if block_idx < len(page.blocks):
                        page.blocks[block_idx].block_type = block_type

            self.parent._render_current_page()

        self.update_blocks_tree()
