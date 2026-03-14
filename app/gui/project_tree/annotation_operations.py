"""Операции с аннотациями документов в дереве проектов"""
import json
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from app.annotation_db import AnnotationDBIO
from app.gui.folder_settings_dialog import get_projects_dir
from app.tree_client import NodeType, TreeNode
from rd_core.annotation_canonicalizer import (
    canonicalize_annotation_document,
    check_annotation_compatibility,
    get_pdf_preview_page_sizes,
    source_pdf_looks_related,
)
from rd_core.annotation_io import AnnotationIO
from rd_core.models import Document
from rd_core.pdf_utils import PDFDocument
from rd_core.r2_storage import R2Storage

if TYPE_CHECKING:
    from app.gui.project_tree.widget import ProjectTreeWidget

logger = logging.getLogger(__name__)


class AnnotationOperations:
    """
    Операции с аннотациями документов.

    Отвечает за:
    - Копирование/вставка аннотаций (через Supabase)
    - Загрузка аннотаций из файла
    - Определение и назначение штампов
    """

    def __init__(self, widget: "ProjectTreeWidget"):
        self._widget = widget
        self._copied_annotation: Dict = {}  # {"data": dict, "source_node_id": str}

    @property
    def has_copied(self) -> bool:
        """Есть ли скопированная аннотация"""
        return bool(self._copied_annotation)

    def copy_annotation(self, node: TreeNode) -> None:
        """Скопировать аннотацию документа из Supabase в буфер"""
        try:
            document = AnnotationDBIO.load_from_db(node.id)

            if document:
                self._copied_annotation = {
                    "data": document.to_dict(),
                    "source_node_id": node.id,
                    "prefer_coords_px": bool(
                        getattr(document, "_prefer_coords_px", False)
                    ),
                }
                self._widget.status_label.setText("📋 Аннотация скопирована")
                logger.info(f"Annotation copied from node {node.id}")
            else:
                QMessageBox.warning(
                    self._widget, "Ошибка", "Аннотация не найдена в базе данных"
                )
        except Exception as e:
            logger.error(f"Copy annotation failed: {e}")
            QMessageBox.critical(self._widget, "Ошибка", f"Ошибка копирования: {e}")

    def _get_cache_pdf_path(self, node: TreeNode) -> Optional[Path]:
        """Вернуть стабильный путь к PDF в локальном кеше проекта."""
        projects_dir = get_projects_dir()
        r2_key = node.attributes.get("r2_key", "")
        if not projects_dir or not r2_key:
            return None

        rel_path = r2_key[len("tree_docs/") :] if r2_key.startswith("tree_docs/") else r2_key
        return Path(projects_dir) / "cache" / rel_path

    def _get_target_pdf_context(
        self, node: TreeNode
    ) -> tuple[Optional[str], Optional[list[tuple[int, int]]], Optional[str]]:
        """Получить путь и реальные preview-размеры целевого PDF."""
        main_window = self._widget.window()
        current_node_id = getattr(main_window, "_current_node_id", None)
        current_pdf_path = getattr(main_window, "_current_pdf_path", "")
        current_pdf_document = getattr(main_window, "pdf_document", None)

        if (
            current_node_id == node.id
            and current_pdf_path
            and Path(current_pdf_path).exists()
            and current_pdf_document
            and getattr(current_pdf_document, "doc", None)
        ):
            try:
                return (
                    current_pdf_path,
                    get_pdf_preview_page_sizes(current_pdf_document),
                    None,
                )
            except Exception as e:
                logger.warning(
                    "Failed to read page sizes from opened PDF %s: %s",
                    current_pdf_path,
                    e,
                )

        cache_pdf_path = self._get_cache_pdf_path(node)
        if cache_pdf_path and cache_pdf_path.exists():
            pdf_document = PDFDocument(str(cache_pdf_path))
            try:
                if not pdf_document.open():
                    return None, None, f"Не удалось открыть PDF: {cache_pdf_path.name}"
                return str(cache_pdf_path), get_pdf_preview_page_sizes(pdf_document), None
            finally:
                pdf_document.close()

        r2_key = node.attributes.get("r2_key", "")
        if not r2_key:
            return None, None, "У документа нет PDF в R2."

        if not cache_pdf_path:
            return None, None, "Не настроена папка проектов для доступа к кешу PDF."

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = tmp.name

            if not R2Storage().download_file(r2_key, tmp_path):
                return None, None, "Не удалось скачать целевой PDF для проверки."

            pdf_document = PDFDocument(tmp_path)
            try:
                if not pdf_document.open():
                    return None, None, "Не удалось открыть скачанный PDF."
                page_sizes = get_pdf_preview_page_sizes(pdf_document)
            finally:
                pdf_document.close()

            return str(cache_pdf_path), page_sizes, None
        except Exception as e:
            logger.error("Failed to prepare target PDF context for %s: %s", node.id, e)
            return None, None, f"Не удалось подготовить PDF для проверки: {e}"
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink()
                except Exception:
                    pass

    def _validate_and_prepare_annotation(
        self, node: TreeNode, document: Document
    ) -> tuple[Optional[Document], Optional[str]]:
        """Проверить совместимость аннотации и привязать её к целевому PDF."""
        target_pdf_path, page_sizes, error_message = self._get_target_pdf_context(node)
        if error_message:
            return None, error_message
        if not target_pdf_path or page_sizes is None:
            return None, "Не удалось определить геометрию целевого PDF."

        compatibility = check_annotation_compatibility(document, page_sizes)
        page_count_matches = len(document.pages) == len(page_sizes)
        prefer_coords_px = bool(getattr(document, "_prefer_coords_px", False))
        allow_legacy_same_pdf = (
            prefer_coords_px
            and page_count_matches
            and source_pdf_looks_related(document, target_pdf_path)
        )

        if not compatibility.compatible and not allow_legacy_same_pdf:
            return (
                None,
                "Аннотация не подходит к выбранному PDF.\n"
                f"Причина: {compatibility.reason}",
            )

        prefer_coords_px = prefer_coords_px or document.pdf_path != target_pdf_path
        canonicalize_annotation_document(
            document,
            pdf_path=target_pdf_path,
            pdf_page_sizes=page_sizes,
            prefer_coords_px=prefer_coords_px,
        )
        if hasattr(document, "_prefer_coords_px"):
            delattr(document, "_prefer_coords_px")

        return document, None

    def paste_annotation(self, node: TreeNode) -> None:
        """Вставить аннотацию из буфера в документ (Supabase)"""
        if self._check_locked(node):
            return

        from app.tree_client import TreeClient

        if not self._copied_annotation:
            return

        try:
            client = TreeClient()
            data = self._copied_annotation["data"]
            document, _ = Document.from_dict(data, migrate_ids=True)
            if self._copied_annotation.get("prefer_coords_px"):
                setattr(document, "_prefer_coords_px", True)
            document, error_message = self._validate_and_prepare_annotation(
                node, document
            )
            if error_message or not document:
                QMessageBox.warning(
                    self._widget,
                    "Несовместимая аннотация",
                    error_message or "Не удалось подготовить аннотацию.",
                )
                return

            success = AnnotationDBIO.save_to_db(document, node.id)
            if success:
                # Обновляем флаг has_annotation
                attrs = node.attributes.copy()
                attrs["has_annotation"] = True
                client.update_node(node.id, attributes=attrs)

                # Обновляем статус PDF
                r2_key = node.attributes.get("r2_key", "")
                if r2_key:
                    from rd_core.r2_storage import R2Storage
                    r2 = R2Storage()
                    self._update_pdf_status(node, r2_key, r2)

                self._widget.status_label.setText("📥 Аннотация вставлена")
                logger.info(f"Annotation pasted to node {node.id}")

                # Сигнал для обновления открытого документа
                self._widget.annotation_replaced.emit(r2_key)
            else:
                QMessageBox.warning(
                    self._widget, "Ошибка", "Не удалось сохранить аннотацию"
                )
        except Exception as e:
            logger.error(f"Paste annotation failed: {e}")
            QMessageBox.critical(self._widget, "Ошибка", f"Ошибка вставки: {e}")

    def upload_from_file(self, node: TreeNode) -> None:
        """Диалог загрузки аннотации блоков из файла → сохранение в Supabase"""
        if self._check_locked(node):
            return

        from app.tree_client import TreeClient

        # Диалог выбора файла
        file_path, _ = QFileDialog.getOpenFileName(
            self._widget,
            "Выберите файл аннотации",
            "",
            "JSON Files (*.json);;All Files (*)"
        )

        if not file_path:
            return

        try:
            # Загружаем и мигрируем
            loaded_doc, result = AnnotationIO.load_and_migrate(file_path)
            if not result.success or not loaded_doc:
                error_msg = "; ".join(result.errors) if result.errors else "Неизвестная ошибка"
                QMessageBox.critical(
                    self._widget, "Ошибка", f"Не удалось загрузить аннотацию:\n{error_msg}"
                )
                return

            loaded_doc, error_message = self._validate_and_prepare_annotation(
                node, loaded_doc
            )
            if error_message or not loaded_doc:
                QMessageBox.warning(
                    self._widget,
                    "Несовместимая аннотация",
                    error_message or "Не удалось подготовить аннотацию.",
                )
                return

            # Сохраняем в Supabase
            success = AnnotationDBIO.save_to_db(loaded_doc, node.id)
            if not success:
                QMessageBox.critical(
                    self._widget, "Ошибка", "Не удалось сохранить аннотацию в Supabase"
                )
                return

            logger.info(f"Annotation uploaded to Supabase: node_id={node.id}")

            # Обновляем флаг has_annotation
            client = TreeClient()
            attrs = node.attributes.copy()
            attrs["has_annotation"] = True
            client.update_node(node.id, attributes=attrs)

            # Обновляем статус PDF
            r2_key = node.attributes.get("r2_key", "")
            if r2_key:
                from rd_core.r2_storage import R2Storage
                r2 = R2Storage()
                self._update_pdf_status(node, r2_key, r2)

            self._widget.status_label.setText("📤 Аннотация загружена")
            self._widget.annotation_replaced.emit(r2_key)

            QMessageBox.information(
                self._widget, "Успех", "Аннотация блоков успешно загружена"
            )

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in annotation file: {e}")
            QMessageBox.critical(self._widget, "Ошибка", f"Неверный формат JSON:\n{e}")
        except Exception as e:
            logger.error(f"Upload annotation failed: {e}")
            QMessageBox.critical(
                self._widget, "Ошибка", f"Ошибка загрузки аннотации:\n{e}"
            )

    def detect_and_assign_stamps(self, node: TreeNode) -> None:
        """Определить и назначить штамп на всех страницах PDF"""
        if self._check_locked(node):
            return

        from app.tree_client import TreeClient
        from rd_core.models import BlockType, Document

        try:
            client = TreeClient()
            data = client.get_annotation(node.id)

            if not data:
                QMessageBox.warning(
                    self._widget, "Ошибка", "Аннотация документа не найдена"
                )
                return

            doc, _ = Document.from_dict(data)

            # Получить категорию stamp из базы
            stamp_category = self._widget.client.get_image_category_by_code("stamp")
            stamp_category_id = stamp_category.get("id") if stamp_category else None

            modified_count = 0

            for page in doc.pages:
                if not page.blocks:
                    continue

                # Пропускаем страницы где уже есть штамп
                has_stamp = any(
                    getattr(b, "category_code", None) == "stamp" for b in page.blocks
                )
                if has_stamp:
                    continue

                # Найти блок в правом нижнем углу
                best_block = None
                best_score = -1

                for block in page.blocks:
                    x1, y1, x2, y2 = block.coords_norm
                    cx = (x1 + x2) / 2
                    cy = (y1 + y2) / 2

                    if cx > 0.5 and cy > 0.7:
                        score = cx + cy
                        if score > best_score:
                            best_score = score
                            best_block = block

                if best_block:
                    best_block.block_type = BlockType.IMAGE
                    best_block.category_code = "stamp"
                    if stamp_category_id:
                        best_block.category_id = stamp_category_id
                    modified_count += 1

            if modified_count == 0:
                QMessageBox.information(self._widget, "Результат", "Штампы не найдены")
                return

            # Сохранить аннотацию обратно в Supabase
            success = AnnotationDBIO.save_to_db(doc, node.id)
            if not success:
                QMessageBox.critical(
                    self._widget, "Ошибка", "Не удалось сохранить аннотацию"
                )
                return

            self._widget.status_label.setText(f"🔖 Назначено штампов: {modified_count}")
            QMessageBox.information(
                self._widget, "Успех", f"Штамп назначен на {modified_count} страницах"
            )

            r2_key = node.attributes.get("r2_key", "")
            self._widget.annotation_replaced.emit(r2_key)

        except Exception as e:
            logger.error(f"Detect stamps failed: {e}")
            QMessageBox.critical(
                self._widget, "Ошибка", f"Ошибка определения штампов:\n{e}"
            )

    def _check_locked(self, node: TreeNode) -> bool:
        """Проверить заблокирован ли документ"""
        if node.node_type == NodeType.DOCUMENT and node.is_locked:
            QMessageBox.warning(
                self._widget,
                "Документ заблокирован",
                "Этот документ заблокирован от изменений.\nСначала снимите блокировку.",
            )
            return True
        return False

    def _update_pdf_status(self, node: TreeNode, r2_key: str, r2) -> None:
        """Обновить статус PDF после изменения аннотации"""
        from rd_core.pdf_status import calculate_pdf_status
        from app.gui.tree_node_operations import NODE_ICONS

        status, message = calculate_pdf_status(r2, node.id, r2_key, client=self._widget.client)
        self._widget.client.update_pdf_status(node.id, status.value, message)

        # Обновляем отображение в дереве
        item = self._widget._node_map.get(node.id)
        if item and node.node_type == NodeType.DOCUMENT:
            node.pdf_status = status.value
            node.pdf_status_message = message

            icon = NODE_ICONS.get(node.node_type, "📄")
            status_icon = self._widget._pdf_status_manager.get_status_icon(status.value)
            lock_icon = "🔒" if node.is_locked else ""
            version_tag = f"[v{node.version}]" if node.version else "[v1]"

            display_name = f"{icon} {node.name} {lock_icon} {status_icon}".strip()
            item.setText(0, display_name)
            item.setData(0, Qt.UserRole + 1, version_tag)
            if message:
                item.setToolTip(0, message)
