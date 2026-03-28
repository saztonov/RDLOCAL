"""
Диалог настройки OCR и выбора папки для результатов
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QVBoxLayout,
)

from app.gui.ocr_config import get_ocr_defaults

logger = logging.getLogger(__name__)

# Загрузка .env для проверки R2
load_dotenv()


class OCRDialog(QDialog):
    """Диалог выбора режима OCR и папки для результатов"""

    def __init__(self, parent=None, task_name: str = "", pdf_path: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Настройка OCR")
        self.setMinimumWidth(550)

        self.output_dir = None
        self.base_dir = None
        self.task_name = task_name
        self.pdf_path = pdf_path  # Путь к PDF для сохранения результатов рядом

        # Дефолтные модели из config.yaml
        defaults = get_ocr_defaults()
        self.ocr_backend = "lmstudio"
        self.image_model = defaults["image_model"]
        self.stamp_model = defaults["stamp_model"]

        self._setup_ui()

    def _setup_ui(self):
        """Настройка интерфейса"""
        layout = QVBoxLayout(self)

        # OCR движок
        backend_group = QGroupBox("OCR движок")
        backend_layout = QVBoxLayout(backend_group)

        engine_label = QLabel("LM Studio (Chandra 2 + Qwen)")
        engine_label.setStyleSheet("font-weight: bold;")
        backend_layout.addWidget(engine_label)

        engine_info = QLabel(
            "   Локальные модели через LM Studio"
        )
        engine_info.setStyleSheet("color: #888; font-size: 10px; margin-left: 20px;")
        backend_layout.addWidget(engine_info)

        # Проверка наличия CHANDRA_BASE_URL
        chandra_url = os.getenv("CHANDRA_BASE_URL", "")
        if not chandra_url:
            chandra_error = QLabel("   CHANDRA_BASE_URL не найден в .env")
            chandra_error.setStyleSheet("color: #ff6b6b; font-weight: bold; margin-left: 20px;")
            backend_layout.addWidget(chandra_error)

        layout.addWidget(backend_group)

        # Кнопки
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Загрузка папки из настроек
        from app.gui.folder_settings_dialog import get_projects_dir

        self.base_dir = get_projects_dir()

    def _accept(self):
        """Проверка и принятие"""
        from PySide6.QtWidgets import QMessageBox

        if not self.task_name:
            QMessageBox.warning(
                self, "Ошибка", "Сначала создайте задание в боковом меню"
            )
            return

        # Результаты сохраняются в папку где лежит PDF
        if self.pdf_path:
            self.output_dir = str(Path(self.pdf_path).parent)
        elif self.base_dir:
            self.output_dir = self.base_dir
        else:
            QMessageBox.warning(
                self, "Ошибка", "Не удалось определить папку для результатов"
            )
            return

        self.ocr_backend = "lmstudio"

        self.accept()
