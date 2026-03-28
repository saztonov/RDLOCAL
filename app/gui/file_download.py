"""Скачивание документов из R2 во временные сессии"""
import logging
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from app.gui.file_transfer_worker import FileTransferWorker, TransferTask, TransferType
from app.gui.temp_session import get_temp_session_manager

logger = logging.getLogger(__name__)


class FileDownloadMixin:
    """Миксин для скачивания документов из R2 во временные сессии"""

    _active_downloads: set = None

    def _update_lock_status(self, node_id: str):
        """Обновить статус блокировки документа"""
        if not node_id:
            self._current_node_locked = False
            return

        try:
            from app.tree_client import TreeClient

            client = TreeClient()
            if client.is_available():
                node = client.get_node(node_id)
                if node:
                    self._current_node_locked = node.is_locked
                    logger.info(f"Document lock status: {self._current_node_locked}")
                    return
        except Exception as e:
            logger.error(f"Failed to get lock status: {e}")

        self._current_node_locked = False

    def _on_tree_file_uploaded_r2(self, node_id: str, r2_key: str, local_path: str = ""):
        """Открыть загруженный файл из R2 в редакторе.

        Если local_path передан, создаём temp-сессию с копией файла
        (без повторного скачивания из R2).
        """
        if local_path and Path(local_path).exists():
            self._open_uploaded_file_via_temp(node_id, r2_key, local_path)
        else:
            self._on_tree_document_selected(node_id, r2_key)

    def _open_uploaded_file_via_temp(self, node_id: str, r2_key: str, local_path: str):
        """Открыть только что загруженный файл без re-download из R2."""
        tsm = get_temp_session_manager()
        workspace = tsm.create_workspace(node_id)
        pdf_path = tsm.get_pdf_path(workspace, r2_key)

        try:
            shutil.copy2(local_path, pdf_path)
        except Exception as e:
            logger.error(f"Failed to seed temp workspace: {e}")
            tsm.cleanup(workspace)
            self._on_tree_document_selected(node_id, r2_key)
            return

        self._current_r2_key = r2_key
        self._current_node_id = node_id
        self._update_lock_status(node_id)
        if hasattr(self, "page_viewer"):
            self.page_viewer.read_only = self._current_node_locked
        if hasattr(self, "move_block_up_btn"):
            self.move_block_up_btn.setEnabled(not self._current_node_locked)
        if hasattr(self, "move_block_down_btn"):
            self.move_block_down_btn.setEnabled(not self._current_node_locked)
        self._open_pdf_file(str(pdf_path), r2_key=r2_key)
        self._current_temp_dir = str(workspace)
        self._current_document_origin = "tree_temp"
        from app.logging_manager import get_logging_manager
        get_logging_manager().switch_to_projects_folder()
        if node_id and hasattr(self, "project_tree_widget"):
            self.project_tree_widget.highlight_document(node_id)

    def _on_tree_document_selected(self, node_id: str, r2_key: str):
        """Открыть документ из дерева (асинхронное скачивание из R2 во temp)"""
        if not r2_key:
            return

        # Инициализация set для отслеживания активных загрузок
        if self._active_downloads is None:
            self._active_downloads = set()

        # Защита от дублирующихся загрузок
        if r2_key in self._active_downloads:
            logger.debug(f"Download already in progress: {r2_key}")
            return

        # Если этот же документ уже открыт в текущей temp-сессии — переиспользуем
        if (
            getattr(self, "_current_node_id", None) == node_id
            and getattr(self, "_current_temp_dir", None)
            and getattr(self, "_current_document_origin", None) == "tree_temp"
        ):
            temp_dir = Path(self._current_temp_dir)
            pdf_path = temp_dir / Path(r2_key).name
            if pdf_path.exists():
                logger.debug(f"Reusing existing temp session for node {node_id}")
                return

        # Создаём новую temp-сессию
        tsm = get_temp_session_manager()
        workspace = tsm.create_workspace(node_id)
        pdf_path = tsm.get_pdf_path(workspace, r2_key)
        local_path = str(pdf_path)

        # Собираем список файлов для скачивания
        tasks = self._build_download_tasks(node_id, r2_key, local_path)

        # Если нет файлов для скачивания (не должно быть с temp, но на всякий)
        if not tasks:
            self._current_r2_key = r2_key
            self._current_node_id = node_id
            self._update_lock_status(node_id)
            if hasattr(self, "page_viewer"):
                self.page_viewer.read_only = self._current_node_locked
            if hasattr(self, "move_block_up_btn"):
                self.move_block_up_btn.setEnabled(not self._current_node_locked)
            if hasattr(self, "move_block_down_btn"):
                self.move_block_down_btn.setEnabled(not self._current_node_locked)
            self._open_pdf_file(local_path, r2_key=r2_key)
            self._current_temp_dir = str(workspace)
            self._current_document_origin = "tree_temp"
            from app.logging_manager import get_logging_manager
            get_logging_manager().switch_to_projects_folder()
            if node_id and hasattr(self, "project_tree_widget"):
                self.project_tree_widget.highlight_document(node_id)
            return

        # Помечаем загрузку как активную
        self._active_downloads.add(r2_key)

        # Сохраняем данные для открытия после завершения загрузки
        self._pending_download_node_id = node_id
        self._pending_download_r2_key = r2_key
        self._pending_download_local_path = local_path
        self._pending_download_temp_dir = str(workspace)
        self._download_errors = []

        # Показываем модальное окно загрузки
        self._download_dialog = QProgressDialog(
            f"Загрузка документа и связанных файлов...",
            "Отмена",
            0,
            len(tasks),
            self,
        )
        self._download_dialog.setWindowTitle("Загрузка")
        self._download_dialog.setWindowModality(Qt.WindowModal)
        self._download_dialog.setMinimumDuration(0)
        self._download_dialog.setAutoReset(False)
        self._download_dialog.setAutoClose(False)
        self._download_dialog.setValue(0)
        self._download_dialog.canceled.connect(self._on_download_canceled)
        self._download_dialog.show()

        # Асинхронное скачивание
        self._download_worker = FileTransferWorker(self)

        for task in tasks:
            self._download_worker.add_task(task)

        # Подключаем сигналы
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.finished_task.connect(self._on_download_task_result)
        self._download_worker.all_finished.connect(self._on_all_downloads_finished)

        # Запускаем
        logger.info(
            f"Starting async download: {r2_key} -> {local_path} ({len(tasks)} files)"
        )
        self._download_worker.start()

    def _build_download_tasks(
        self, node_id: str, r2_key: str, local_path: str
    ) -> list:
        """Собрать список задач для скачивания (PDF + OCR результаты).

        Все файлы скачиваются в temp-папку (parent от local_path).
        use_cache=False для tree-документов — не используем R2DiskCache.
        """
        from app.tree_client import FileType, TreeClient

        tasks = []
        temp_dir = Path(local_path).parent

        # Основной PDF — всегда скачиваем (temp-папка новая)
        tasks.append(
            TransferTask(
                transfer_type=TransferType.DOWNLOAD,
                local_path=local_path,
                r2_key=r2_key,
                node_id=node_id,
                use_cache=False,
            )
        )

        # Типы файлов для скачивания (без кропов и аннотаций)
        download_file_types = {
            FileType.OCR_HTML,
            FileType.RESULT_JSON,
            FileType.RESULT_MD,
        }

        # Проверяем есть ли дополнительные файлы в node_files
        try:
            from rd_core.r2_storage import R2Storage

            client = TreeClient()
            node_files = client.get_node_files(node_id)
            r2 = R2Storage()

            for nf in node_files:
                if nf.file_type == FileType.PDF:
                    continue
                if nf.file_type in (FileType.CROP, FileType.CROPS_FOLDER):
                    continue
                if nf.file_type not in download_file_types:
                    continue

                try:
                    if not r2.exists(nf.r2_key):
                        logger.warning(f"File not found in R2, skipping: {nf.r2_key}")
                        continue
                except Exception as e:
                    logger.warning(f"R2 exists check failed for {nf.r2_key}: {e}")

                file_local_path = temp_dir / Path(nf.r2_key).name

                tasks.append(
                    TransferTask(
                        transfer_type=TransferType.DOWNLOAD,
                        local_path=str(file_local_path),
                        r2_key=nf.r2_key,
                        node_id=node_id,
                        timeout=15,
                        use_cache=False,
                    )
                )
                logger.debug(f"Added download task: {nf.file_type.value} -> {file_local_path}")

        except Exception as e:
            logger.warning(f"Failed to get additional files for download: {e}")

        return tasks

    def _on_download_canceled(self):
        """Отмена загрузки пользователем"""
        logger.info("Download canceled by user")
        if hasattr(self, "_download_worker") and self._download_worker:
            self._download_worker.stop()

    def _on_download_progress(self, message: str, current: int, total: int):
        """Обновление прогресса загрузки"""
        if hasattr(self, "_download_dialog") and self._download_dialog:
            self._download_dialog.setLabelText(message)
            self._download_dialog.setValue(current)
        self.show_transfer_progress(message, current, total)

    def _on_download_task_result(self, task: TransferTask, success: bool, error: str):
        """Сохранение результата загрузки файла (без открытия)"""
        if not success:
            if hasattr(self, "_download_errors"):
                self._download_errors.append(f"{task.r2_key}: {error}")
            logger.error(f"Download failed: {task.r2_key} - {error}")
        else:
            logger.info(f"File downloaded from R2: {task.r2_key}")

    def _on_all_downloads_finished(self):
        """Все загрузки завершены - открываем PDF"""
        # Проверяем отмену до закрытия диалога
        was_canceled = (
            hasattr(self, "_download_worker")
            and self._download_worker
            and not self._download_worker._running
        )

        # Закрываем диалог прогресса
        if hasattr(self, "_download_dialog") and self._download_dialog:
            self._download_dialog.close()
            self._download_dialog = None

        self.hide_transfer_progress()

        # Убираем из активных загрузок
        if self._active_downloads and hasattr(self, "_pending_download_r2_key"):
            self._active_downloads.discard(self._pending_download_r2_key)

        pending_temp_dir = getattr(self, "_pending_download_temp_dir", None)

        # При отмене — не открываем PDF, удаляем temp
        if was_canceled:
            logger.info("Download was canceled, not opening PDF")
            if pending_temp_dir:
                get_temp_session_manager().cleanup(pending_temp_dir)
            self._download_worker = None
            return

        # Проверяем ошибки
        if hasattr(self, "_download_errors") and self._download_errors:
            main_pdf_error = None
            for err in self._download_errors:
                if (
                    hasattr(self, "_pending_download_r2_key")
                    and self._pending_download_r2_key in err
                ):
                    main_pdf_error = err
                    break

            if main_pdf_error:
                QMessageBox.critical(
                    self, "Ошибка", f"Не удалось скачать PDF:\n{main_pdf_error}"
                )
                if pending_temp_dir:
                    get_temp_session_manager().cleanup(pending_temp_dir)
                self._download_worker = None
                return
            else:
                logger.warning(
                    f"Some files failed to download: {self._download_errors}"
                )

        # Открываем основной PDF
        if (
            hasattr(self, "_pending_download_local_path")
            and Path(self._pending_download_local_path).exists()
        ):
            self._current_r2_key = self._pending_download_r2_key
            self._current_node_id = self._pending_download_node_id

            # Проверяем блокировку документа
            self._update_lock_status(self._pending_download_node_id)
            if hasattr(self, "page_viewer"):
                self.page_viewer.read_only = self._current_node_locked
            if hasattr(self, "move_block_up_btn"):
                self.move_block_up_btn.setEnabled(not self._current_node_locked)
            if hasattr(self, "move_block_down_btn"):
                self.move_block_down_btn.setEnabled(not self._current_node_locked)
            self._open_pdf_file(
                self._pending_download_local_path, r2_key=self._pending_download_r2_key
            )
            self._current_temp_dir = pending_temp_dir
            self._current_document_origin = "tree_temp"
            from app.logging_manager import get_logging_manager
            get_logging_manager().switch_to_projects_folder()

            # Подсветить документ в дереве
            if self._pending_download_node_id and hasattr(self, "project_tree_widget"):
                self.project_tree_widget.highlight_document(
                    self._pending_download_node_id
                )
        else:
            # PDF не скачался — cleanup
            if pending_temp_dir:
                get_temp_session_manager().cleanup(pending_temp_dir)

        self._download_worker = None
