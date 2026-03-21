"""Диалог верификации блоков - сравнение annotation.json, ocr.html, result.json"""

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from app.gui.block_verification_models import VerificationResult
from app.gui.block_verification_worker import VerificationWorker

logger = logging.getLogger(__name__)


class BlockVerificationDialog(QDialog):
    """Диалог верификации блоков"""

    def __init__(self, node_name: str, r2_key: str, parent=None, node_id: str = ""):
        super().__init__(parent)
        self.node_name = node_name
        self.r2_key = r2_key
        self.node_id = node_id
        self._worker: Optional[VerificationWorker] = None

        self.setWindowTitle(f"Верификация блоков: {node_name}")
        self.setMinimumSize(650, 550)
        self.resize(700, 700)  # Начальный размер больше минимального
        self.setModal(True)

        self._setup_ui()
        self._start_verification()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Заголовок
        title = QLabel(f"📊 Верификация блоков документа")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        # Прогресс
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # Indeterminate
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Загрузка данных...")
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

        # Группа: Annotation
        self.ann_group = QGroupBox("📄 Annotation.json")
        ann_layout = QVBoxLayout(self.ann_group)
        self.ann_label = QLabel()
        self.ann_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ann_layout.addWidget(self.ann_label)
        layout.addWidget(self.ann_group)
        self.ann_group.hide()

        # Группа: OCR HTML
        self.ocr_group = QGroupBox("🌐 OCR.html")
        ocr_layout = QVBoxLayout(self.ocr_group)
        self.ocr_label = QLabel()
        self.ocr_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ocr_layout.addWidget(self.ocr_label)
        layout.addWidget(self.ocr_group)
        self.ocr_group.hide()

        # Группа: Result JSON
        self.result_group = QGroupBox("📋 Result.json")
        result_layout = QVBoxLayout(self.result_group)
        self.result_label = QLabel()
        self.result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        result_layout.addWidget(self.result_label)
        layout.addWidget(self.result_group)
        self.result_group.hide()

        # Группа: Document MD
        self.md_group = QGroupBox("📝 Document.md")
        md_layout = QVBoxLayout(self.md_group)
        self.md_label = QLabel()
        self.md_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        md_layout.addWidget(self.md_label)
        layout.addWidget(self.md_group)
        self.md_group.hide()

        # Результат верификации
        self.verdict_group = QGroupBox("🔍 Результат верификации")
        verdict_layout = QVBoxLayout(self.verdict_group)
        self.verdict_label = QLabel()
        self.verdict_label.setWordWrap(True)
        self.verdict_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        verdict_layout.addWidget(self.verdict_label)
        layout.addWidget(self.verdict_group)
        self.verdict_group.hide()

        # Детали отсутствующих блоков
        self.missing_group = QGroupBox("❌ Отсутствующие блоки")
        missing_layout = QVBoxLayout(self.missing_group)
        self.missing_text = QTextEdit()
        self.missing_text.setReadOnly(True)
        self.missing_text.setStyleSheet(
            """
            QTextEdit {
                background-color: #2d2d2d;
                color: #ff6b6b;
                font-family: 'Consolas', monospace;
                font-size: 11px;
            }
        """
        )
        self.missing_text.setMaximumHeight(200)
        missing_layout.addWidget(self.missing_text)
        layout.addWidget(self.missing_group)
        self.missing_group.hide()

        # Кнопки
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        self.copy_btn = QPushButton("📋 Копировать отчёт")
        self.copy_btn.clicked.connect(self._copy_report)
        self.copy_btn.hide()
        buttons_layout.addWidget(self.copy_btn)

        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.close)
        buttons_layout.addWidget(self.close_btn)

        layout.addLayout(buttons_layout)

    def _start_verification(self):
        """Запустить верификацию"""
        self._worker = VerificationWorker(self.r2_key, self.node_id)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, message: str):
        self.status_label.setText(message)

    def _on_finished(self, result):
        self.progress_bar.hide()

        if isinstance(result, str):
            # Ошибка
            self.status_label.setText(f"❌ {result}")
            self.status_label.setStyleSheet("color: #ff6b6b;")
            return

        self._result = result
        self._display_result(result)

    def _display_result(self, r: VerificationResult):
        """Отобразить результат верификации"""
        self.status_label.hide()

        # Annotation stats
        self.ann_label.setText(
            f"<b>Всего блоков:</b> {r.ann_total}<br>"
            f"<b>Текстовых:</b> {r.ann_text}<br>"
            f"<b>Изображений:</b> {r.ann_image}<br>"
            f"<b>Штампов (code=stamp):</b> {r.ann_stamp}<br>"
            f"<b>Встроенных TEXT→IMAGE:</b> {len(r.embedded_text_ids)}"
        )
        self.ann_group.show()

        # OCR HTML stats
        self.ocr_label.setText(
            f"<b>Найдено блоков:</b> {len(r.ocr_html_blocks)}<br>"
            f"<span style='color: #888;'>(штампы не включаются в ocr.html)</span>"
        )
        self.ocr_group.show()

        # Result JSON stats
        self.result_label.setText(f"<b>Найдено блоков:</b> {len(r.result_blocks)}")
        self.result_group.show()

        # Document MD stats
        self.md_label.setText(
            f"<b>Найдено блоков:</b> {len(r.document_md_blocks)}<br>"
            f"<span style='color: #888;'>(компактный формат для LLM)</span>"
        )
        self.md_group.show()

        # Вердикт
        expected_count = len(r.expected_blocks)

        if r.is_success:
            self.verdict_label.setText(
                f"<span style='color: #4ade80; font-size: 16px;'>✅ Верификация пройдена</span><br><br>"
                f"Все {expected_count} блоков (без штампов) найдены в итоговых документах."
            )
        else:
            missing_ocr = len(r.missing_in_ocr_html)
            missing_res = len(r.missing_in_result)
            missing_md = len(r.missing_in_document_md)
            error_count = len(r.error_blocks)
            suspicious_count = len(r.suspicious_blocks)

            verdict_lines = [
                f"<span style='color: #ff6b6b; font-size: 16px;'>❌ Обнаружены расхождения</span><br><br>",
                f"<b>Ожидалось блоков (без штампов):</b> {expected_count}<br>",
            ]
            if missing_ocr:
                verdict_lines.append(f"<b>Отсутствует в ocr.html:</b> {missing_ocr}<br>")
            if missing_res:
                verdict_lines.append(f"<b>Отсутствует в result.json:</b> {missing_res}<br>")
            if missing_md:
                verdict_lines.append(f"<b>Отсутствует в document.md:</b> {missing_md}<br>")
            if error_count:
                verdict_lines.append(f"<b>Блоков с ошибками OCR:</b> {error_count}<br>")
            if suspicious_count:
                verdict_lines.append(f"<b>Блоков с подозрительным выводом:</b> {suspicious_count}<br>")

            self.verdict_label.setText("".join(verdict_lines))

            # Детали отсутствующих и проблемных блоков
            lines = []

            if r.missing_in_ocr_html:
                lines.append("=== Отсутствуют в ocr.html ===")
                for b in r.missing_in_ocr_html:
                    lines.append(f"  Стр. {b.page_index + 1}: {b.id} ({b.block_type})")

            if r.missing_in_result:
                if lines:
                    lines.append("")
                lines.append("=== Отсутствуют в result.json ===")
                for b in r.missing_in_result:
                    lines.append(f"  Стр. {b.page_index + 1}: {b.id} ({b.block_type})")

            if r.missing_in_document_md:
                if lines:
                    lines.append("")
                lines.append("=== Отсутствуют в document.md ===")
                for b in r.missing_in_document_md:
                    lines.append(f"  Стр. {b.page_index + 1}: {b.id} ({b.block_type})")

            if r.error_blocks:
                if lines:
                    lines.append("")
                lines.append("=== Блоки с ошибками OCR ===")
                for b in r.error_blocks:
                    reason = r.error_reasons.get(b.id, "")
                    lines.append(f"  Стр. {b.page_index + 1}: {b.id} ({b.block_type}) — {reason}")

            if r.suspicious_blocks:
                if lines:
                    lines.append("")
                lines.append("=== Подозрительный OCR вывод ===")
                for b in r.suspicious_blocks:
                    reason = r.suspicious_reasons.get(b.id, "")
                    lines.append(f"  Стр. {b.page_index + 1}: {b.id} ({b.block_type}) — {reason}")

            self.missing_text.setPlainText("\n".join(lines))
            self.missing_group.show()

        self.verdict_group.show()
        self.copy_btn.show()

        # Подстраиваем размер под содержимое
        self.adjustSize()
        # Убеждаемся что размер не меньше минимального
        if self.width() < 700:
            self.resize(700, self.height())
        if self.height() < 650:
            self.resize(self.width(), 650)

    def _copy_report(self):
        """Скопировать отчёт в буфер обмена"""
        if not hasattr(self, "_result"):
            return

        r = self._result
        lines = [
            f"Верификация блоков: {self.node_name}",
            f"R2 Key: {self.r2_key}",
            "",
            "=== Annotation.json ===",
            f"Всего блоков: {r.ann_total}",
            f"Текстовых: {r.ann_text}",
            f"Изображений: {r.ann_image}",
            f"Штампов: {r.ann_stamp}",
            f"Встроенных TEXT→IMAGE: {len(r.embedded_text_ids)}",
            "",
            "=== OCR.html ===",
            f"Найдено блоков: {len(r.ocr_html_blocks)}",
            "",
            "=== Result.json ===",
            f"Найдено блоков: {len(r.result_blocks)}",
            "",
            "=== Document.md ===",
            f"Найдено блоков: {len(r.document_md_blocks)}",
            "",
            "=== Результат ===",
        ]

        if r.is_success:
            lines.append("✅ Верификация пройдена")
        else:
            lines.append("❌ Обнаружены расхождения")

            if r.missing_in_ocr_html:
                lines.append("")
                lines.append("Отсутствуют в ocr.html:")
                for b in r.missing_in_ocr_html:
                    lines.append(f"  Стр. {b.page_index + 1}: {b.id} ({b.block_type})")

            if r.missing_in_result:
                lines.append("")
                lines.append("Отсутствуют в result.json:")
                for b in r.missing_in_result:
                    lines.append(f"  Стр. {b.page_index + 1}: {b.id} ({b.block_type})")

            if r.missing_in_document_md:
                lines.append("")
                lines.append("Отсутствуют в document.md:")
                for b in r.missing_in_document_md:
                    lines.append(f"  Стр. {b.page_index + 1}: {b.id} ({b.block_type})")

            if r.error_blocks:
                lines.append("")
                lines.append("Блоки с ошибками OCR:")
                for b in r.error_blocks:
                    reason = r.error_reasons.get(b.id, "")
                    lines.append(f"  Стр. {b.page_index + 1}: {b.id} ({b.block_type}) — {reason}")

            if r.suspicious_blocks:
                lines.append("")
                lines.append("Подозрительный OCR вывод:")
                for b in r.suspicious_blocks:
                    reason = r.suspicious_reasons.get(b.id, "")
                    lines.append(f"  Стр. {b.page_index + 1}: {b.id} ({b.block_type}) — {reason}")

        QApplication.clipboard().setText("\n".join(lines))

        from app.gui.toast import show_toast

        show_toast(self, "Отчёт скопирован")

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait()
        super().closeEvent(event)
