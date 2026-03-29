"""Единый диалог аудита хранения: R2 + Supabase node_files."""
from __future__ import annotations

import logging
import posixpath
from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
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

# Статусы строк (текстовые маркеры, видны при любой теме)
_STATUS_BOTH = "OK"
_STATUS_R2_ONLY = "R2 only"
_STATUS_DB_ONLY = "DB only"
_STATUS_SIZE_MISMATCH = "Size !="


class _DownloadWorker(QObject):
    """Фоновый воркер для скачивания файлов с R2."""

    progress = Signal(int, int)
    finished = Signal(int, int)

    def __init__(self, keys: list[str], dest_dir: str):
        super().__init__()
        self.keys = keys
        self.dest_dir = dest_dir
        self._cancelled = False

    def run(self):
        r2 = R2Storage()
        ok = fail = 0
        for i, key in enumerate(self.keys):
            if self._cancelled:
                break
            filename = posixpath.basename(key)
            rel = key.split("/crops/", 1)
            if len(rel) == 2:
                local_path = posixpath.join(self.dest_dir, "crops", rel[1])
            else:
                local_path = posixpath.join(self.dest_dir, filename)
            local_path = local_path.replace("/", "\\")

            if r2.download_file(key, local_path, use_cache=False):
                ok += 1
            else:
                fail += 1
            self.progress.emit(i + 1, len(self.keys))
        self.finished.emit(ok, fail)

    def cancel(self):
        self._cancelled = True


