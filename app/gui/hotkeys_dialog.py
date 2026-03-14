"""Диалог настройки горячих клавиш"""

from typing import Dict

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class HotkeysDialog(QDialog):
    """Диалог для настройки горячих клавиш"""

    DEFAULT_HOTKEYS = {
        "text_block": "Ctrl+1",
        "image_block": "Ctrl+2",
        "stamp_block": "Ctrl+3",
        "toggle_shape": "Ctrl+Q",
        "cycle_block_type": "Ctrl+W",
        "copy_blocks": "Ctrl+C",
        "paste_blocks": "Ctrl+V",
        "undo": "Ctrl+Z",
        "redo": "Ctrl+Y",
    }

    HOTKEY_NAMES = {
        "text_block": "Текстовый блок",
        "image_block": "Блок картинки",
        "stamp_block": "Блок штампа",
        "toggle_shape": "Переключение формы (прямоугольник/обводка)",
        "cycle_block_type": "Смена типа блока по кругу",
        "copy_blocks": "Копировать блоки",
        "paste_blocks": "Вставить блоки",
        "undo": "Отменить действие",
        "redo": "Повторить действие",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройка горячих клавиш")
        self.setMinimumWidth(500)
        self.setModal(True)

        self.hotkey_editors: Dict[str, QKeySequenceEdit] = {}
        self._setup_ui()
        self._load_hotkeys()

    def _setup_ui(self):
        """Создание интерфейса"""
        layout = QVBoxLayout(self)

        # Информация
        info_label = QLabel(
            "Настройте горячие клавиши для быстрого доступа к функциям.\n"
            "Нажмите на поле и введите новую комбинацию клавиш."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #888; padding: 10px;")
        layout.addWidget(info_label)

        # Группа: Типы блоков
        blocks_group = QGroupBox("Типы блоков")
        blocks_layout = QFormLayout(blocks_group)
        self._add_hotkey_edit(blocks_layout, "text_block")
        self._add_hotkey_edit(blocks_layout, "image_block")
        self._add_hotkey_edit(blocks_layout, "stamp_block")
        self._add_hotkey_edit(blocks_layout, "cycle_block_type")
        layout.addWidget(blocks_group)

        # Группа: Формы
        shapes_group = QGroupBox("Формы")
        shapes_layout = QFormLayout(shapes_group)
        self._add_hotkey_edit(shapes_layout, "toggle_shape")
        layout.addWidget(shapes_group)

        # Группа: Операции с блоками
        operations_group = QGroupBox("Операции с блоками")
        operations_layout = QFormLayout(operations_group)
        self._add_hotkey_edit(operations_layout, "copy_blocks")
        self._add_hotkey_edit(operations_layout, "paste_blocks")
        layout.addWidget(operations_group)

        # Группа: Undo/Redo
        undo_group = QGroupBox("Отмена/Повтор")
        undo_layout = QFormLayout(undo_group)
        self._add_hotkey_edit(undo_layout, "undo")
        self._add_hotkey_edit(undo_layout, "redo")
        layout.addWidget(undo_group)

        # Кнопка сброса
        reset_btn = QPushButton("Сбросить по умолчанию")
        reset_btn.clicked.connect(self._reset_to_defaults)
        layout.addWidget(reset_btn)

        # Кнопки OK/Cancel
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self._save_and_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _add_hotkey_edit(self, form_layout: QFormLayout, hotkey_id: str):
        """Добавить поле редактирования горячей клавиши"""
        editor = QKeySequenceEdit()
        editor.setMaximumSequenceLength(1)
        self.hotkey_editors[hotkey_id] = editor

        label = QLabel(self.HOTKEY_NAMES.get(hotkey_id, hotkey_id))
        form_layout.addRow(label, editor)

    def _load_hotkeys(self):
        """Загрузить сохраненные горячие клавиши"""
        settings = QSettings("PDFAnnotationTool", "Hotkeys")
        for hotkey_id, editor in self.hotkey_editors.items():
            saved_key = settings.value(
                hotkey_id, self.DEFAULT_HOTKEYS.get(hotkey_id, "")
            )
            if saved_key:
                editor.setKeySequence(QKeySequence(saved_key))

    def _reset_to_defaults(self):
        """Сбросить все горячие клавиши на значения по умолчанию"""
        for hotkey_id, editor in self.hotkey_editors.items():
            default_key = self.DEFAULT_HOTKEYS.get(hotkey_id, "")
            editor.setKeySequence(QKeySequence(default_key))

    def _save_and_accept(self):
        """Сохранить изменения и закрыть диалог"""
        settings = QSettings("PDFAnnotationTool", "Hotkeys")
        for hotkey_id, editor in self.hotkey_editors.items():
            key_sequence = editor.keySequence().toString()
            settings.setValue(hotkey_id, key_sequence)

        # Уведомляем главное окно о необходимости обновить горячие клавиши
        if self.parent():
            self.parent()._update_hotkeys_from_settings()

        self.accept()

    @staticmethod
    def get_hotkey(hotkey_id: str) -> str:
        """Получить текущую горячую клавишу по ID"""
        settings = QSettings("PDFAnnotationTool", "Hotkeys")
        return settings.value(
            hotkey_id, HotkeysDialog.DEFAULT_HOTKEYS.get(hotkey_id, "")
        )
