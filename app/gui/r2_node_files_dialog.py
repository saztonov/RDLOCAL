"""Диалог для просмотра и скачивания файлов узла с R2"""
from __future__ import annotations

import logging
import posixpath
from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
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

        # Дерево файлов
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Имя файла", "Размер", "Дата создания", "R2 ключ"])
        self.tree.setSelectionBehavior(QTreeWidget.SelectRows)
        self.tree.setEditTriggers(QTreeWidget.NoEditTriggers)
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 170)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.tree)

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
        self.refresh_btn.setText("Загрузка...")
        try:
            r2 = R2Storage()
            self._objects = r2.list_objects_with_metadata(self._prefix, use_cache=False)
            self._populate_tree()
        except Exception as e:
            logger.error(f"R2 list error: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось получить список файлов:\n{e}")
        finally:
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("Обновить")

    def _populate_tree(self):
        self.tree.clear()

        crops: list[tuple[str, dict]] = []
        regular: list[tuple[str, dict]] = []

        for obj in self._objects:
            key: str = obj["Key"]
            rel = key[len(self._prefix):].lstrip("/") if key.startswith(self._prefix) else posixpath.basename(key)
            if rel.startswith("crops/"):
                crops.append((rel, obj))
            else:
                regular.append((rel, obj))

        # Обычные файлы
        for rel_name, obj in regular:
            item = self._make_item(rel_name, obj)
            self.tree.addTopLevelItem(item)

        # Папка crops
        if crops:
            folder = QTreeWidgetItem([f"crops/ ({len(crops)})", "", "", ""])
            folder.setFlags(folder.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            folder.setCheckState(0, Qt.Unchecked)
            self.tree.addTopLevelItem(folder)
            for rel_name, obj in crops:
                child_name = rel_name[len("crops/"):]
                child = self._make_item(child_name, obj)
                folder.addChild(child)

        self.setWindowTitle(f"Файлы на R2: {self.node.name} ({len(self._objects)})")

    def _make_item(self, display_name: str, obj: dict) -> QTreeWidgetItem:
        key = obj["Key"]
        size_str = self._format_size(obj.get("Size", 0))
        date_str = self._format_datetime(obj.get("LastModified")) if obj.get("LastModified") else ""

        item = QTreeWidgetItem([display_name, size_str, date_str, key])
        item.setCheckState(0, Qt.Unchecked)
        item.setToolTip(0, key)
        item.setData(0, Qt.UserRole, key)
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        return item

    # ── Выбор ───────────────────────────────────────────

    def _get_all_file_items(self) -> list[QTreeWidgetItem]:
        """Все листовые items (файлы, не папки)."""
        items = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.childCount() > 0:
                for j in range(top.childCount()):
                    items.append(top.child(j))
            else:
                items.append(top)
        return items

    def _toggle_select_all(self):
        items = self._get_all_file_items()
        all_checked = all(it.checkState(0) == Qt.Checked for it in items) if items else False
        state = Qt.Unchecked if all_checked else Qt.Checked
        for it in items:
            it.setCheckState(0, state)
        self.select_all_btn.setText("Снять все" if not all_checked else "Выбрать все")

    def _get_selected_keys(self) -> list[str]:
        return [
            it.data(0, Qt.UserRole)
            for it in self._get_all_file_items()
            if it.checkState(0) == Qt.Checked and it.data(0, Qt.UserRole)
        ]

    # ── Двойной клик → браузер ───────────────────────────

    def _on_double_click(self, item: QTreeWidgetItem, column: int):
        """Двойной клик → открыть файл в браузере через presigned URL."""
        r2_key = item.data(0, Qt.UserRole)
        if not r2_key:
            return
        try:
            url = R2Storage().generate_presigned_url(r2_key)
            if url:
                QDesktopServices.openUrl(QUrl(url))
            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось сгенерировать ссылку.")
        except Exception as e:
            logger.error(f"Presigned URL error: {e}")
            QMessageBox.warning(self, "Ошибка", f"Не удалось открыть файл:\n{e}")

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
