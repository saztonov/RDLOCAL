"""Mixin для управления жизненным циклом Remote OCR задач"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox

from app.gui.remote_ocr.table_manager import JOB_ID_ROLE

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class JobLifecycleMixin:
    """Миксин для управления задачами: возобновление, отмена, удаление, перемещение"""

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

    def _cancel_all_jobs(self):
        """Отменить все активные задачи (queued/processing/paused)"""
        client = self._get_client()
        if client is None:
            return

        try:
            jobs, _ = client.list_jobs()
            active_jobs = [j for j in jobs if j.status in ("queued", "processing", "paused")]

            if not active_jobs:
                from app.gui.toast import show_toast
                show_toast(self, "Нет активных задач для отмены")
                return

            reply = QMessageBox.question(
                self,
                "Отмена задач",
                f"Отменить все активные задачи ({len(active_jobs)} шт.)?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

            cancelled = 0
            errors = 0
            for job in active_jobs:
                try:
                    if client.cancel_job(job.id):
                        cancelled += 1
                    else:
                        errors += 1
                except Exception as e:
                    logger.warning(f"Ошибка отмены задачи {job.id}: {e}")
                    errors += 1

            self._refresh_jobs(manual=True)

            from app.gui.toast import show_toast
            if errors == 0:
                show_toast(self, f"Отменено {cancelled} задач")
            else:
                show_toast(self, f"Отменено {cancelled}, ошибок: {errors}")

        except Exception as e:
            logger.error(f"Ошибка отмены всех задач: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось отменить задачи:\n{e}")

    def _delete_job(self, job_id: str):
        """Удалить задачу (без удаления файлов из R2)"""
        client = self._get_client()
        if client is None:
            return

        try:
            if client.delete_job(job_id):
                from app.gui.toast import show_toast

                show_toast(self, f"Задача {job_id[:8]}... удалена")
                self._jobs_cache.pop(job_id, None)
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
            jobs, _ = client.list_jobs()
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
