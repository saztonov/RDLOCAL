"""Диалог выбора режима повторного OCR: умное или полное перераспознавание."""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)


class SmartOCRModeDialog(QDialog):
    """Диалог выбора режима при повторном распознавании."""

    MODE_SMART = "smart"
    MODE_FULL = "full"

    def __init__(
        self,
        parent=None,
        total_count: int = 0,
        needs_ocr_count: int = 0,
        successful_count: int = 0,
    ):
        super().__init__(parent)
        self.setWindowTitle("Режим распознавания")
        self.setMinimumWidth(420)

        self.total_count = total_count
        self.needs_ocr_count = needs_ocr_count
        self.successful_count = successful_count
        self.selected_mode = self.MODE_SMART

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            f"Всего блоков: {self.total_count}\n"
            f"Успешно распознано: {self.successful_count}\n"
            f"Требуют распознавания: {self.needs_ocr_count}"
        )
        layout.addWidget(info)

        group = QGroupBox("Выберите режим распознавания")
        group_layout = QVBoxLayout(group)

        self.smart_radio = QRadioButton(
            f"Умное распознавание ({self.needs_ocr_count} блоков)"
        )
        self.smart_radio.setChecked(True)
        group_layout.addWidget(self.smart_radio)

        smart_hint = QLabel(
            "Распознать только новые и ошибочные блоки.\n"
            f"Результаты {self.successful_count} успешных блоков сохранятся."
        )
        smart_hint.setStyleSheet("color: #888; font-size: 10px; margin-left: 20px;")
        group_layout.addWidget(smart_hint)

        self.full_radio = QRadioButton(
            f"Полное перераспознавание ({self.total_count} блоков)"
        )
        group_layout.addWidget(self.full_radio)

        full_warn = QLabel("Все предыдущие результаты будут удалены.")
        full_warn.setStyleSheet("color: #e74c3c; font-size: 10px; margin-left: 20px;")
        group_layout.addWidget(full_warn)

        layout.addWidget(group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        self.selected_mode = (
            self.MODE_SMART if self.smart_radio.isChecked() else self.MODE_FULL
        )
        self.accept()
