"""Миксин для создания блоков (рисование)"""

import logging
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from rd_core.models import Block, BlockSource, BlockType, ShapeType

logger = logging.getLogger(__name__)


class BlockDrawMixin:
    """Миксин для создания блоков через рисование"""

    def _on_block_drawn(self, x1: int, y1: int, x2: int, y2: int):
        """Обработка завершения рисования блока (прямоугольник)"""
        if not self.annotation_document:
            return

        # Проверка блокировки документа
        if self._check_document_locked_for_editing():
            return

        self._save_undo_state()

        checked_action = self.block_type_group.checkedAction()
        action_data = checked_action.data() if checked_action else {}
        block_type = (
            action_data.get("block_type", BlockType.TEXT)
            if isinstance(action_data, dict)
            else BlockType.TEXT
        )

        current_page_data = self._get_or_create_page(self.current_page)
        if not current_page_data:
            return

        # Проверка: на странице может быть только один штамп
        if block_type == BlockType.STAMP and self._has_stamp_on_page(current_page_data):
            QMessageBox.warning(self, "Ошибка", "На листе может быть только один штамп")
            return

        block = Block.create(
            page_index=self.current_page,
            coords_px=(x1, y1, x2, y2),
            page_width=current_page_data.width,
            page_height=current_page_data.height,
            block_type=block_type,
            source=BlockSource.USER,
            shape_type=ShapeType.RECTANGLE,
        )

        # Автопометка: если документ уже распознан, пометить для корректировки
        if self._is_document_recognized():
            block.is_correction = True

        logger.debug(
            f"Block created: {block.id} coords_px={block.coords_px} "
            f"page_size={current_page_data.width}x{current_page_data.height}"
        )

        current_page_data.blocks.append(block)
        new_block_idx = len(current_page_data.blocks) - 1

        # Автоматически выбираем созданный блок для возможности изменения размера
        self.page_viewer.selected_block_idx = new_block_idx
        self.page_viewer.set_blocks(current_page_data.blocks)

        # Отложенное обновление дерева (не блокирует UI)
        QTimer.singleShot(0, self.blocks_tree_manager.update_blocks_tree)
        self._auto_save_annotation()

    def _on_polygon_drawn(self, points: list):
        """Обработка завершения рисования полигона"""
        if not self.annotation_document or not points or len(points) < 3:
            return

        # Проверка блокировки документа
        if self._check_document_locked_for_editing():
            return

        self._save_undo_state()

        checked_action = self.block_type_group.checkedAction()
        action_data = checked_action.data() if checked_action else {}
        block_type = (
            action_data.get("block_type", BlockType.TEXT)
            if isinstance(action_data, dict)
            else BlockType.TEXT
        )

        current_page_data = self._get_or_create_page(self.current_page)
        if not current_page_data:
            return

        # Проверка: на странице может быть только один штамп
        if block_type == BlockType.STAMP and self._has_stamp_on_page(current_page_data):
            QMessageBox.warning(self, "Ошибка", "На листе может быть только один штамп")
            return

        # Вычисляем bounding box для coords_px
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)

        block = Block.create(
            page_index=self.current_page,
            coords_px=(x1, y1, x2, y2),
            page_width=current_page_data.width,
            page_height=current_page_data.height,
            block_type=block_type,
            source=BlockSource.USER,
            shape_type=ShapeType.POLYGON,
            polygon_points=points,
        )

        # Автопометка: если документ уже распознан, пометить для корректировки
        if self._is_document_recognized():
            block.is_correction = True

        logger.debug(
            f"Polygon created: {block.id} bbox={block.coords_px} vertices={len(points)}"
        )

        current_page_data.blocks.append(block)
        new_block_idx = len(current_page_data.blocks) - 1

        # Автоматически выбираем созданный блок
        self.page_viewer.selected_block_idx = new_block_idx
        self.page_viewer.set_blocks(current_page_data.blocks)

        # Отложенное обновление дерева (не блокирует UI)
        QTimer.singleShot(0, self.blocks_tree_manager.update_blocks_tree)
        self._auto_save_annotation()

    def _is_document_recognized(self) -> bool:
        """Проверить, был ли документ уже распознан (есть result.json)"""
        pdf_path = getattr(self, "_current_pdf_path", None)
        if not pdf_path:
            return False

        result_path = Path(pdf_path).parent / f"{Path(pdf_path).stem}_result.json"
        return result_path.exists()