class UnifiedNodeFilesDialog(QDialog):
    """Диагностический диалог: сравнение файлов R2 и Supabase node_files."""

    def __init__(self, node: "TreeNode", client, parent=None):
        super().__init__(parent)
        self.node = node
        self.client = client
        self._merged: list[dict] = []
        self._thread: QThread | None = None
        self._worker: _DownloadWorker | None = None

        self.setWindowTitle(f"Файлы узла: {node.name}")
        self.resize(1100, 650)
        self._setup_ui()
        self._load_files()

    # ── UI ──────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Summary
        self.summary_label = QLabel("Загрузка данных...")
        layout.addWidget(self.summary_label)

        # Дерево файлов
        self.tree = QTreeWidget()
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels([
            "Статус", "Тип", "Имя файла", "R2 ключ",
            "Размер R2", "Размер DB", "Дата",
        ])
        self.tree.setSelectionBehavior(QTreeWidget.SelectRows)
        self.tree.setEditTriggers(QTreeWidget.NoEditTriggers)
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnWidth(0, 110)
        self.tree.setColumnWidth(1, 80)
        self.tree.setColumnWidth(2, 200)
        self.tree.setColumnWidth(3, 260)
        self.tree.setColumnWidth(4, 90)
        self.tree.setColumnWidth(5, 90)
        self.tree.setColumnWidth(6, 150)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.tree)

        # Прогресс-бар
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

    # ── Загрузка и слияние ──────────────────────────────

    def _load_files(self):
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("Загрузка...")
        try:
            r2_files = self._fetch_r2_files()
            db_files = self._fetch_db_files()
            self._merged = self._merge(r2_files, db_files)
            self._populate_tree()
        except Exception as e:
            logger.error(f"Unified load error: {e}")
            QMessageBox.critical(self, "Ошибка", f"Ошибка загрузки:\n{e}")
        finally:
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("Обновить")

    def _get_r2_prefixes(self) -> set[str]:
        """Собрать все возможные R2 префиксы для узла."""
        prefixes: set[str] = set()
        # Основной: tree_docs/{node_id}/
        prefixes.add(f"tree_docs/{self.node.id}")
        # Из атрибутов узла
        r2_key = self.node.attributes.get("r2_key", "")
        if r2_key:
            prefixes.add(posixpath.dirname(r2_key))
        return prefixes

    def _fetch_r2_files(self) -> dict[str, dict]:
        """Получить файлы из R2 по всем префиксам."""
        r2 = R2Storage()
        result: dict[str, dict] = {}
        for prefix in self._get_r2_prefixes():
            try:
                objects = r2.list_objects_with_metadata(prefix, use_cache=False)
                for obj in objects:
                    result[obj["Key"]] = obj
            except Exception as e:
                logger.warning(f"R2 list error for prefix {prefix}: {e}")
        return result

    def _fetch_db_files(self) -> dict[str, dict]:
        """Получить записи node_files из Supabase."""
        result: dict[str, dict] = {}
        try:
            path = (
                f"/node_files?"
                f"node_id=eq.{self.node.id}&"
                f"select=id,file_type,file_name,r2_key,file_size,mime_type,created_at,metadata&"
                f"order=created_at.desc"
            )
            resp = self.client._request("get", path)
            if resp and resp.status_code == 200:
                for row in resp.json():
                    r2_key = row.get("r2_key", "")
                    if r2_key:
                        result[r2_key] = row
        except Exception as e:
            logger.warning(f"Supabase node_files error: {e}")
        return result

    def _merge(self, r2_files: dict, db_files: dict) -> list[dict]:
        """Свести данные по r2_key."""
        all_keys = sorted(set(r2_files.keys()) | set(db_files.keys()))
        merged = []
        for key in all_keys:
            r2 = r2_files.get(key)
            db = db_files.get(key)
            r2_size = r2.get("Size", 0) if r2 else None
            db_size = db.get("file_size", 0) if db else None

            if r2 and db:
                if r2_size is not None and db_size is not None and r2_size != db_size:
                    status = _STATUS_SIZE_MISMATCH
                else:
                    status = _STATUS_BOTH
            elif r2 and not db:
                status = _STATUS_R2_ONLY
            else:
                status = _STATUS_DB_ONLY

            merged.append({
                "r2_key": key,
                "status": status,
                "file_type": db.get("file_type", "") if db else self._guess_type(key),
                "file_name": db.get("file_name", "") if db else posixpath.basename(key),
                "r2_size": r2_size,
                "db_size": db_size,
                "date": (
                    r2.get("LastModified") if r2
                    else db.get("created_at", "") if db
                    else ""
                ),
                "in_r2": bool(r2),
            })
        return merged

    # ── Заполнение дерева ──────────────────────────────

    def _populate_tree(self):
        self.tree.clear()

        crops: list[dict] = []
        regular: list[dict] = []
        counts = {_STATUS_BOTH: 0, _STATUS_R2_ONLY: 0, _STATUS_DB_ONLY: 0, _STATUS_SIZE_MISMATCH: 0}

        for item_data in self._merged:
            counts[item_data["status"]] = counts.get(item_data["status"], 0) + 1
            if "/crops/" in item_data["r2_key"]:
                crops.append(item_data)
            else:
                regular.append(item_data)

        # Обычные файлы
        for d in regular:
            item = self._make_item(d)
            self.tree.addTopLevelItem(item)

        # Папка crops
        if crops:
            folder = QTreeWidgetItem([f"crops/ ({len(crops)})", "", "", "", "", "", ""])
            folder.setFlags(folder.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            folder.setCheckState(0, Qt.Unchecked)
            self.tree.addTopLevelItem(folder)
            for d in crops:
                child = self._make_item(d)
                folder.addChild(child)

        # Summary
        total_r2 = counts[_STATUS_BOTH] + counts[_STATUS_R2_ONLY] + counts[_STATUS_SIZE_MISMATCH]
        total_db = counts[_STATUS_BOTH] + counts[_STATUS_DB_ONLY] + counts[_STATUS_SIZE_MISMATCH]
        self.summary_label.setText(
            f"<b>R2:</b> {total_r2} файлов | "
            f"<b>Supabase:</b> {total_db} записей | "
            f"<b>Совпадений:</b> {counts[_STATUS_BOTH]} | "
            f"<b>Только R2:</b> {counts[_STATUS_R2_ONLY]} | "
            f"<b>Только DB:</b> {counts[_STATUS_DB_ONLY]} | "
            f"<b>Размер ≠:</b> {counts[_STATUS_SIZE_MISMATCH]} | "
            f"<b>Кропы:</b> {len(crops)}"
        )
        self.setWindowTitle(
            f"Файлы узла: {self.node.name} ({len(self._merged)})"
        )

    def _make_item(self, d: dict) -> QTreeWidgetItem:
        status = d["status"]
        r2_size_str = self._format_size(d["r2_size"]) if d["r2_size"] is not None else "—"
        db_size_str = self._format_size(d["db_size"]) if d["db_size"] is not None else "—"
        date_str = self._format_datetime(d["date"])

        item = QTreeWidgetItem([
            status,
            d["file_type"],
            d["file_name"],
            d["r2_key"],
            r2_size_str,
            db_size_str,
            date_str,
        ])
        item.setCheckState(0, Qt.Unchecked)
        item.setToolTip(3, d["r2_key"])
        item.setData(0, Qt.UserRole, d["r2_key"])
        item.setData(0, Qt.UserRole + 1, d["in_r2"])
        item.setTextAlignment(4, Qt.AlignRight | Qt.AlignVCenter)
        item.setTextAlignment(5, Qt.AlignRight | Qt.AlignVCenter)

        # Цвет текста статуса (без фона — совместимо с тёмными темами)
        status_color = {
            _STATUS_BOTH: QColor(100, 200, 100),       # зелёный
            _STATUS_R2_ONLY: QColor(220, 200, 80),     # жёлтый
            _STATUS_DB_ONLY: QColor(220, 100, 100),    # красный
            _STATUS_SIZE_MISMATCH: QColor(220, 170, 60), # оранжевый
        }.get(status)
        if status_color:
            item.setForeground(0, status_color)

        return item

    # ── Выбор ──────────────────────────────────────────

    def _get_all_file_items(self) -> list[QTreeWidgetItem]:
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
            if it.checkState(0) == Qt.Checked
            and it.data(0, Qt.UserRole)
            and it.data(0, Qt.UserRole + 1)  # in_r2 = True
        ]

    # ── Двойной клик → браузер ──────────────────────────

    def _on_double_click(self, item: QTreeWidgetItem, column: int):
        r2_key = item.data(0, Qt.UserRole)
        in_r2 = item.data(0, Qt.UserRole + 1)
        if not r2_key or not in_r2:
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

    # ── Скачивание ─────────────────────────────────────

    def _download_selected(self):
        keys = self._get_selected_keys()
        if not keys:
            QMessageBox.information(self, "Скачивание", "Выберите файлы для скачивания (доступны только файлы из R2).")
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
        self._worker.progress.connect(lambda c, t: self.progress_bar.setValue(c))
        self._worker.finished.connect(self._on_download_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

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

    # ── Утилиты ────────────────────────────────────────

    @staticmethod
    def _guess_type(r2_key: str) -> str:
        name = posixpath.basename(r2_key).lower()
        if "/crops/" in r2_key:
            return "crop"
        if name.endswith(".pdf"):
            return "pdf"
        if name.endswith("_ocr.html"):
            return "ocr_html"
        if name.endswith("_document.md") or name.endswith("_result.md"):
            return "result_md"
        if name.endswith(".zip"):
            return "result_zip"
        if name.endswith("_result.json"):
            return "result_json"
        return "unknown"

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
