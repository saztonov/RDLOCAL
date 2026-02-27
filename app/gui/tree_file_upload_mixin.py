"""Миксин загрузки файлов в дерево проектов."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from app.gui.file_transfer_worker import FileTransferWorker, TransferTask, TransferType

logger = logging.getLogger(__name__)


class TreeFileUploadMixin:
    """Загрузка файлов в R2 и создание документов в дереве."""

    def _upload_file(self, node):
        """Добавить файл в папку заданий (асинхронная загрузка в R2)"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Выберите файлы", "", "PDF Files (*.pdf);;All Files (*)"
        )
        if not paths:
            return

        # Создаём worker для асинхронной загрузки
        self._upload_worker = FileTransferWorker(self)
        self._upload_target_node = node

        for path in paths:
            file_path = Path(path)
            filename = file_path.name
            file_size = file_path.stat().st_size
            r2_key = f"tree_docs/{node.id}/{filename}"

            task = TransferTask(
                transfer_type=TransferType.UPLOAD,
                local_path=str(file_path),
                r2_key=r2_key,
                node_id="",  # Будет заполнен после создания узла
                file_size=file_size,
                filename=filename,
                parent_node_id=node.id,
            )
            self._upload_worker.add_task(task)

        # Подключаем сигналы
        main_window = self.window()
        self._upload_worker.progress.connect(
            lambda msg, cur, tot: main_window.show_transfer_progress(msg, cur, tot)
        )
        self._upload_worker.finished_task.connect(self._on_upload_task_finished)
        self._upload_worker.all_finished.connect(self._on_all_uploads_finished)

        # Запускаем
        self._upload_worker.start()

    def _on_upload_task_finished(self, task: TransferTask, success: bool, error: str):
        """Обработка завершения загрузки одного файла"""
        if not success:
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Не удалось загрузить файл в R2:\n{task.filename}\n{error}",
            )
            return

        logger.info(f"File uploaded to R2: {task.r2_key}")

        # Проверка уникальности имени в папке
        if not self._check_name_unique(task.parent_node_id, task.filename):
            QMessageBox.warning(
                self,
                "Ошибка",
                f"Файл с именем '{task.filename}' уже существует в этой папке",
            )
            return

        # Копируем файл в локальный кэш ДО создания узла (чтобы открытие было мгновенным)
        self._copy_to_cache(task.local_path, task.r2_key)

        try:
            doc_node = self.client.add_document(
                parent_id=task.parent_node_id,
                name=task.filename,
                r2_key=task.r2_key,
                file_size=task.file_size,
            )

            # Re-fetch parent_item ПОСЛЕ сетевого вызова: за это время
            # auto-refresh мог перестроить дерево и удалить старый item
            parent_item = self._node_map.get(task.parent_node_id)

            if parent_item:
                try:
                    if parent_item.childCount() == 1:
                        child = parent_item.child(0)
                        if child.data(0, self._get_user_role()) == "placeholder":
                            parent_item.removeChild(child)

                    child_item = self._item_builder.create_item(doc_node)
                    parent_item.addChild(child_item)
                    parent_item.setExpanded(True)
                    self.tree.setCurrentItem(child_item)
                    self.highlight_document(doc_node.id)
                except RuntimeError:
                    logger.warning(
                        f"Parent item for {task.parent_node_id} deleted during UI update, "
                        f"document {doc_node.id} created but not shown in tree"
                    )

            logger.info(f"Document added: {doc_node.id} with r2_key={task.r2_key}")
            # Сигнал с node_id и r2_key для открытия
            self.file_uploaded_r2.emit(doc_node.id, task.r2_key)

        except Exception as e:
            logger.exception(f"Failed to add document: {e}")
            QMessageBox.critical(
                self, "Ошибка", f"Файл загружен в R2, но не добавлен в дерево:\n{e}"
            )

    def _on_all_uploads_finished(self):
        """Все загрузки завершены"""
        main_window = self.window()
        main_window.hide_transfer_progress()
        self._upload_worker = None
