"""Миксин переименования узлов дерева проектов."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import QInputDialog, QMessageBox

from app.tree_client import NodeType, TreeNode

logger = logging.getLogger(__name__)


class TreeRenameMixin:
    """Переименование узлов и связанных файлов в R2."""

    def _rename_related_files(self, old_r2_key: str, new_r2_key: str, node_id: str):
        """Переименовать связанные файлы (ocr.html, document.md) в R2.

        Аннотация хранится в Supabase (привязана к node_id), переименование не нужно.
        """
        from pathlib import PurePosixPath

        from rd_core.r2_storage import R2Storage

        old_stem = PurePosixPath(old_r2_key).stem
        new_stem = PurePosixPath(new_r2_key).stem
        r2_prefix = str(PurePosixPath(old_r2_key).parent)

        r2 = R2Storage()

        # Список связанных файлов для переименования
        # Аннотация привязана к node_id в Supabase — переименование не требуется
        related_files = [
            (f"{r2_prefix}/{old_stem}_ocr.html", f"{r2_prefix}/{new_stem}_ocr.html"),
            (
                f"{r2_prefix}/{old_stem}_document.md",
                f"{r2_prefix}/{new_stem}_document.md",
            ),
        ]

        # Переименовываем файлы в R2
        for old_key, new_key in related_files:
            # Переименовываем в R2 если файл там существует
            if r2.exists(old_key):
                try:
                    if r2.rename_object(old_key, new_key):
                        logger.info(f"Renamed in R2: {old_key} → {new_key}")
                except Exception as e:
                    logger.error(f"Failed to rename in R2 {old_key}: {e}")

            # Обновляем запись в node_files если существует
            self._update_node_file_r2_key(node_id, old_key, new_key)

    def _update_node_file_r2_key(self, node_id: str, old_r2_key: str, new_r2_key: str):
        """Обновить r2_key в таблице node_files"""
        try:
            node_file = self.client.get_node_file_by_r2_key(node_id, old_r2_key)
            if node_file:
                # Обновляем r2_key и file_name
                new_file_name = Path(new_r2_key).name
                self.client.update_node_file(
                    node_file.id, r2_key=new_r2_key, file_name=new_file_name
                )
                logger.info(f"Updated node_file: {old_r2_key} → {new_r2_key}")
        except Exception as e:
            logger.error(f"Failed to update node_file: {e}")

    def _rename_node(self, node: TreeNode):
        """Переименовать узел (для документов также переименовывает в R2)"""
        # Проверка блокировки документа
        if self._check_document_locked(node):
            return

        new_name, ok = QInputDialog.getText(
            self, "Переименовать", "Новое название:", text=node.name
        )
        if ok and new_name.strip() and new_name.strip() != node.name:
            try:
                new_name_clean = new_name.strip()

                # Проверка уникальности имени в папке
                if node.parent_id and not self._check_name_unique(
                    node.parent_id, new_name_clean, node.id
                ):
                    QMessageBox.warning(
                        self,
                        "Ошибка",
                        f"Элемент с именем '{new_name_clean}' уже существует в этой папке",
                    )
                    return

                # Для документов проверяем и добавляем расширение .pdf
                if node.node_type == NodeType.DOCUMENT:
                    # Проверяем что имя заканчивается на .pdf (регистронезависимо)
                    if not new_name_clean.lower().endswith(".pdf"):
                        # Автоматически добавляем расширение .pdf
                        new_name_clean = f"{new_name_clean}.pdf"
                        logger.info(
                            f"Added .pdf extension to document name: {new_name_clean}"
                        )
                        # Повторная проверка уникальности после добавления расширения
                        if node.parent_id and not self._check_name_unique(
                            node.parent_id, new_name_clean, node.id
                        ):
                            QMessageBox.warning(
                                self,
                                "Ошибка",
                                f"Элемент с именем '{new_name_clean}' уже существует в этой папке",
                            )
                            return

                # Для документов переименовываем файл в R2
                if node.node_type == NodeType.DOCUMENT:
                    old_r2_key = node.attributes.get("r2_key", "")

                    # Закрываем файл если он открыт в редакторе
                    self._close_if_open(old_r2_key)

                    if old_r2_key:
                        from pathlib import PurePosixPath

                        from rd_core.r2_storage import R2Storage

                        # Формируем новый ключ (меняем только имя файла)
                        old_path = PurePosixPath(old_r2_key)
                        new_r2_key = str(old_path.parent / new_name_clean)

                        try:
                            r2 = R2Storage()
                            # Проверяем существование файла в R2 перед переименованием
                            if not r2.exists(old_r2_key, use_cache=False):
                                logger.warning(
                                    f"File not found in R2: {old_r2_key}, updating metadata only"
                                )
                                self._rename_related_files(
                                    old_r2_key, new_r2_key, node.id
                                )
                                node.attributes["r2_key"] = new_r2_key
                                node.attributes["original_name"] = new_name_clean
                                self.client.update_node(
                                    node.id,
                                    name=new_name_clean,
                                    attributes=node.attributes,
                                )
                                self._update_node_file_r2_key(
                                    node.id, old_r2_key, new_r2_key
                                )
                            elif r2.rename_object(old_r2_key, new_r2_key):
                                # Переименовываем связанные файлы
                                self._rename_related_files(
                                    old_r2_key, new_r2_key, node.id
                                )

                                # Обновляем r2_key в attributes
                                node.attributes["r2_key"] = new_r2_key
                                node.attributes["original_name"] = new_name_clean
                                self.client.update_node(
                                    node.id,
                                    name=new_name_clean,
                                    attributes=node.attributes,
                                )

                                # Обновляем запись PDF в node_files
                                self._update_node_file_r2_key(
                                    node.id, old_r2_key, new_r2_key
                                )
                            else:
                                QMessageBox.warning(
                                    self,
                                    "Внимание",
                                    "Не удалось переименовать файл в R2",
                                )
                                return
                        except Exception as e:
                            logger.error(f"R2 rename error: {e}")
                            QMessageBox.warning(self, "Ошибка R2", f"Ошибка R2: {e}")
                            return
                    else:
                        self.client.update_node(node.id, name=new_name_clean)
                else:
                    self.client.update_node(node.id, name=new_name_clean)

                # Обновляем UI без полной перезагрузки дерева
                node.name = new_name_clean
                self._update_single_item(node.id, name=new_name_clean)
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", str(e))
