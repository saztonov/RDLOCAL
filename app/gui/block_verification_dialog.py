"""Диалог верификации блоков - сравнение annotation.json, ocr.html, result.json"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import List, Optional, Set

from PySide6.QtCore import Qt, QThread, Signal
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

logger = logging.getLogger(__name__)


@dataclass
class BlockInfo:
    """Информация о блоке"""

    id: str
    page_index: int
    block_type: str  # "text", "image"
    category_code: Optional[str] = None  # "stamp" для штампов
    linked_block_id: Optional[str] = None  # ID связанного блока (для TEXT→IMAGE)

    @property
    def is_stamp(self) -> bool:
        return self.category_code == "stamp"


@dataclass
class VerificationResult:
    """Результат верификации"""

    # Блоки в annotation.json
    ann_total: int = 0
    ann_text: int = 0
    ann_image: int = 0
    ann_stamp: int = 0
    ann_blocks: List[BlockInfo] = field(default_factory=list)

    # Блоки в ocr.html (без штампов)
    ocr_html_blocks: Set[str] = field(default_factory=set)  # block IDs

    # Блоки в result.json
    result_blocks: Set[str] = field(default_factory=set)  # block IDs

    # Блоки в document.md (без штампов)
    document_md_blocks: Set[str] = field(default_factory=set)  # block IDs

    # Ожидаемые блоки (без штампов)
    expected_blocks: Set[str] = field(default_factory=set)

    # Embedded TEXT блоки (связаны с IMAGE через linked_block_id)
    embedded_text_ids: Set[str] = field(default_factory=set)

    # Отсутствующие блоки
    missing_in_ocr_html: List[BlockInfo] = field(default_factory=list)
    missing_in_result: List[BlockInfo] = field(default_factory=list)
    missing_in_document_md: List[BlockInfo] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        """Верификация прошла успешно?"""
        return (
            len(self.missing_in_ocr_html) == 0
            and len(self.missing_in_result) == 0
            and len(self.missing_in_document_md) == 0
        )


class VerificationWorker(QThread):
    """Фоновый worker для верификации"""

    progress = Signal(str)
    finished = Signal(object)  # VerificationResult или str (ошибка)

    def __init__(self, r2_key: str, node_id: str = ""):
        super().__init__()
        self.r2_key = r2_key
        self.node_id = node_id

    def run(self):
        try:
            result = self._verify()
            self.finished.emit(result)
        except Exception as e:
            logger.error(f"Verification failed: {e}", exc_info=True)
            self.finished.emit(f"Ошибка верификации: {e}")

    def _verify(self) -> VerificationResult:
        from rd_core.r2_storage import R2Storage

        r2 = R2Storage()
        result = VerificationResult()

        # Формируем ключи файлов
        pdf_path = PurePosixPath(self.r2_key)
        pdf_stem = pdf_path.stem
        pdf_parent = str(pdf_path.parent)

        ocr_r2_key = f"{pdf_parent}/{pdf_stem}_ocr.html"
        res_r2_key = f"{pdf_parent}/{pdf_stem}_result.json"
        md_r2_key = f"{pdf_parent}/{pdf_stem}_document.md"

        # 1. Загружаем и парсим аннотацию из Supabase
        self.progress.emit("Загрузка аннотации...")
        ann_data = None
        if self.node_id:
            from app.tree_client import TreeClient
            client = TreeClient()
            ann_data = client.get_annotation(self.node_id)

        if not ann_data:
            raise ValueError("Аннотация не найдена в базе данных")

        for page in ann_data.get("pages", []):
            page_num = page.get("page_number", 0)
            for block in page.get("blocks", []):
                block_id = block.get("id", "")
                block_type = block.get("block_type", "text")
                category_code = block.get("category_code")
                linked_block_id = block.get("linked_block_id")

                block_info = BlockInfo(
                    id=block_id,
                    page_index=page_num,
                    block_type=block_type,
                    category_code=category_code,
                    linked_block_id=linked_block_id,
                )
                result.ann_blocks.append(block_info)
                result.ann_total += 1

                if block_info.is_stamp:
                    result.ann_stamp += 1
                elif block_type == "text":
                    result.ann_text += 1
                    result.expected_blocks.add(block_id)
                elif block_type == "image":
                    result.ann_image += 1
                    result.expected_blocks.add(block_id)

        # Определяем embedded TEXT блоки (связаны с IMAGE через linked_block_id)
        all_blocks_by_id = {b.id: b for b in result.ann_blocks}
        for block_info in result.ann_blocks:
            if block_info.block_type == "text" and block_info.linked_block_id:
                linked_id = block_info.linked_block_id
                if linked_id in all_blocks_by_id:
                    linked_block = all_blocks_by_id[linked_id]
                    if linked_block.block_type == "image":
                        result.embedded_text_ids.add(block_info.id)

        # 2. Загружаем и парсим ocr.html
        self.progress.emit("Загрузка ocr.html...")
        ocr_content = r2.download_text(ocr_r2_key)
        if ocr_content:
            # Ищем маркеры BLOCK: XXXX-XXXX-XXX
            block_pattern = re.compile(
                r"BLOCK:\s*([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{3})"
            )
            for match in block_pattern.finditer(ocr_content):
                result.ocr_html_blocks.add(match.group(1))

        # 3. Загружаем и парсим result.json
        self.progress.emit("Загрузка result.json...")
        res_content = r2.download_text(res_r2_key)
        if res_content:
            res_data = json.loads(res_content)
            for page in res_data.get("pages", []):
                for block in page.get("blocks", []):
                    block_id = block.get("id", "")
                    if block_id:
                        result.result_blocks.add(block_id)

        # 4. Загружаем и парсим document.md
        self.progress.emit("Загрузка document.md...")
        md_content = r2.download_text(md_r2_key)
        if md_content:
            # Ищем маркеры в формате: ### BLOCK [TYPE]: XXXX-XXXX-XXX
            block_pattern = re.compile(
                r"###\s+BLOCK\s+\[[A-Z]+\]:\s*([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{3})"
            )
            for match in block_pattern.finditer(md_content):
                result.document_md_blocks.add(match.group(1))

        # 5. Находим отсутствующие блоки
        self.progress.emit("Анализ расхождений...")

        for block_info in result.ann_blocks:
            if block_info.is_stamp:
                continue  # Штампы не проверяем

            if block_info.id not in result.ocr_html_blocks:
                result.missing_in_ocr_html.append(block_info)

            if block_info.id not in result.result_blocks:
                result.missing_in_result.append(block_info)

            if block_info.id not in result.document_md_blocks:
                # Не считать отсутствующим, если это embedded TEXT (связан с IMAGE)
                if block_info.id not in result.embedded_text_ids:
                    result.missing_in_document_md.append(block_info)

        return result


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
            self.verdict_label.setText(
                f"<span style='color: #ff6b6b; font-size: 16px;'>❌ Обнаружены расхождения</span><br><br>"
                f"<b>Ожидалось блоков (без штампов):</b> {expected_count}<br>"
                f"<b>Отсутствует в ocr.html:</b> {missing_ocr}<br>"
                f"<b>Отсутствует в result.json:</b> {missing_res}<br>"
                f"<b>Отсутствует в document.md:</b> {missing_md}"
            )

            # Детали отсутствующих блоков
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

        QApplication.clipboard().setText("\n".join(lines))

        from app.gui.toast import show_toast

        show_toast(self, "Отчёт скопирован")

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait()
        super().closeEvent(event)
