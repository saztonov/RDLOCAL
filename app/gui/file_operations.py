"""
Миксин для работы с файлами (открытие, сохранение, загрузка)
"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from app.gui.file_auto_save import FileAutoSaveMixin
from app.gui.file_download import FileDownloadMixin
from rd_core.annotation_canonicalizer import (
    canonicalize_annotation_document,
    get_pdf_preview_page_sizes,
)
from app.annotation_db import AnnotationDBIO
from rd_core.models import Document, Page
from rd_core.pdf_utils import PDFDocument

logger = logging.getLogger(__name__)

# Импорт метаданных продукта
try:
    from _metadata import __product__
except ImportError:
    __product__ = "Core Structure"


class FileOperationsMixin(FileAutoSaveMixin, FileDownloadMixin):
    """Миксин для операций с файлами"""

    def _canonicalize_loaded_annotation(self, pdf_path: str):
        """Align annotation geometry with the actual preview sizes of the opened PDF."""
        if not self.annotation_document or not self.pdf_document:
            return

        try:
            page_sizes = get_pdf_preview_page_sizes(self.pdf_document)
            prefer_coords_px = bool(
                getattr(self.annotation_document, "_prefer_coords_px", False)
            )
            result = canonicalize_annotation_document(
                self.annotation_document,
                pdf_path=pdf_path,
                pdf_page_sizes=page_sizes,
                prefer_coords_px=prefer_coords_px,
            )
            if hasattr(self.annotation_document, "_prefer_coords_px"):
                delattr(self.annotation_document, "_prefer_coords_px")

            if result.changed:
                logger.info(
                    "Annotation canonicalized for %s using %s strategy",
                    pdf_path,
                    result.strategy,
                )
        except Exception as e:
            logger.warning(f"Annotation canonicalization failed for {pdf_path}: {e}")

    def _update_has_annotation_flag(self, has_annotation: bool):
        """Обновить флаг has_annotation в узле дерева"""
        if not hasattr(self, "_current_node_id") or not self._current_node_id:
            return

        try:

            from app.tree_client import TreeClient
            from rd_core.pdf_status import calculate_pdf_status
            from rd_core.r2_storage import R2Storage

            client = TreeClient()
            node = client.get_node(self._current_node_id)
            if node:
                attrs = node.attributes.copy()
                attrs["has_annotation"] = has_annotation
                client.update_node(self._current_node_id, attributes=attrs)

                # Обновляем статус PDF в БД
                if node.node_type.value == "document" and self._current_r2_key:
                    r2 = R2Storage()
                    status, message = calculate_pdf_status(
                        r2, self._current_node_id, self._current_r2_key,
                        client=client,
                    )
                    client.update_pdf_status(
                        self._current_node_id, status.value, message
                    )

                    # Обновляем только конкретный узел в дереве
                    if hasattr(self, "project_tree") and self.project_tree:
                        item = self.project_tree._node_map.get(self._current_node_id)
                        if item:
                            node.pdf_status = status.value
                            node.pdf_status_message = message

                            from app.gui.tree_node_operations import NODE_ICONS

                            icon = NODE_ICONS.get(node.node_type, "📄")
                            status_icon = self.project_tree._get_pdf_status_icon(
                                status.value
                            )
                            lock_icon = "🔒" if node.is_locked else ""
                            version_tag = (
                                f"[v{node.version}]" if node.version else "[v1]"
                            )

                            display_name = (
                                f"{icon} {node.name} {lock_icon} {status_icon}".strip()
                            )
                            item.setText(0, display_name)
                            item.setData(0, Qt.UserRole + 1, version_tag)
                            if message:
                                item.setToolTip(0, message)
        except Exception as e:
            logger.debug(f"Update has_annotation failed: {e}")

    def _load_annotation_if_exists(self, pdf_path: str, r2_key: str = ""):
        """Загрузить аннотацию из Supabase"""
        if self._current_node_id:
            try:
                loaded = AnnotationDBIO.load_from_db(self._current_node_id)
                if loaded:
                    self.annotation_document = loaded
                    self._canonicalize_loaded_annotation(pdf_path)
                    logger.info(f"Annotation loaded from Supabase: {self._current_node_id}")

                    from app.gui.annotation_cache import get_annotation_cache
                    cache = get_annotation_cache()
                    cache.set(self._current_node_id, self.annotation_document, pdf_path)

                    self._annotation_synced = True
                    self._update_has_annotation_flag(True)
                    return True
            except Exception as e:
                logger.debug(f"Supabase annotation load error: {e}")

        return False

    def _create_empty_annotation(self, pdf_path: str) -> Document:
        """Создать пустой документ аннотации со страницами"""
        doc = Document(pdf_path=pdf_path)
        for page_num in range(self.pdf_document.page_count):
            if page_num in self.page_images:
                img = self.page_images[page_num]
                page = Page(page_number=page_num, width=img.width, height=img.height)
            else:
                dims = self.pdf_document.get_page_dimensions(page_num)
                if dims:
                    page = Page(page_number=page_num, width=dims[0], height=dims[1])
                else:
                    page = Page(page_number=page_num, width=595, height=842)
            doc.pages.append(page)
        return doc

    def _open_pdf_file(self, pdf_path: str, r2_key: str = ""):
        """Открыть PDF файл напрямую"""
        # Сохранить изменения предыдущего файла
        self._flush_pending_save()

        # Удалить temp-сессию предыдущего tree-документа
        old_origin = getattr(self, "_current_document_origin", "local")
        old_temp_dir = getattr(self, "_current_temp_dir", None)
        if old_origin == "tree_temp" and old_temp_dir:
            from app.gui.temp_session import get_temp_session_manager

            get_temp_session_manager().cleanup(old_temp_dir)
            self._current_temp_dir = None
            self._current_document_origin = "local"

        if self.pdf_document:
            self.pdf_document.close()

        self.page_images.clear()
        self._page_images_order.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()

        # Сброс флага синхронизации для нового файла
        self._annotation_synced = False

        self.pdf_document = PDFDocument(pdf_path)
        if not self.pdf_document.open() or self.pdf_document.page_count == 0:
            QMessageBox.warning(self, "Ошибка", "PDF файл пустой или повреждён")
            return

        self.current_page = 0
        self._current_pdf_path = pdf_path
        self._current_r2_key = r2_key

        # Переключить логи: для tree-документов НЕ в temp (логи потеряются при cleanup)
        from app.logging_manager import get_logging_manager

        if getattr(self, "_current_document_origin", "local") != "tree_temp":
            get_logging_manager().switch_to_pdf_folder(pdf_path)
        else:
            get_logging_manager().switch_to_projects_folder()

        # Пробуем загрузить существующую разметку
        if not self._load_annotation_if_exists(pdf_path, r2_key):
            # Создаём пустой документ аннотации
            self.annotation_document = self._create_empty_annotation(pdf_path)

        self._canonicalize_loaded_annotation(pdf_path)

        # Рендерим первую страницу
        self._render_current_page()
        self._update_ui()

        # Загружаем OCR preview данные из аннотации
        if hasattr(self, "_load_ocr_preview_data"):
            self._load_ocr_preview_data()

        # Обновляем статистику OCR
        if hasattr(self, "remote_ocr_panel") and self.remote_ocr_panel:
            self.remote_ocr_panel.update_ocr_stats()

        # Обновляем заголовок
        self.setWindowTitle(f"{__product__} - {Path(pdf_path).name}")

    def _save_annotation(self):
        """Сохранить разметку в Supabase"""
        if not self.annotation_document:
            return

        from app.gui.toast import show_toast

        if not self._current_node_id:
            show_toast(self, "Документ не привязан к дереву проектов")
            return

        success = AnnotationDBIO.save_to_db(
            self.annotation_document, self._current_node_id
        )
        if success:
            show_toast(self, "Разметка сохранена в Supabase", success=True)
            self._update_has_annotation_flag(True)
        else:
            show_toast(self, "Ошибка сохранения в Supabase")

    def _on_annotation_replaced(self, r2_key: str):
        """Обработчик замены аннотации в дереве проектов"""
        from app.gui.toast import show_toast

        # Проверяем совпадает ли r2_key с текущим открытым документом
        if not hasattr(self, "_current_r2_key") or self._current_r2_key != r2_key:
            return

        if not self._current_pdf_path or not self._current_node_id:
            return

        try:
            # Загружаем из Supabase
            loaded_doc = AnnotationDBIO.load_from_db(self._current_node_id)
            if not loaded_doc:
                logger.warning(f"Не удалось загрузить аннотацию из Supabase: {self._current_node_id}")
                return

            # Заменяем текущую аннотацию
            self.annotation_document = loaded_doc
            self._canonicalize_loaded_annotation(self._current_pdf_path)
            self._annotation_synced = True

            from app.gui.annotation_cache import get_annotation_cache

            cache = get_annotation_cache()
            cache.set(
                self._current_node_id,
                self.annotation_document,
                self._current_pdf_path,
            )

            # Обновляем отображение
            self._render_current_page()
            if hasattr(self, "blocks_tree_manager") and self.blocks_tree_manager:
                self.blocks_tree_manager.update_blocks_tree()
            logger.info(f"Аннотация обновлена из Supabase: {self._current_node_id}")
            show_toast(self, "Аннотация обновлена", success=True)

        except Exception as e:
            logger.error(f"Ошибка обновления аннотации: {e}")
