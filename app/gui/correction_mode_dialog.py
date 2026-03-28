"""Диалог выбора режима распознавания: все блоки или только корректировочные"""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)


class CorrectionModeDialog(QDialog):
    """Диалог выбора режима распознавания"""

    MODE_ALL = "all"
    MODE_CORRECTION = "correction"

    def __init__(self, parent=None, correction_count: int = 0, total_count: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Режим распознавания")
        self.setMinimumWidth(400)

        self.correction_count = correction_count
        self.total_count = total_count
        self.selected_mode = self.MODE_ALL

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Информация о блоках
        info = QLabel(
            f"Всего блоков: {self.total_count}\n"
            f"Помечено для корректировки: {self.correction_count}"
        )
        layout.addWidget(info)

        # Группа выбора режима
        group = QGroupBox("Выберите режим распознавания")
        group_layout = QVBoxLayout(group)

        self.all_radio = QRadioButton(f"Распознать ВСЕ блоки ({self.total_count})")
        self.all_radio.setChecked(True)
        group_layout.addWidget(self.all_radio)

        self.correction_radio = QRadioButton(
            f"Только КОРРЕКТИРОВОЧНЫЕ блоки ({self.correction_count})"
        )
        self.correction_radio.setEnabled(self.correction_count > 0)
        group_layout.addWidget(self.correction_radio)

        if self.correction_count > 0:
            hint = QLabel(
                "Результаты будут вставлены в существующие файлы\n"
                "(аннотация в Supabase, ocr.html, document.md)"
            )
            hint.setStyleSheet("color: #888; font-size: 10px; margin-left: 20px;")
            group_layout.addWidget(hint)

        layout.addWidget(group)

        # Кнопки
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        self.selected_mode = (
            self.MODE_CORRECTION if self.correction_radio.isChecked() else self.MODE_ALL
        )
        self.accept()
