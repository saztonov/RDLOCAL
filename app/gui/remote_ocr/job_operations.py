"""Mixin для операций с Remote OCR задачами (CRUD, pause/resume)"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox

from app.gui.remote_ocr.table_manager import JOB_ID_ROLE

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class JobOperationsMixin:
    """Миксин для операций с задачами: создание, удаление, пауза, возобновление, перезапуск"""

    def _clean_old_ocr_results(
        self, node_id: str, r2_key: str, r2, blocks_to_reprocess=None
    ):
        """Очистить старые результаты OCR перед новым распознаванием.

        Args:
            node_id: ID узла в дереве проектов
            r2_key: R2 ключ PDF файла
            r2: объект R2Storage
            blocks_to_reprocess: список ID блоков для переобработки (smart mode).
                Если None — полная очистка (кропы + node_files + все ocr_text).
                Если задан — очищаем только ocr_text указанных блоков,
                кропы и node_files не трогаем (сервер обновит в correction_mode).
        """
        import shutil
        from pathlib import PurePosixPath

        from app.gui.folder_settings_dialog import get_projects_dir
        from app.tree_client import FileType, TreeClient

        is_smart_mode = blocks_to_reprocess is not None

        try:
            pdf_stem = Path(r2_key).stem
            r2_prefix = str(PurePosixPath(r2_key).parent)
            projects_dir = get_projects_dir()

            if not is_smart_mode:
                # 1. Удаляем кропы из R2 и локального кэша
                crops_prefix = f"{r2_prefix}/crops/{pdf_stem}/"
                crop_keys = r2.list_files(crops_prefix)

                if crop_keys:
                    deleted_keys, errors = r2.delete_objects_batch(crop_keys)
                    logger.debug(f"Deleted {len(deleted_keys)} crops from R2")
                    if errors:
                        logger.warning(
                            f"Failed to delete {len(errors)} crops from R2"
                        )

                    if projects_dir:
                        for crop_key in deleted_keys:
                            rel = (
                                crop_key[len("tree_docs/"):]
                                if crop_key.startswith("tree_docs/")
                                else crop_key
                            )
                            crop_local = Path(projects_dir) / "cache" / rel
                            if crop_local.exists():
                                crop_local.unlink()

                # Удаляем папку crops из кэша
                if projects_dir:
                    rel_prefix = (
                        r2_prefix[len("tree_docs/"):]
                        if r2_prefix.startswith("tree_docs/")
                        else r2_prefix
                    )
                    crops_folder = (
                        Path(projects_dir)
                        / "cache"
                        / rel_prefix
                        / "crops"
                        / pdf_stem
                    )
                    if crops_folder.exists():
                        shutil.rmtree(crops_folder, ignore_errors=True)

                # 2. Удаляем записи из node_files (CROP)
                client = TreeClient()
                node_files = client.get_node_files(node_id)
                for nf in node_files:
                    if nf.file_type == FileType.CROP:
                        client.delete_node_file(nf.id)

            # 3. Очищаем ocr_text в блоках текущего документа
            reprocess_set = set(blocks_to_reprocess) if blocks_to_reprocess else None

            if self.main_window.annotation_document:
                cleared = 0
                for page in self.main_window.annotation_document.pages:
                    for block in page.blocks:
                        if hasattr(block, "ocr_text") and block.ocr_text:
                            if reprocess_set is None or block.id in reprocess_set:
                                block.ocr_text = None
                                cleared += 1

                # В smart mode всегда загружаем annotation в R2,
                # чтобы сервер увидел новые блоки при merge
                should_upload = cleared > 0 or is_smart_mode
                if should_upload:
                    from app.gui.file_operations import (
                        get_annotation_path,
                        get_annotation_r2_key,
                    )
                    from rd_core.annotation_io import AnnotationIO

                    pdf_path = getattr(self.main_window, "_current_pdf_path", None)
                    if pdf_path:
                        ann_path = get_annotation_path(pdf_path)
                        AnnotationIO.save_annotation(
                            self.main_window.annotation_document, str(ann_path)
                        )
                        logger.debug(f"Saved cleared annotation to {ann_path}")

                        ann_r2_key = get_annotation_r2_key(r2_key)
                        r2.upload_file(str(ann_path), ann_r2_key)
                        logger.debug(f"Synced cleared annotation to R2: {ann_r2_key}")

            mode_str = f"smart ({len(blocks_to_reprocess)} blocks)" if is_smart_mode else "full"
            logger.info(f"Cleaned old OCR results for node: {node_id} (mode={mode_str})")

            if hasattr(self, "_downloaded_jobs"):
                self._downloaded_jobs.clear()

        except Exception as e:
            logger.warning(f"Failed to clean old OCR results: {e}")

    def _create_job(self):
        """Создать новую задачу OCR"""
        if (
            not self.main_window.pdf_document
            or not self.main_window.annotation_document
        ):
            QMessageBox.warning(self, "Ошибка", "Откройте PDF документ")
            return

        if (
            hasattr(self.main_window, "_current_node_locked")
            and self.main_window._current_node_locked
        ):
            QMessageBox.warning(
                self,
                "Документ заблокирован",
                "Этот документ заблокирован от изменений.\nСначала снимите блокировку.",
            )
            return

        pdf_path = self.main_window.annotation_document.pdf_path
        if not pdf_path or not Path(pdf_path).exists():
            if (
                hasattr(self.main_window, "_current_pdf_path")
                and self.main_window._current_pdf_path
            ):
                pdf_path = self.main_window._current_pdf_path
                self.main_window.annotation_document.pdf_path = pdf_path

        if not pdf_path or not Path(pdf_path).exists():
            QMessageBox.warning(self, "Ошибка", "PDF файл не найден")
            return

        node_id = getattr(self.main_window, "_current_node_id", None) or None
        r2_key = getattr(self.main_window, "_current_r2_key", None) or None

        r2 = None
        if node_id and r2_key:
            try:
                from rd_core.r2_storage import R2Storage

                r2 = R2Storage()
                if not r2.exists(r2_key):
                    QMessageBox.warning(
                        self,
                        "Ошибка",
                        "PDF не загружен в облако.\n"
                        "Синхронизируйте документ или перезагрузите его в дерево проектов.",
                    )
                    return
            except Exception as e:
                logger.warning(f"Не удалось проверить R2: {e}")

        from PySide6.QtWidgets import QDialog

        from app.gui.ocr_dialog import OCRDialog

        task_name = Path(pdf_path).stem if pdf_path else ""

        dialog = OCRDialog(self.main_window, task_name=task_name, pdf_path=pdf_path)
        if dialog.exec() != QDialog.Accepted:
            return

        self._last_output_dir = dialog.output_dir
        self._last_engine = dialog.ocr_backend

        all_blocks = self._get_selected_blocks()
        if not all_blocks:
            QMessageBox.warning(self, "Ошибка", "Нет блоков для распознавания")
            return

        blocks_needing = self._get_blocks_needing_ocr()
        has_previous = len(all_blocks) > len(blocks_needing)

        if has_previous and blocks_needing:
            # Есть старые результаты и блоки для распознавания → SmartOCRModeDialog
            from app.gui.smart_ocr_mode_dialog import SmartOCRModeDialog

            mode_dialog = SmartOCRModeDialog(
                self,
                total_count=len(all_blocks),
                needs_ocr_count=len(blocks_needing),
                successful_count=len(all_blocks) - len(blocks_needing),
            )
            if mode_dialog.exec() != QDialog.Accepted:
                return

            if mode_dialog.selected_mode == SmartOCRModeDialog.MODE_SMART:
                selected_blocks = blocks_needing
                self._is_correction_mode = True
                if node_id and r2_key and r2:
                    block_ids = [b.id for b in selected_blocks]
                    self._clean_old_ocr_results(
                        node_id, r2_key, r2, blocks_to_reprocess=block_ids
                    )
            else:
                selected_blocks = all_blocks
                self._is_correction_mode = False
                if node_id and r2_key and r2:
                    self._clean_old_ocr_results(node_id, r2_key, r2)

        elif has_previous and not blocks_needing:
            # Всё уже распознано
            QMessageBox.information(
                self,
                "Все распознано",
                "Все блоки уже успешно распознаны.\n"
                "Добавьте новые блоки или пометьте для корректировки.",
            )
            return

        else:
            # Первый запуск или все нуждаются в OCR
            selected_blocks = all_blocks
            self._is_correction_mode = False
            if node_id and r2_key and r2:
                self._clean_old_ocr_results(node_id, r2_key, r2)

        client = self._get_client()
        if client is None:
            QMessageBox.warning(self, "Ошибка", "Клиент не инициализирован")
            return

        engine = "openrouter"
        if dialog.ocr_backend == "datalab":
            engine = "datalab"
        elif dialog.ocr_backend == "chandra":
            engine = "chandra"
        elif dialog.ocr_backend == "openrouter":
            engine = "openrouter"

        self._pending_output_dir = dialog.output_dir

        from app.gui.toast import show_toast

        show_toast(self, "Отправка задачи...", duration=1500)

        logger.info(
            f"Отправка задачи на сервер: engine={engine}, blocks={len(selected_blocks)}, "
            f"image_model={getattr(dialog, 'image_model', None)}, "
            f"stamp_model={getattr(dialog, 'stamp_model', None)}, node_id={node_id}"
        )

        import uuid

        temp_job_id = f"uploading-{uuid.uuid4().hex[:12]}"

        from app.ocr_client import JobInfo

        temp_job = JobInfo(
            id=temp_job_id,
            status="uploading",
            progress=0.0,
            document_id="",
            document_name=Path(pdf_path).name,
            task_name=task_name,
            status_message="Загрузка на сервер...",
        )
        self._signals.job_uploading.emit(temp_job)

        self._executor.submit(
            self._create_job_bg,
            client,
            pdf_path,
            selected_blocks,
            task_name,
            engine,
            getattr(dialog, "text_model", None),
            getattr(dialog, "table_model", None),
            getattr(dialog, "image_model", None),
            getattr(dialog, "stamp_model", None),
            node_id,
            temp_job_id,
            getattr(self, "_is_correction_mode", False),
        )

    def _create_job_bg(
        self,
        client,
        pdf_path,
        blocks,
        task_name,
        engine,
        text_model,
        table_model,
        image_model,
        stamp_model,
        node_id=None,
        temp_job_id=None,
        is_correction_mode=False,
    ):
        """Фоновое создание задачи"""
        try:
            from app.ocr_client import (
                AuthenticationError,
                PayloadTooLargeError,
                ServerError,
                get_or_create_client_id,
            )

            client_id = get_or_create_client_id()
            logger.info(
                f"Начало создания задачи: engine={engine}, blocks={len(blocks)}"
            )
            job_info = client.create_job(
                pdf_path,
                blocks,
                client_id=client_id,
                task_name=task_name,
                engine=engine,
                text_model=text_model,
                table_model=table_model,
                image_model=image_model,
                stamp_model=stamp_model,
                node_id=node_id,
                is_correction_mode=is_correction_mode,
            )
            logger.info(f"Задача создана: id={job_info.id}, status={job_info.status}")
            job_info._temp_job_id = temp_job_id
            self._signals.job_created.emit(job_info)
        except AuthenticationError:
            logger.error("Ошибка авторизации при создании задачи")
            self._signals.job_create_error.emit("auth", "Неверный API ключ.")
        except PayloadTooLargeError:
            logger.error("PDF файл слишком большой")
            self._signals.job_create_error.emit(
                "size", "PDF файл превышает лимит сервера."
            )
        except ServerError as e:
            logger.error(f"Ошибка сервера: {e}")
            self._signals.job_create_error.emit("server", f"Сервер недоступен.\n{e}")
        except Exception as e:
            logger.error(f"Ошибка создания задачи: {e}", exc_info=True)
            self._signals.job_create_error.emit("generic", str(e))

    def _pause_job(self, job_id: str):
        """Поставить задачу на паузу"""
        client = self._get_client()
        if client is None:
            return

        try:
            if client.pause_job(job_id):
                from app.gui.toast import show_toast

                show_toast(self, f"Задача {job_id[:8]}... на паузе")
                self._refresh_jobs(manual=True)
            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось поставить на паузу")
        except Exception as e:
            logger.error(f"Ошибка паузы задачи: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось поставить на паузу:\n{e}")

    def _resume_job(self, job_id: str):
        """Возобновить задачу с паузы"""
        client = self._get_client()
        if client is None:
            return

        try:
            if client.resume_job(job_id):
                from app.gui.toast import show_toast

                show_toast(self, f"Задача {job_id[:8]}... возобновлена")
                self._refresh_jobs(manual=True)
            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось возобновить")
        except Exception as e:
            logger.error(f"Ошибка возобновления задачи: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось возобновить:\n{e}")

    def _show_job_details(self, job_id: str):
        """Показать детальную информацию о задаче"""
        client = self._get_client()
        if client is None:
            return

        try:
            job_details = client.get_job_details(job_id)

            pdf_path = getattr(self.main_window, "_current_pdf_path", None)
            if pdf_path:
                job_details["client_output_dir"] = str(Path(pdf_path).parent)

            from app.gui.job_details_dialog import JobDetailsDialog

            dialog = JobDetailsDialog(job_details, self)
            dialog.exec()
        except Exception as e:
            logger.error(f"Ошибка получения информации о задаче: {e}")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось получить информацию:\n{e}"
            )

    def _cancel_job(self, job_id: str):
        """Отменить задачу"""
        client = self._get_client()
        if client is None:
            return

        try:
            if client.cancel_job(job_id):
                from app.gui.toast import show_toast

                show_toast(self, f"Задача {job_id[:8]}... отменена")
                self._refresh_jobs(manual=True)
            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось отменить задачу")
        except Exception as e:
            logger.error(f"Ошибка отмены задачи: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось отменить задачу:\n{e}")

    def _delete_job(self, job_id: str):
        """Удалить задачу (без удаления файлов из R2)"""
        client = self._get_client()
        if client is None:
            return

        try:
            if client.delete_job(job_id):
                from app.gui.toast import show_toast

                show_toast(self, f"Задача {job_id[:8]}... удалена")
                self._remove_job_from_table(job_id)
            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось удалить задачу")
        except Exception as e:
            logger.error(f"Ошибка удаления задачи: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить задачу:\n{e}")

    def _clear_all_jobs(self):
        """Очистить все задачи"""
        client = self._get_client()
        if client is None:
            QMessageBox.warning(self, "Ошибка", "Клиент не инициализирован")
            return

        reply = QMessageBox.question(
            self,
            "Очистка задач",
            "Удалить все задачи из списка?\n\n"
            "• Файлы документов из дерева проектов сохранятся\n"
            "• Legacy файлы (без привязки к дереву) будут удалены",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        try:
            jobs = client.list_jobs()
            deleted = 0
            errors = 0

            for job in jobs:
                try:
                    if client.delete_job(job.id):
                        deleted += 1
                    else:
                        errors += 1
                except Exception as e:
                    logger.warning(f"Ошибка удаления задачи {job.id}: {e}")
                    errors += 1

            self._refresh_jobs(manual=True)

            from app.gui.toast import show_toast

            if errors == 0:
                show_toast(self, f"Удалено {deleted} задач")
            else:
                show_toast(self, f"Удалено {deleted}, ошибок: {errors}")

        except Exception as e:
            logger.error(f"Ошибка очистки задач: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось очистить задачи:\n{e}")

    def _move_job_up(self):
        """Переместить выделенную задачу вверх в очереди."""
        self._reorder_selected_job("up")

    def _move_job_down(self):
        """Переместить выделенную задачу вниз в очереди."""
        self._reorder_selected_job("down")

    def _reorder_selected_job(self, direction: str):
        """Переместить задачу вверх/вниз в очереди обработки."""
        selected = self.jobs_table.selectedItems()
        if not selected:
            return

        row = selected[0].row()
        job_id_item = self.jobs_table.item(row, 0)
        if not job_id_item:
            return

        job_id = job_id_item.data(JOB_ID_ROLE)
        if not job_id:
            return

        cached_job = self._jobs_cache.get(job_id)
        if not cached_job or cached_job.status != "queued":
            return

        client = self._get_client()
        if client is None:
            return

        try:
            ok = client.reorder_job(job_id, direction)
            if ok:
                from app.gui.toast import show_toast

                label = "вверх" if direction == "up" else "вниз"
                show_toast(self, f"Задача перемещена {label}")
                self._refresh_jobs(manual=True)
            else:
                from app.gui.toast import show_toast

                show_toast(self, "Не удалось переместить задачу")
        except Exception as e:
            logger.error(f"Ошибка перемещения задачи: {e}")
            from app.gui.toast import show_toast

            show_toast(self, f"Ошибка: {e}")
