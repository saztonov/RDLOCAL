"""Миксин для валидации операций с блоками"""

from PySide6.QtWidgets import QMessageBox

from rd_core.models.enums import BlockType


class BlockValidationMixin:
    """Миксин для валидации документа и блоков"""

    def _check_document_locked_for_editing(self) -> bool:
        """
        Проверить заблокирован ли текущий документ.
        Если заблокирован - показать предупреждение и вернуть True.
        Если не заблокирован - вернуть False.
        """
        if hasattr(self, "_current_node_locked") and self._current_node_locked:
            QMessageBox.warning(
                self,
                "Документ заблокирован",
                "Этот документ заблокирован от изменений.\nСначала снимите блокировку.",
            )
            return True
        return False

    def _has_stamp_on_page(self, page_data, exclude_block_id: str = None) -> bool:
        """Проверить, есть ли уже штамп на странице"""
        if not page_data or not page_data.blocks:
            return False
        for block in page_data.blocks:
            if exclude_block_id and block.id == exclude_block_id:
                continue
            if block.block_type == BlockType.STAMP:
                return True
        return False

