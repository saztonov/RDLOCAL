"""Mixin для операций с узлами дерева проектов"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QInputDialog,
    QMessageBox,
)

from app.gui.tree_cache_ops import TreeCacheOperationsMixin
from app.gui.tree_file_upload_mixin import TreeFileUploadMixin
from app.gui.tree_folder_ops import TreeFolderOperationsMixin
from app.gui.tree_rename_mixin import TreeRenameMixin
from app.gui.tree_split_mixin import TreeSplitMixin
from app.tree_client import NodeStatus, NodeType, TreeNode

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


NODE_ICONS = {
    # Новые типы v2
    NodeType.FOLDER: "📁",
    NodeType.DOCUMENT: "📄",
    # Legacy aliases (для обратной совместимости с данными в БД)
    "project": "📁",
    "stage": "🏗",
    "section": "📚",
    "task_folder": "📂",
    "document": "📄",
    "folder": "📁",
}


def get_node_icon(node: TreeNode) -> str:
    """Получить иконку для узла (учитывает legacy_node_type)."""
    # Сначала проверяем legacy_node_type в attributes
    legacy_type = node.legacy_node_type
    if legacy_type and legacy_type in NODE_ICONS:
        return NODE_ICONS[legacy_type]

    # Используем node_type
    if node.node_type in NODE_ICONS:
        return NODE_ICONS[node.node_type]

    # Fallback
    return "📁" if node.is_folder else "📄"

STATUS_COLORS = {
    NodeStatus.ACTIVE: "#e0e0e0",
    NodeStatus.COMPLETED: "#4caf50",
    NodeStatus.ARCHIVED: "#9e9e9e",
}


class TreeNodeOperationsMixin(
    TreeCacheOperationsMixin,
    TreeFolderOperationsMixin,
    TreeFileUploadMixin,
    TreeRenameMixin,
    TreeSplitMixin,
):
    """Миксин для CRUD операций с узлами дерева"""

    def _check_name_unique(
        self, parent_id: str, name: str, exclude_node_id: str = None
    ) -> bool:
        """Проверить уникальность имени в папке. True если уникально."""
        siblings = self.client.get_children(parent_id)
        for s in siblings:
            if s.name == name and s.id != exclude_node_id:
                return False
        return True

    def _create_project(self):
        """Создать новый проект (корневая папка)"""
        name, ok = QInputDialog.getText(self, "Новый проект", "Название проекта:")
        if ok and name.strip():
            try:
                # Создаём корневую папку (FOLDER вместо PROJECT)
                node = self.client.create_node(NodeType.FOLDER, name.strip())
                self._node_cache.invalidate_roots()
                item = self._item_builder.create_item(node)
                self.tree.addTopLevelItem(item)
                self._item_builder.add_placeholder(item, node)
                self.tree.setCurrentItem(item)
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", str(e))

    def _create_child_node(self, parent_node: TreeNode, child_type):
        """Создать дочерний узел"""
        if isinstance(child_type, str):
            logger.debug(f"child_type is str: {child_type}, converting to NodeType")
            child_type = NodeType(child_type)

        logger.debug(
            f"_create_child_node: parent={parent_node.id}, child_type={child_type}"
        )

        from app.gui.create_node_dialog import CreateNodeDialog

        dialog = CreateNodeDialog(self, child_type)
        if dialog.exec_() == QDialog.Accepted:
            name, code = dialog.get_data()
            logger.debug(f"Dialog result: name={name}, code={code}")
            if name:
                try:
                    logger.debug(
                        f"Creating node: type={child_type}, name={name}, parent={parent_node.id}, code={code}"
                    )
                    node = self.client.create_node(
                        child_type, name, parent_node.id, code
                    )
                    self._node_cache.invalidate_children(parent_node.id)
                    logger.debug(f"Node created: {node.id}")
                    parent_item = self._node_map.get(parent_node.id)
                    if parent_item:
                        if parent_item.childCount() == 1:
                            child = parent_item.child(0)
                            if child.data(0, self._get_user_role()) == "placeholder":
                                parent_item.removeChild(child)

                        child_item = self._item_builder.create_item(node)
                        parent_item.addChild(child_item)
                        self._item_builder.add_placeholder(child_item, node)
                        parent_item.setExpanded(True)
                        self.tree.setCurrentItem(child_item)
                except Exception as e:
                    logger.exception(f"Error creating child node: {e}")
                    QMessageBox.critical(self, "Ошибка", str(e))

    def _get_user_role(self):
        """Получить Qt.UserRole"""
        return Qt.UserRole

    def _close_if_open(self, r2_key: str):
        """Закрыть файл в редакторе если он открыт (по r2_key)"""
        if not r2_key:
            return

        main_window = self.window()
        current_r2_key = getattr(main_window, "_current_r2_key", None)
        if not current_r2_key:
            return

        if current_r2_key == r2_key:
            if hasattr(main_window, "_clear_interface"):
                main_window._clear_interface()
                logger.info(f"Closed file in editor: {r2_key}")

    def _set_status(self, node: TreeNode, status: NodeStatus):
        """Установить статус узла"""
        try:
            self.client.update_node(node.id, status=status)
            item = self._node_map.get(node.id)
            if item:
                item.setForeground(0, QColor(STATUS_COLORS.get(status, "#e0e0e0")))
                node.status = status
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _set_document_version(self, node: TreeNode, version: int):
        """Установить версию документа"""
        try:
            self.client.update_node(node.id, version=version)
            node.version = version

            # Обновляем отображение в дереве
            item = self._node_map.get(node.id)
            if item:
                icon = NODE_ICONS.get(node.node_type, "📄")
                has_annotation = node.attributes.get("has_annotation", False)
                ann_icon = " 📋" if has_annotation else ""
                display_name = f"{icon} {node.name}{ann_icon}"
                item.setText(0, display_name)
                item.setData(0, Qt.UserRole + 1, f"[v{version}]")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _delete_node(self, node: TreeNode):
        """Удалить узел и все вложенные (из R2, кэша и Supabase)"""
        # Проверка блокировки документа
        if self._check_document_locked(node):
            return

        reply = QMessageBox.question(
            self,
            "Подтверждение",
            f"Удалить '{node.name}' и все вложенные элементы?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                # Рекурсивно удаляем все документы в ветке из R2 и кэша
                self._delete_branch_files(node)

                if self.client.delete_node(node.id):
                    # Инвалидируем кэш
                    self._node_cache.invalidate_subtree(node.id)
                    if node.parent_id:
                        self._node_cache.invalidate_children(node.parent_id)
                    else:
                        self._node_cache.invalidate_roots()

                    item = self._node_map.get(node.id)
                    if item:
                        self._remove_subtree_from_node_map(item)
                        parent = item.parent()
                        if parent:
                            parent.removeChild(item)
                        else:
                            idx = self.tree.indexOfTopLevelItem(item)
                            self.tree.takeTopLevelItem(idx)
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", str(e))
