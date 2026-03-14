"""Построение элементов дерева проектов"""
import logging
from typing import TYPE_CHECKING

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTreeWidgetItem
from PySide6.QtCore import Qt

from app.gui.tree_node_operations import NODE_ICONS, STATUS_COLORS
from app.tree_client import NodeType, TreeNode

if TYPE_CHECKING:
    from app.gui.project_tree.widget import ProjectTreeWidget

logger = logging.getLogger(__name__)


class TreeItemBuilder:
    """
    Построитель элементов дерева проектов.

    Отвечает за:
    - Создание QTreeWidgetItem из TreeNode
    - Добавление placeholder для lazy loading
    - Форматирование отображения узлов
    """

    def __init__(self, widget: "ProjectTreeWidget"):
        """
        Args:
            widget: Родительский виджет ProjectTreeWidget
        """
        self._widget = widget

    def create_item(self, node: TreeNode) -> QTreeWidgetItem:
        """Создать элемент дерева для узла"""
        icon = NODE_ICONS.get(node.node_type, "📄")

        # Для документов показываем версию и иконку статуса PDF из БД
        if node.node_type == NodeType.DOCUMENT:
            display_name, version_display = self._format_document(node, icon)
        elif node.node_type == NodeType.TASK_FOLDER:
            display_name, version_display = self._format_task_folder(node, icon)
        else:
            display_name, version_display = self._format_default(node, icon)

        item = QTreeWidgetItem([display_name])
        item.setData(0, Qt.UserRole, node)
        item.setData(0, Qt.UserRole + 1, version_display)  # Версия для делегата
        item.setForeground(0, QColor(STATUS_COLORS.get(node.status, "#e0e0e0")))

        # Устанавливаем tooltip для PDF документов
        if node.node_type == NodeType.DOCUMENT and node.pdf_status_message:
            item.setToolTip(0, node.pdf_status_message)

        # Регистрируем в node_map
        self._widget._node_map[node.id] = item

        return item

    def add_placeholder(self, item: QTreeWidgetItem, node: TreeNode) -> None:
        """Добавить placeholder для lazy loading"""
        allowed = node.get_allowed_child_types()
        # Для документов не добавляем placeholder
        if allowed:
            placeholder = QTreeWidgetItem(["..."])
            placeholder.setData(0, Qt.UserRole, "placeholder")
            item.addChild(placeholder)

    def _format_document(self, node: TreeNode, icon: str) -> tuple:
        """Форматировать отображение документа"""
        from app.gui.project_tree.pdf_status_manager import PDFStatusManager

        version_tag = f"[v{node.version}]" if node.version else "[v1]"
        status_icon = PDFStatusManager.get_status_icon(node.pdf_status or "unknown")
        lock_icon = "🔒" if node.is_locked else ""

        display_name = f"{icon} {node.name} {lock_icon} {status_icon}".strip()
        return display_name, version_tag

    def _format_task_folder(self, node: TreeNode, icon: str) -> tuple:
        """Форматировать отображение папки заданий"""
        if node.code:
            display_name = f"{icon} [{node.code}] {node.name}".strip()
        else:
            display_name = f"{icon} {node.name}".strip()
        return display_name, None

    def _format_default(self, node: TreeNode, icon: str) -> tuple:
        """Форматировать отображение по умолчанию"""
        if node.code:
            display_name = f"{icon} [{node.code}] {node.name}"
        else:
            display_name = f"{icon} {node.name}"
        return display_name, None

    def update_item_display(self, item: QTreeWidgetItem, node: TreeNode) -> None:
        """Обновить отображение существующего элемента без пересоздания."""
        icon = NODE_ICONS.get(node.node_type, "📄")

        if node.node_type == NodeType.DOCUMENT:
            display_name, version_display = self._format_document(node, icon)
        elif node.node_type == NodeType.TASK_FOLDER:
            display_name, version_display = self._format_task_folder(node, icon)
        else:
            display_name, version_display = self._format_default(node, icon)

        item.setText(0, display_name)
        item.setData(0, Qt.UserRole, node)
        if version_display is not None:
            item.setData(0, Qt.UserRole + 1, version_display)
        item.setForeground(0, QColor(STATUS_COLORS.get(node.status, "#e0e0e0")))

        if node.node_type == NodeType.DOCUMENT and node.pdf_status_message:
            item.setToolTip(0, node.pdf_status_message)
        else:
            item.setToolTip(0, "")


def update_document_item(
    item: QTreeWidgetItem,
    node: TreeNode,
    status: str,
    message: str
) -> None:
    """
    Обновить отображение элемента документа.

    Args:
        item: Элемент дерева
        node: Узел TreeNode
        status: Статус PDF
        message: Сообщение статуса
    """
    from app.gui.project_tree.pdf_status_manager import PDFStatusManager

    icon = NODE_ICONS.get(node.node_type, "📄")
    status_icon = PDFStatusManager.get_status_icon(status)
    lock_icon = "🔒" if node.is_locked else ""
    version_tag = f"[v{node.version}]" if node.version else "[v1]"

    display_name = f"{icon} {node.name} {lock_icon} {status_icon}".strip()
    item.setText(0, display_name)
    item.setData(0, Qt.UserRole + 1, version_tag)

    if message:
        item.setToolTip(0, message)
    else:
        item.setToolTip(0, "")
