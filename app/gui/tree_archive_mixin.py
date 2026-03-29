"""Миксин для скачивания полного архива документа из дерева проектов."""
from __future__ import annotations

import logging
import os
import posixpath
import subprocess
import sys
import tempfile
import zipfile
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog

from app.tree_models import FileType
from rd_core.r2_storage import R2Storage

if TYPE_CHECKING:
    from app.tree_client import TreeNode

logger = logging.getLogger(__name__)


class _ArchiveWorker(QObject):
    """Фоновый воркер: скачивает файлы из R2 и собирает ZIP-архив."""

    progress = Signal(int, int, str)  # (current, total, filename)
    finished = Signal(str)  # zip_path
    error = Signal(str)  # error message

    def __init__(self, r2_keys: list[tuple[str, str]], zip_path: str):
        super().__init__()
        self.r2_keys = r2_keys
        self.zip_path = zip_path
        self._cancelled = False

    def run(self):
        r2 = R2Storage()
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                downloaded: list[tuple[str, str]] = []
                for i, (r2_key, arc_name) in enumerate(self.r2_keys):
                    if self._cancelled:
                        return
                    self.progress.emit(i + 1, len(self.r2_keys), arc_name)
                    local_path = os.path.join(tmp_dir, arc_name)
                    if r2.download_file(r2_key, local_path, use_cache=True):
                        downloaded.append((local_path, arc_name))

                if not downloaded:
                    self.error.emit("Не удалось скачать ни одного файла")
                    return

                with zipfile.ZipFile(self.zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for local_path, arc_name in downloaded:
                        zf.write(local_path, arc_name)

                self.finished.emit(self.zip_path)
        except Exception as e:
            logger.exception("Ошибка создания архива")
            self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True


class TreeArchiveMixin:
    """Миксин для скачивания полного архива документа (PDF + OCR результаты)."""

    def _download_full_archive(self, node: TreeNode):
        """Скачать ZIP-архив с итоговыми документами узла."""
        r2_key = node.attributes.get("r2_key", "")
        if not r2_key:
            return

        prefix = posixpath.dirname(r2_key)
        stem = PurePosixPath(r2_key).stem

        # Собираем файлы из node_files
        node_files = self.client.get_node_files(node.id)
        files_by_type = {}
        for nf in node_files:
            ft = nf.file_type if isinstance(nf.file_type, str) else nf.file_type.value
            files_by_type[ft] = nf

        # Список файлов для архива: (r2_key, имя в архиве)
        r2_keys: list[tuple[str, str]] = []

        # PDF — всегда из атрибутов узла
        r2_keys.append((r2_key, PurePosixPath(r2_key).name))

        # OCR результаты — из node_files или fallback по конвенции
        type_map = {
            FileType.OCR_HTML.value: f"{stem}_ocr.html",
            FileType.RESULT_MD.value: f"{stem}_document.md",
        }
        for ft_value, fallback_name in type_map.items():
            if ft_value in files_by_type:
                nf = files_by_type[ft_value]
                r2_keys.append((nf.r2_key, PurePosixPath(nf.r2_key).name))
            else:
                r2_keys.append((f"{prefix}/{fallback_name}", fallback_name))

        # Выбор пути сохранения
        default_name = f"{stem}.zip"
        zip_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить архив", default_name, "ZIP архив (*.zip)"
        )
        if not zip_path:
            return

        self._start_archive_download(r2_keys, zip_path)

    def _start_archive_download(
        self, r2_keys: list[tuple[str, str]], zip_path: str
    ):
        """Запустить фоновое скачивание и сборку архива."""
        self._archive_progress = QProgressDialog(
            "Подготовка архива...", "Отмена", 0, len(r2_keys), self
        )
        self._archive_progress.setWindowTitle("Скачивание архива")
        self._archive_progress.setModal(True)
        self._archive_progress.setMinimumDuration(0)

        self._archive_thread = QThread()
        self._archive_worker = _ArchiveWorker(r2_keys, zip_path)
        self._archive_worker.moveToThread(self._archive_thread)

        self._archive_thread.started.connect(self._archive_worker.run)
        self._archive_worker.progress.connect(self._on_archive_progress)
        self._archive_worker.finished.connect(self._on_archive_finished)
        self._archive_worker.error.connect(self._on_archive_error)
        self._archive_worker.finished.connect(self._archive_thread.quit)
        self._archive_worker.error.connect(self._archive_thread.quit)
        self._archive_progress.canceled.connect(self._archive_worker.cancel)

        self._archive_worker.finished.connect(self._archive_worker.deleteLater)
        self._archive_thread.finished.connect(self._archive_thread.deleteLater)

        self._archive_thread.start()

    def _on_archive_progress(self, current: int, total: int, filename: str):
        self._archive_progress.setValue(current)
        self._archive_progress.setLabelText(
            f"Скачивание {current}/{total}: {filename}"
        )

    def _on_archive_finished(self, zip_path: str):
        self._archive_progress.close()
        QMessageBox.information(self, "Готово", f"Архив сохранён:\n{zip_path}")
        _reveal_in_explorer(zip_path)

    def _on_archive_error(self, error_msg: str):
        self._archive_progress.close()
        QMessageBox.critical(
            self, "Ошибка", f"Ошибка создания архива:\n{error_msg}"
        )


def _reveal_in_explorer(path: str) -> None:
    """Открыть файловый менеджер с выделением указанного файла."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception:
        # Fallback: просто открыть папку
        try:
            folder = os.path.dirname(path)
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception:
            logger.warning(f"Не удалось открыть папку: {path}", exc_info=True)
