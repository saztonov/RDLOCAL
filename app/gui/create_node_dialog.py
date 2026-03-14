"""Диалог создания узла дерева проектов"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
)

from app.gui.project_tree import get_node_type_name
from app.tree_client import NodeType


class CreateNodeDialog(QDialog):
    """Диалог создания узла дерева (folder/document)."""

    def __init__(self, parent, node_type: NodeType):
        super().__init__(parent)
        self.node_type = node_type
        self._setup_ui()

    def _setup_ui(self):
        type_name = get_node_type_name(self.node_type)
        self.setWindowTitle(f"Создать: {type_name}")
        self.setMinimumWidth(350)

        layout = QFormLayout(self)

        self.name_edit = QLineEdit()
        placeholder = "Введите название папки..." if self.node_type == NodeType.FOLDER else "Введите название..."
        self.name_edit.setPlaceholderText(placeholder)
        layout.addRow("Название:", self.name_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self) -> tuple[str, Optional[str]]:
        """Вернуть (name, code)"""
        return self.name_edit.text().strip(), None
