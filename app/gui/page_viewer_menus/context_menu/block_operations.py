"""Операции с блоками из контекстного меню"""
import logging

from rd_core.models import Block, BlockSource, BlockType

logger = logging.getLogger(__name__)


class BlockOperationsMixin:
    """Миксин для операций с блоками"""

    def _create_linked_blocks(self, blocks_data: list):
        """Создать связанные блоки для множественного выбора"""
        main_window = self.parent().window()
        if (
            not hasattr(main_window, "annotation_document")
            or not main_window.annotation_document
        ):
            return

        current_page = main_window.current_page
        if current_page >= len(main_window.annotation_document.pages):
            return

        page = main_window.annotation_document.pages[current_page]

        if hasattr(main_window, "_save_undo_state"):
            main_window._save_undo_state()

        saved_transform = self.transform()
        saved_zoom_factor = self.zoom_factor
        saved_h_scroll = self.horizontalScrollBar().value()
        saved_v_scroll = self.verticalScrollBar().value()

        source_blocks = []
        for data in blocks_data:
            block_idx = data["idx"]
            if 0 <= block_idx < len(page.blocks):
                source_blocks.append(page.blocks[block_idx])

        created_count = 0
        for source_block in source_blocks:
            target_type = (
                BlockType.IMAGE
                if source_block.block_type == BlockType.TEXT
                else BlockType.TEXT
            )

            new_block = Block.create(
                page_index=source_block.page_index,
                coords_px=source_block.coords_px,
                page_width=page.width,
                page_height=page.height,
                block_type=target_type,
                source=BlockSource.USER,
                shape_type=source_block.shape_type,
                polygon_points=source_block.polygon_points,
                linked_block_id=source_block.id,
            )

            source_block.linked_block_id = new_block.id
            current_idx = page.blocks.index(source_block)
            page.blocks.insert(current_idx + 1, new_block)
            created_count += 1

        main_window._render_current_page()

        self.setTransform(saved_transform)
        self.zoom_factor = saved_zoom_factor
        self.horizontalScrollBar().setValue(saved_h_scroll)
        self.verticalScrollBar().setValue(saved_v_scroll)

        if hasattr(main_window, "blocks_tree_manager"):
            main_window.blocks_tree_manager.update_blocks_tree()
        if hasattr(main_window, "_auto_save_annotation"):
            main_window._auto_save_annotation()

        from app.gui.toast import show_toast

        if created_count == 1:
            show_toast(main_window, "Создан связанный блок")
        else:
            show_toast(main_window, f"Создано связанных блоков: {created_count}")

    def _create_linked_block(self, source_block: Block, target_type: BlockType):
        """Создать связанный блок другого типа (legacy метод)"""
        main_window = self.parent().window()
        if (
            not hasattr(main_window, "annotation_document")
            or not main_window.annotation_document
        ):
            return

        current_page = main_window.current_page
        if current_page >= len(main_window.annotation_document.pages):
            return

        page = main_window.annotation_document.pages[current_page]

        if hasattr(main_window, "_save_undo_state"):
            main_window._save_undo_state()

        saved_transform = self.transform()
        saved_zoom_factor = self.zoom_factor
        saved_h_scroll = self.horizontalScrollBar().value()
        saved_v_scroll = self.verticalScrollBar().value()

        new_block = Block.create(
            page_index=source_block.page_index,
            coords_px=source_block.coords_px,
            page_width=page.width,
            page_height=page.height,
            block_type=target_type,
            source=BlockSource.USER,
            shape_type=source_block.shape_type,
            polygon_points=source_block.polygon_points,
            linked_block_id=source_block.id,
        )

        source_block.linked_block_id = new_block.id
        source_idx = page.blocks.index(source_block)
        page.blocks.insert(source_idx + 1, new_block)

        main_window._render_current_page()

        self.setTransform(saved_transform)
        self.zoom_factor = saved_zoom_factor
        self.horizontalScrollBar().setValue(saved_h_scroll)
        self.verticalScrollBar().setValue(saved_v_scroll)

        if hasattr(main_window, "blocks_tree_manager"):
            main_window.blocks_tree_manager.update_blocks_tree()
        if hasattr(main_window, "_auto_save_annotation"):
            main_window._auto_save_annotation()

        from app.gui.toast import show_toast

        show_toast(main_window, f"Создан связанный блок: {target_type.value}")

    def _delete_blocks(self, blocks_data: list):
        """Удалить несколько блоков"""
        if len(blocks_data) == 1:
            self.blockDeleted.emit(blocks_data[0]["idx"])
        else:
            indices = [b["idx"] for b in blocks_data]
            self.blocks_deleted.emit(indices)

    def _apply_type_to_blocks(self, blocks_data: list, block_type):
        """Применить тип к нескольким блокам"""
        main_window = self.parent().window()
        if (
            not hasattr(main_window, "annotation_document")
            or not main_window.annotation_document
        ):
            return

        current_page = main_window.current_page
        if current_page >= len(main_window.annotation_document.pages):
            return

        page = main_window.annotation_document.pages[current_page]

        saved_transform = self.transform()
        saved_zoom_factor = self.zoom_factor
        saved_h_scroll = self.horizontalScrollBar().value()
        saved_v_scroll = self.verticalScrollBar().value()

        for data in blocks_data:
            block_idx = data["idx"]
            if block_idx < len(page.blocks):
                page.blocks[block_idx].block_type = block_type

        main_window._render_current_page()

        self.setTransform(saved_transform)
        self.zoom_factor = saved_zoom_factor
        self.horizontalScrollBar().setValue(saved_h_scroll)
        self.verticalScrollBar().setValue(saved_v_scroll)

        if hasattr(main_window, "blocks_tree_manager"):
            main_window.blocks_tree_manager.update_blocks_tree()

    def _apply_category_to_blocks(
        self, blocks_data: list, category_id: str, category_code: str
    ):
        """Применить категорию к IMAGE блокам"""
        from PySide6.QtWidgets import QMessageBox

        main_window = self.parent().window()
        if (
            not hasattr(main_window, "annotation_document")
            or not main_window.annotation_document
        ):
            return

        current_page = main_window.current_page
        if current_page >= len(main_window.annotation_document.pages):
            return

        page = main_window.annotation_document.pages[current_page]

        if hasattr(main_window, "_save_undo_state"):
            main_window._save_undo_state()

        saved_transform = self.transform()
        saved_zoom_factor = self.zoom_factor
        saved_h_scroll = self.horizontalScrollBar().value()
        saved_v_scroll = self.verticalScrollBar().value()

        count = 0
        for data in blocks_data:
            block_idx = data["idx"]
            if block_idx < len(page.blocks):
                block = page.blocks[block_idx]
                if block.block_type == BlockType.IMAGE:
                    block.category_id = category_id
                    block.category_code = category_code
                    count += 1

        main_window._render_current_page()

        self.setTransform(saved_transform)
        self.zoom_factor = saved_zoom_factor
        self.horizontalScrollBar().setValue(saved_h_scroll)
        self.verticalScrollBar().setValue(saved_v_scroll)

        if hasattr(main_window, "blocks_tree_manager"):
            main_window.blocks_tree_manager.update_blocks_tree()
        if hasattr(main_window, "_auto_save_annotation"):
            main_window._auto_save_annotation()

        if count > 0:
            from app.gui.toast import show_toast

            cat_name = category_code or "default"
            show_toast(main_window, f"Категория установлена: {cat_name}")

    def _toggle_correction_flag(self, blocks_data: list):
        """Переключить флаг корректировки для выбранных блоков"""
        main_window = self.parent().window()
        if (
            not hasattr(main_window, "annotation_document")
            or not main_window.annotation_document
        ):
            return

        current_page = main_window.current_page
        if current_page >= len(main_window.annotation_document.pages):
            return

        page = main_window.annotation_document.pages[current_page]

        if hasattr(main_window, "_save_undo_state"):
            main_window._save_undo_state()

        # Если хоть один не помечен - пометить все
        any_not_correction = any(
            not page.blocks[b["idx"]].is_correction
            for b in blocks_data
            if 0 <= b["idx"] < len(page.blocks)
        )
        new_value = any_not_correction

        count = 0
        for data in blocks_data:
            idx = data["idx"]
            if 0 <= idx < len(page.blocks):
                page.blocks[idx].is_correction = new_value
                count += 1

        main_window._render_current_page()

        if hasattr(main_window, "blocks_tree_manager"):
            main_window.blocks_tree_manager.update_blocks_tree()
        if hasattr(main_window, "_auto_save_annotation"):
            main_window._auto_save_annotation()

        from app.gui.toast import show_toast

        if new_value:
            show_toast(main_window, f"Помечено для корректировки: {count}")
        else:
            show_toast(main_window, f"Пометка снята: {count}")
