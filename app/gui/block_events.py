"""Миксин для обработки событий клавиатуры блоков"""

import copy
import logging

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence

from rd_core.annotation_canonicalizer import sync_block_to_page
from rd_core.models.enums import BlockType

logger = logging.getLogger(__name__)


class BlockEventsMixin:
    """Миксин для обработки событий клавиатуры"""

    def _copy_selected_blocks(self):
        """Копировать выбранные блоки в буфер обмена"""
        if not self.annotation_document:
            return

        current_page_data = self._get_or_create_page(self.current_page)
        if not current_page_data:
            return

        # Собираем выбранные блоки
        blocks_to_copy = []
        if self.page_viewer.selected_block_indices:
            # Копируем множественный выбор
            for idx in self.page_viewer.selected_block_indices:
                if 0 <= idx < len(current_page_data.blocks):
                    blocks_to_copy.append(current_page_data.blocks[idx])
        elif self.page_viewer.selected_block_idx is not None:
            # Копируем единственный выбранный блок
            idx = self.page_viewer.selected_block_idx
            if 0 <= idx < len(current_page_data.blocks):
                blocks_to_copy.append(current_page_data.blocks[idx])

        if blocks_to_copy:
            # Делаем глубокую копию блоков
            self._blocks_clipboard = copy.deepcopy(blocks_to_copy)
            logger.info(f"Скопировано блоков: {len(self._blocks_clipboard)}")
            from app.gui.toast import show_toast
            show_toast(self, f"📋 Скопировано блоков: {len(self._blocks_clipboard)}")

    def _paste_blocks(self):
        """Вставить блоки из буфера обмена на текущую страницу"""
        if not hasattr(self, "_blocks_clipboard") or not self._blocks_clipboard:
            return

        if not self.annotation_document:
            return

        # Проверка блокировки документа
        if self._check_document_locked_for_editing():
            return

        current_page_data = self._get_or_create_page(self.current_page)
        if not current_page_data:
            return

        self._save_undo_state()

        # Вставляем блоки
        pasted_count = 0
        skipped_count = 0
        for block in self._blocks_clipboard:
            # Создаем новую копию блока с новым ID
            new_block = copy.deepcopy(block)
            new_block.id = new_block.generate_id()
            new_block.page_index = self.current_page
            
            # Очищаем связанные данные (ocr_text, image_file и т.д.)
            new_block.ocr_text = None
            new_block.image_file = None
            new_block.linked_block_id = None

            # Проверка: на странице может быть только один штамп
            if new_block.block_type == BlockType.STAMP and self._has_stamp_on_page(current_page_data):
                logger.warning("Пропускаем вставку штампа - на странице уже есть штамп")
                skipped_count += 1
                continue

            # Проверка: блок должен попадать на границы листа
            x1, y1, x2, y2 = new_block.coords_px
            page_width = current_page_data.width
            page_height = current_page_data.height
            
            # Проверяем, что блок хотя бы частично попадает на страницу
            if x2 <= 0 or y2 <= 0 or x1 >= page_width or y1 >= page_height:
                logger.warning(f"Пропускаем блок за границей листа: coords={new_block.coords_px}, page_size={page_width}x{page_height}")
                skipped_count += 1
                continue

            sync_block_to_page(
                new_block,
                page_width=page_width,
                page_height=page_height,
                prefer_coords_px=True,
            )
            current_page_data.blocks.append(new_block)
            pasted_count += 1

        if pasted_count > 0:
            self.page_viewer.set_blocks(current_page_data.blocks)
            self.blocks_tree_manager.update_blocks_tree()
            self._auto_save_annotation()
            
            logger.info(f"Вставлено блоков: {pasted_count}, пропущено: {skipped_count}")
            from app.gui.toast import show_toast
            if skipped_count > 0:
                show_toast(self, f"✅ Вставлено: {pasted_count}, пропущено: {skipped_count}")
            else:
                show_toast(self, f"✅ Вставлено блоков: {pasted_count}")
        elif skipped_count > 0:
            from app.gui.toast import show_toast
            show_toast(self, f"⚠️ Все блоки ({skipped_count}) за границами листа")

    def keyPressEvent(self, event):
        """Обработка нажатия клавиш"""
        # В режиме read_only блокируем все команды редактирования
        is_read_only = hasattr(self, "page_viewer") and self.page_viewer.read_only

        # Получаем настроенные горячие клавиши
        from app.gui.hotkeys_dialog import HotkeysDialog
        
        key_sequence = event.keyCombination()
        pressed_key = QKeySequence(key_sequence).toString()

        # Проверяем соответствие нажатой клавиши настроенным горячим клавишам
        if pressed_key == HotkeysDialog.get_hotkey("undo"):
            if not is_read_only:
                self._undo()
            return
        elif pressed_key == HotkeysDialog.get_hotkey("redo"):
            if not is_read_only:
                self._redo()
            return
        elif pressed_key == HotkeysDialog.get_hotkey("text_block"):
            if hasattr(self, "text_action"):
                self.text_action.setChecked(True)
            return
        elif pressed_key == HotkeysDialog.get_hotkey("image_block"):
            if hasattr(self, "image_action"):
                self.image_action.setChecked(True)
            return
        elif pressed_key == HotkeysDialog.get_hotkey("stamp_block"):
            if hasattr(self, "stamp_action"):
                self.stamp_action.setChecked(True)
            return
        elif pressed_key == HotkeysDialog.get_hotkey("toggle_shape"):
            if hasattr(self, "rectangle_action") and hasattr(self, "polygon_action"):
                if self.rectangle_action.isChecked():
                    self.polygon_action.setChecked(True)
                    self._on_shape_type_changed(self.polygon_action)
                else:
                    self.rectangle_action.setChecked(True)
                    self._on_shape_type_changed(self.rectangle_action)
            return
        elif pressed_key == HotkeysDialog.get_hotkey("cycle_block_type"):
            if hasattr(self, "text_action") and hasattr(self, "image_action") and hasattr(self, "stamp_action"):
                if self.text_action.isChecked():
                    self.image_action.setChecked(True)
                elif self.image_action.isChecked():
                    self.stamp_action.setChecked(True)
                else:
                    self.text_action.setChecked(True)
            return
        elif pressed_key == HotkeysDialog.get_hotkey("copy_blocks"):
            self._copy_selected_blocks()
            return
        elif pressed_key == HotkeysDialog.get_hotkey("paste_blocks"):
            if not is_read_only:
                self._paste_blocks()
            return
        elif event.key() == Qt.Key_Left:
            self._prev_page()
            return
        elif event.key() == Qt.Key_Right:
            self._next_page()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        """Обработка событий для деревьев блоков"""
        if hasattr(self, "blocks_tree") and obj is self.blocks_tree:
            if event.type() == QEvent.KeyPress and isinstance(event, QKeyEvent):
                if event.key() == Qt.Key_Delete:
                    # В режиме read_only не разрешаем удаление
                    if hasattr(self, "page_viewer") and self.page_viewer.read_only:
                        return True

                    current_item = obj.currentItem()
                    if current_item:
                        data = current_item.data(0, Qt.UserRole)
                        if (
                            data
                            and isinstance(data, dict)
                            and data.get("type") == "block"
                        ):
                            page_num = data["page"]
                            block_idx = data["idx"]

                            self.current_page = page_num
                            self.navigation_manager.load_page_image(self.current_page)

                            current_page_data = self._get_or_create_page(
                                self.current_page
                            )
                            self.page_viewer.set_blocks(
                                current_page_data.blocks if current_page_data else []
                            )

                            self._on_block_deleted(block_idx)
                            self._update_ui()
                            return True

        return super().eventFilter(obj, event)
