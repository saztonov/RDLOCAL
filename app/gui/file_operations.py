"""
Миксин для работы с файлами (открытие, сохранение, загрузка)
"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from app.gui.file_auto_save import FileAutoSaveMixin
from app.gui.file_download import FileDownloadMixin
from rd_core.annotation_canonicalizer import (
    canonicalize_annotation_document,
    get_pdf_preview_page_sizes,
)
from app.annotation_db import AnnotationDBIO
from rd_core.annotation_io import AnnotationIO
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
        """Загрузить аннотацию из Supabase или мигрировать из старого JSON файла"""
        from app.gui.toast import show_toast

        # 1. Попытка загрузить из Supabase (основной источник)
        if self._current_node_id:
            try:
                loaded = AnnotationDBIO.load_from_db(self._current_node_id)
                if loaded:
                    self.annotation_document = loaded
                    self._canonicalize_loaded_annotation(pdf_path)
                    logger.info(f"Annotation loaded from Supabase: {self._current_node_id}")

                    # Инициализируем кеш аннотаций
                    from app.gui.annotation_cache import get_annotation_cache
                    cache = get_annotation_cache()
                    cache.set(self._current_node_id, self.annotation_document, pdf_path)

                    self._annotation_synced = True
                    self._update_has_annotation_flag(True)
                    return True
            except Exception as e:
                logger.debug(f"Supabase annotation load error: {e}")

        # 2. Проверить локальный JSON файл (миграция)
        ann_path = Path(pdf_path).parent / f"{Path(pdf_path).stem}_annotation.json"

        if ann_path.exists() and self._current_node_id:
            logger.info(f"Найден старый JSON файл: {ann_path}, миграция в Supabase...")
            loaded, result = AnnotationIO.load_and_migrate(str(ann_path))

            if not result.success:
                error_msg = "; ".join(result.errors)
                logger.error(f"Annotation load failed: {error_msg}")

                reply = QMessageBox.warning(
                    self,
                    "Ошибка аннотации",
                    f"Не удалось загрузить файл разметки:\n{error_msg}\n\n"
                    "Создать новый файл разметки?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )

                if reply == QMessageBox.Yes:
                    try:
                        ann_path.unlink()
                    except Exception:
                        pass
                    show_toast(self, "Создана новая разметка", success=True)
                    return False
                else:
                    return False

            if loaded:
                self.annotation_document = loaded
                self._canonicalize_loaded_annotation(pdf_path)

                # Мигрируем в Supabase
                success = AnnotationDBIO.save_to_db(
                    self.annotation_document, self._current_node_id
                )
                if success:
                    # Удаляем JSON файл после успешной миграции
                    try:
                        ann_path.unlink()
                        logger.info(f"JSON файл удалён после миграции: {ann_path}")
                    except Exception as e:
                        logger.warning(f"Не удалось удалить JSON файл: {e}")

                    show_toast(
                        self,
                        "Разметка мигрирована в Supabase",
                        duration=3000,
                        success=True,
                    )
                else:
                    logger.warning("Миграция в Supabase не удалась, данные только в памяти")

                # Инициализируем кеш аннотаций
                from app.gui.annotation_cache import get_annotation_cache
                cache = get_annotation_cache()
                cache.set(self._current_node_id, self.annotation_document, pdf_path)

                self._annotation_synced = True
                self._update_has_annotation_flag(True)
                return True

        # 3. Проверить R2 (для обратной совместимости — миграция)
        if r2_key and self._current_node_id:
            try:
                from pathlib import PurePosixPath
                from rd_core.r2_storage import R2Storage

                r2 = R2Storage()
                p = PurePosixPath(r2_key)
                ann_r2_key = str(p.parent / f"{p.stem}_annotation.json")

                # Скачать во временный файл
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
                    tmp_path = tmp.name

                success = r2.download_file(ann_r2_key, tmp_path)
                if success:
                    loaded, result = AnnotationIO.load_and_migrate(tmp_path)
                    if result.success and loaded:
                        self.annotation_document = loaded
                        self._canonicalize_loaded_annotation(pdf_path)

                        # Мигрируем в Supabase
                        AnnotationDBIO.save_to_db(
                            self.annotation_document, self._current_node_id
                        )

                        # Инициализируем кеш
                        from app.gui.annotation_cache import get_annotation_cache
                        cache = get_annotation_cache()
                        cache.set(self._current_node_id, self.annotation_document, pdf_path)

                        self._annotation_synced = True
                        self._update_has_annotation_flag(True)

                        show_toast(
                            self,
                            "Разметка мигрирована из R2 в Supabase",
                            duration=3000,
                            success=True,
                        )

                        logger.info(f"Annotation migrated from R2 to Supabase: {ann_r2_key}")

                # Удалить временный файл
                try:
                    Path(tmp_path).unlink()
                except Exception:
                    pass

                if success and loaded:
                    return True

            except Exception as e:
                logger.debug(f"R2 annotation migration error: {e}")

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

    def _open_pdf(self):
        """Открыть PDF файл через диалог"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Открыть PDF", "", "PDF Files (*.pdf)"
        )
        if file_path:
            self._open_pdf_file(file_path)

    def _open_pdf_file(self, pdf_path: str, r2_key: str = ""):
        """Открыть PDF файл напрямую"""
        # Сохранить изменения предыдущего файла
        self._flush_pending_save()

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

        # Переключить логи в папку PDF
        from app.logging_manager import get_logging_manager
        get_logging_manager().switch_to_pdf_folder(pdf_path)

        # Пробуем загрузить существующую разметку
        if not self._load_annotation_if_exists(pdf_path, r2_key):
            # Создаём пустой документ аннотации
            self.annotation_document = self._create_empty_annotation(pdf_path)

        self._canonicalize_loaded_annotation(pdf_path)

        # Рендерим первую страницу
        self._render_current_page()
        self._update_ui()

        # Обновляем дерево групп
        if hasattr(self, "_update_groups_tree"):
            self._update_groups_tree()

        # Загружаем OCR result file для preview
        if hasattr(self, "_load_ocr_result_file"):
            self._load_ocr_result_file()

        # Обновляем статистику OCR
        if hasattr(self, "remote_ocr_panel") and self.remote_ocr_panel:
            self.remote_ocr_panel.update_ocr_stats()

        # Обновляем заголовок
        self.setWindowTitle(f"{__product__} - {Path(pdf_path).name}")

    def _save_annotation(self):
        """Сохранить разметку в Supabase (или в JSON через диалог)"""
        if not self.annotation_document:
            return

        from app.gui.toast import show_toast

        # Если есть node_id — сохраняем в Supabase
        if self._current_node_id:
            success = AnnotationDBIO.save_to_db(
                self.annotation_document, self._current_node_id
            )
            if success:
                show_toast(self, "Разметка сохранена в Supabase", success=True)
                self._update_has_annotation_flag(True)
            else:
                show_toast(self, "Ошибка сохранения в Supabase")
            return

        # Fallback: сохранение в JSON файл (для локального использования без дерева)
        default_path = ""
        if hasattr(self, "_current_pdf_path") and self._current_pdf_path:
            pdf_path = Path(self._current_pdf_path)
            default_path = str(pdf_path.parent / f"{pdf_path.stem}_annotation.json")

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить разметку", default_path, "JSON Files (*.json)"
        )
        if file_path:
            AnnotationIO.save_annotation(self.annotation_document, file_path)
            show_toast(self, "Разметка сохранена")

    def _load_annotation(self):
        """Загрузить разметку из JSON и мигрировать в Supabase"""
        from app.gui.toast import show_toast

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Загрузить разметку", "", "JSON Files (*.json)"
        )
        if not file_path:
            return

        loaded_doc, result = AnnotationIO.load_and_migrate(file_path)

        if not result.success:
            error_msg = "; ".join(result.errors)
            QMessageBox.warning(
                self, "Ошибка", f"Не удалось загрузить разметку:\n{error_msg}"
            )
            return

        if loaded_doc:
            # Поддержка относительного пути
            try:
                pdf_path_obj = Path(loaded_doc.pdf_path)
                if not pdf_path_obj.is_absolute():
                    resolved = (Path(file_path).parent / pdf_path_obj).resolve()
                    loaded_doc.pdf_path = str(resolved)
            except Exception:
                pass

            self.annotation_document = loaded_doc
            pdf_path = loaded_doc.pdf_path
            if Path(pdf_path).exists():
                self._open_pdf_file(pdf_path)
                self.annotation_document = loaded_doc
                self._canonicalize_loaded_annotation(pdf_path)

                if self._current_node_id:
                    from app.gui.annotation_cache import get_annotation_cache

                    cache = get_annotation_cache()
                    cache.set(self._current_node_id, self.annotation_document, pdf_path)

                self._render_current_page()

            # Сохраняем в Supabase если есть node_id
            if self._current_node_id:
                AnnotationDBIO.save_to_db(loaded_doc, self._current_node_id)
                show_toast(self, "Разметка загружена и сохранена в Supabase", success=True)
            else:
                show_toast(self, "Разметка загружена", success=True)

            self.blocks_tree_manager.update_blocks_tree()

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
            if hasattr(self, "_update_groups_tree"):
                self._update_groups_tree()

            logger.info(f"Аннотация обновлена из Supabase: {self._current_node_id}")
            show_toast(self, "Аннотация обновлена", success=True)

        except Exception as e:
            logger.error(f"Ошибка обновления аннотации: {e}")
