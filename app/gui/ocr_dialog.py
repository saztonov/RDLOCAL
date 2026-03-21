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
    QRadioButton,
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

        # Дефолтные модели и движок из config.yaml
        defaults = get_ocr_defaults()
        self.ocr_backend = defaults["engine"]
        self.image_model = defaults["image_model"]
        self.stamp_model = defaults["stamp_model"]

        # Datalab настройки
        self.use_datalab = self.ocr_backend == "datalab"

        self._setup_ui()

    def _setup_ui(self):
        """Настройка интерфейса"""
        layout = QVBoxLayout(self)

        # OCR движок для текста и таблиц
        backend_group = QGroupBox("OCR движок для текста и таблиц")
        backend_layout = QVBoxLayout(backend_group)

        # RadioButton: Datalab
        self.radio_datalab = QRadioButton("Datalab Marker API (экономия бюджета)")
        self.radio_datalab.setChecked(self.ocr_backend == "datalab")
        backend_layout.addWidget(self.radio_datalab)

        datalab_info = QLabel(
            "   Склейка блоков в одно изображение для экономии кредитов.\n"
            "   10 блоков = 1 кредит вместо 10"
        )
        datalab_info.setStyleSheet("color: #888; font-size: 10px; margin-left: 20px;")
        backend_layout.addWidget(datalab_info)

        # Проверка наличия DATALAB_API_KEY
        datalab_key = os.getenv("DATALAB_API_KEY", "")
        if not datalab_key:
            error_label = QLabel("   DATALAB_API_KEY не найден в .env")
            error_label.setStyleSheet("color: #ff6b6b; font-weight: bold; margin-left: 20px;")
            backend_layout.addWidget(error_label)

        # RadioButton: Chandra 2
        self.radio_chandra = QRadioButton("Chandra 2 (локальная модель, LM Studio)")
        self.radio_chandra.setChecked(self.ocr_backend == "chandra")
        backend_layout.addWidget(self.radio_chandra)

        chandra_info = QLabel(
            "   Chandra OCR 2 на локальной машине через LM Studio + ngrok"
        )
        chandra_info.setStyleSheet("color: #888; font-size: 10px; margin-left: 20px;")
        backend_layout.addWidget(chandra_info)

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

        # Выбор OCR движка
        if self.radio_chandra.isChecked():
            self.ocr_backend = "chandra"
            self.use_datalab = False
        else:
            self.ocr_backend = "datalab"
            self.use_datalab = True

        self.accept()
