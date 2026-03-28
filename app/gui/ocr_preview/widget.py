"""
Виджет предварительного просмотра OCR результатов
Отображает HTML из _result.json для выбранного блока
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .content_mixin import ContentMixin
from .edit_mixin import EditMixin

logger = logging.getLogger(__name__)


class OcrPreviewWidget(ContentMixin, EditMixin, QWidget):
    """Виджет просмотра и редактирования OCR результатов"""

    content_changed = Signal(str, str)  # block_id, new_html

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_block_id: Optional[str] = None
        self._result_data: Optional[Dict[str, Any]] = None
        self._node_id: Optional[str] = None
        self._is_modified = False
        self._is_editing = False  # Режим редактирования

        self._setup_ui()

    def _setup_ui(self):
        """Настройка UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Заголовок с ID блока
        header = QHBoxLayout()
        header.setSpacing(4)

        self.title_label = QLabel("OCR Preview")
        self.title_label.setStyleSheet("font-weight: bold; font-size: 11px;")
        header.addWidget(self.title_label)

        # ID блока (полный, с возможностью копирования)
        self.block_id_label = QLabel("")
        self.block_id_label.setStyleSheet(
            """
            QLabel {
                color: #888;
                font-family: 'Consolas', monospace;
                font-size: 10px;
                padding: 2px 4px;
                background: #2d2d2d;
                border-radius: 3px;
            }
        """
        )
        self.block_id_label.setCursor(QCursor(Qt.PointingHandCursor))
        self.block_id_label.setToolTip("Клик для копирования ID")
        self.block_id_label.mousePressEvent = self._copy_block_id
        header.addWidget(self.block_id_label)

        header.addStretch()

        # Кнопка редактирования/сохранения
        self.edit_save_btn = QPushButton("✏️ Редактировать")
        self.edit_save_btn.setToolTip("Редактировать HTML")
        self.edit_save_btn.clicked.connect(self._toggle_edit_mode)
        self.edit_save_btn.setEnabled(False)
        header.addWidget(self.edit_save_btn)

        layout.addLayout(header)

        # Главный splitter
        main_splitter = QSplitter(Qt.Vertical)

        # === Верхняя часть: Preview + Editor ===
        content_splitter = QSplitter(Qt.Vertical)

        # HTML Preview (QWebEngineView для корректного рендеринга HTML/CSS)
        self.preview_edit = QWebEngineView()
        self.preview_edit.setStyleSheet(
            """
            QWebEngineView {
                background-color: #1e1e1e;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
            }
        """
        )
        # Отключаем контекстное меню браузера
        self.preview_edit.setContextMenuPolicy(Qt.NoContextMenu)
        content_splitter.addWidget(self.preview_edit)

        # Raw HTML Editor (скрыт по умолчанию)
        self.editor_widget = QWidget()
        editor_layout = QVBoxLayout(self.editor_widget)
        editor_layout.setContentsMargins(0, 4, 0, 0)

        editor_label = QLabel("HTML (редактирование)")
        editor_label.setStyleSheet("font-size: 10px; color: #888;")
        editor_layout.addWidget(editor_label)

        self.html_edit = QTextEdit()
        self.html_edit.setStyleSheet(
            """
            QTextEdit {
                background-color: #252526;
                color: #9cdcfe;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 4px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
            }
        """
        )
        self.html_edit.textChanged.connect(self._on_text_changed)
        editor_layout.addWidget(self.html_edit)

        content_splitter.addWidget(self.editor_widget)
        content_splitter.setSizes([250, 150])

        # Скрываем редактор по умолчанию
        self.editor_widget.hide()

        main_splitter.addWidget(content_splitter)

        # === Нижняя часть: Штамп ===
        self.stamp_group = QGroupBox("📋 Штамп листа")
        self.stamp_group.setStyleSheet(
            """
            QGroupBox {
                font-weight: bold;
                border: 1px solid #569cd6;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #569cd6;
            }
        """
        )
        stamp_layout = QVBoxLayout(self.stamp_group)
        stamp_layout.setContentsMargins(8, 12, 8, 8)

        self.stamp_content = QLabel("")
        self.stamp_content.setWordWrap(True)
        self.stamp_content.setStyleSheet("font-size: 11px; color: #d4d4d4;")
        self.stamp_content.setTextInteractionFlags(Qt.TextSelectableByMouse)
        stamp_layout.addWidget(self.stamp_content)

        self.stamp_group.hide()
        main_splitter.addWidget(self.stamp_group)

        main_splitter.setSizes([400, 150])
        layout.addWidget(main_splitter)

        # Placeholder
        self._show_placeholder()

    def _copy_block_id(self, event):
        """Копировать ID блока в буфер обмена"""
        if self._current_block_id:
            QApplication.clipboard().setText(self._current_block_id)
            from app.gui.toast import show_toast

            show_toast(self.window(), f"ID скопирован: {self._current_block_id}")

    def _show_placeholder(self):
        """Показать заглушку"""
        self.preview_edit.setHtml(
            '<p style="color: #666; text-align: center; margin-top: 40px;">'
            "Выберите блок для просмотра OCR результата</p>"
        )
        self.html_edit.clear()
        self.html_edit.setEnabled(False)
        self.block_id_label.setText("")
        self.stamp_group.hide()
        self._current_block_id = None

    def clear(self):
        """Очистить виджет"""
        self._result_data = None
        self._node_id = None
        self._current_block_id = None
        self._blocks_index = {}
        self.title_label.setText("OCR Preview")
        self.block_id_label.setText("")
        self.stamp_group.hide()
        self._show_placeholder()
