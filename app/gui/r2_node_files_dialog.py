"""Диалог для просмотра и скачивания файлов узла с R2"""
from __future__ import annotations

import logging
import posixpath
from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rd_core.r2_storage import R2Storage

if TYPE_CHECKING:
    from app.tree_client import TreeNode

logger = logging.getLogger(__name__)


class _DownloadWorker(QObject):
    """Фоновый воркер для скачивания файлов с R2."""

    progress = Signal(int, int)  # (current, total)
    file_done = Signal(str, bool)  # (r2_key, success)
    finished = Signal(int, int)  # (success_count, fail_count)

    def __init__(self, keys: list[str], dest_dir: str):
        super().__init__()
        self.keys = keys
        self.dest_dir = dest_dir
        self._cancelled = False

    def run(self):
        r2 = R2Storage()
        ok = 0
        fail = 0
        for i, key in enumerate(self.keys):
            if self._cancelled:
                break
            filename = posixpath.basename(key)
            # Сохраняем структуру: crops/ подпапка
            rel = key.split("/crops/", 1)
            if len(rel) == 2:
                local_path = posixpath.join(self.dest_dir, "crops", rel[1])
            else:
                local_path = posixpath.join(self.dest_dir, filename)
            local_path = local_path.replace("/", "\\")

            success = r2.download_file(key, local_path, use_cache=False)
            if success:
                ok += 1
            else:
                fail += 1
            self.file_done.emit(key, success)
            self.progress.emit(i + 1, len(self.keys))

        self.finished.emit(ok, fail)

    def cancel(self):
        self._cancelled = True


class R2NodeFilesDialog(QDialog):
    """Диалог для отображения и скачивания файлов узла с R2."""

    def __init__(self, node: "TreeNode", parent=None):
        super().__init__(parent)
        self.node = node
        self._objects: list[dict] = []
        self._thread: QThread | None = None
        self._worker: _DownloadWorker | None = None

        self.setWindowTitle(f"Файлы на R2: {node.name}")
        self.resize(950, 600)
        self._setup_ui()
        self._load_files()

    # ── UI ──────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Информация
        r2_key = self.node.attributes.get("r2_key", "")
        prefix = posixpath.dirname(r2_key) if r2_key else f"tree_docs/{self.node.id}"
        info = QLabel(
            f"<b>Узел:</b> {self.node.name}<br>"
            f"<b>R2 префикс:</b> {prefix}"
        )
        layout.addWidget(info)
        self._prefix = prefix

        # Таблица
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Имя файла", "Размер", "Дата создания", "R2 ключ"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 300)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 170)
        layout.addWidget(self.table)

        # Прогресс-бар (скрыт по умолчанию)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Кнопки
        btn_layout = QHBoxLayout()

        self.select_all_btn = QPushButton("Выбрать все")
        self.select_all_btn.clicked.connect(self._toggle_select_all)
        btn_layout.addWidget(self.select_all_btn)

        self.download_btn = QPushButton("Скачать выбранные")
        self.download_btn.clicked.connect(self._download_selected)
        btn_layout.addWidget(self.download_btn)

        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.clicked.connect(self._load_files)
        btn_layout.addWidget(self.refresh_btn)

        btn_layout.addStretch()

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    # ── Загрузка списка ─────────────────────────────────

    def _load_files(self):
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("⏳ Загрузка...")
        try:
            r2 = R2Storage()
            self._objects = r2.list_objects_with_metadata(self._prefix, use_cache=False)
            self._populate_table()
        except Exception as e:
            logger.error(f"R2 list error: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось получить список файлов:\n{e}")
        finally:
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("Обновить")

    def _populate_table(self):
        self.table.setRowCount(len(self._objects))
        for row, obj in enumerate(self._objects):
            key: str = obj["Key"]

            # Чекбокс
            cb = QCheckBox()
            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.addWidget(cb)
            cb_layout.setAlignment(Qt.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)

            # Имя = относительный путь от префикса
            rel_name = key[len(self._prefix):].lstrip("/") if key.startswith(self._prefix) else posixpath.basename(key)
            name_item = QTableWidgetItem(rel_name)
            name_item.setToolTip(key)
            name_item.setData(Qt.UserRole, key)

            # Размер
            size_item = QTableWidgetItem(self._format_size(obj.get("Size", 0)))
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            # Дата
            last_modified = obj.get("LastModified")
            date_str = self._format_datetime(last_modified) if last_modified else ""
            date_item = QTableWidgetItem(date_str)

            # R2 ключ
            key_item = QTableWidgetItem(key)
            key_item.setToolTip(key)

            self.table.setCellWidget(row, 0, cb_widget)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, size_item)
            self.table.setItem(row, 2, date_item)
            self.table.setItem(row, 3, key_item)

        self.setWindowTitle(f"Файлы на R2: {self.node.name} ({len(self._objects)})")

    # ── Выбор ───────────────────────────────────────────

    def _get_checkboxes(self) -> list[tuple[int, QCheckBox]]:
        result = []
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            if widget:
                cb = widget.findChild(QCheckBox)
                if cb:
                    result.append((row, cb))
        return result

    def _toggle_select_all(self):
        cbs = self._get_checkboxes()
        all_checked = all(cb.isChecked() for _, cb in cbs) if cbs else False
        for _, cb in cbs:
            cb.setChecked(not all_checked)
        self.select_all_btn.setText("Снять все" if not all_checked else "Выбрать все")

    def _get_selected_keys(self) -> list[str]:
        keys = []
        for row, cb in self._get_checkboxes():
            if cb.isChecked():
                item = self.table.item(row, 0)
                if item:
                    keys.append(item.data(Qt.UserRole))
        return keys

    # ── Скачивание ──────────────────────────────────────

    def _download_selected(self):
        keys = self._get_selected_keys()
        if not keys:
            QMessageBox.information(self, "Скачивание", "Выберите файлы для скачивания.")
            return

        dest_dir = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения")
        if not dest_dir:
            return

        self._set_downloading(True)
        self.progress_bar.setRange(0, len(keys))
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

        self._thread = QThread()
        self._worker = _DownloadWorker(keys, dest_dir)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_download_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    def _on_progress(self, current: int, total: int):
        self.progress_bar.setValue(current)

    def _on_download_finished(self, ok: int, fail: int):
        self._set_downloading(False)
        self.progress_bar.setVisible(False)
        msg = f"Скачано: {ok}"
        if fail:
            msg += f", ошибок: {fail}"
        QMessageBox.information(self, "Скачивание завершено", msg)

    def _set_downloading(self, downloading: bool):
        self.download_btn.setEnabled(not downloading)
        self.refresh_btn.setEnabled(not downloading)
        self.select_all_btn.setEnabled(not downloading)

    # ── Утилиты ─────────────────────────────────────────

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes == 0:
            return "0 B"
        elif size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    @staticmethod
    def _format_datetime(dt) -> str:
        if not dt:
            return ""
        if isinstance(dt, datetime):
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        try:
            parsed = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(dt)

    def closeEvent(self, event):
        if self._worker:
            self._worker.cancel()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        super().closeEvent(event)
