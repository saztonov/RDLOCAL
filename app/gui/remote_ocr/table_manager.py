"""Управление таблицей задач Remote OCR"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QPushButton,
    QTableWidgetItem,
    QWidget,
)

from app.gui.utils import format_datetime_utc3

logger = logging.getLogger(__name__)

JOB_ID_ROLE = Qt.UserRole + 1


class SortableTableWidgetItem(QTableWidgetItem):
    """QTableWidgetItem с сортировкой по данным из UserRole (числа, даты)."""

    def __lt__(self, other):
        my = self.data(Qt.UserRole)
        ot = other.data(Qt.UserRole)
        if my is not None and ot is not None:
            if isinstance(my, (int, float)) and isinstance(ot, (int, float)):
                return my < ot
            return str(my) < str(ot)
        return super().__lt__(other)


class TableManagerMixin:
    """Миксин для управления таблицей задач"""

    # ── Утилитные методы ─────────────────────────────────────────────

    def _find_row_by_job_id(self, job_id: str) -> int:
        """Найти строку в таблице по job_id. Возвращает -1 если не найден."""
        for row in range(self.jobs_table.rowCount()):
            item = self.jobs_table.item(row, 0)
            if item and item.data(JOB_ID_ROLE) == job_id:
                return row
        return -1

    def _update_row_cells(self, row: int, job, row_number: int):
        """Обновить все ячейки строки данными задачи (in-place)."""
        table = self.jobs_table

        # Колонка 0: №
        item0 = table.item(row, 0)
        if item0 is None:
            item0 = SortableTableWidgetItem(str(row_number))
            item0.setData(Qt.UserRole, row_number)
            item0.setData(JOB_ID_ROLE, job.id)
            table.setItem(row, 0, item0)
        else:
            item0.setText(str(row_number))
            item0.setData(Qt.UserRole, row_number)
            item0.setData(JOB_ID_ROLE, job.id)

        # Колонка 1: Наименование
        display_name = job.task_name if job.task_name else job.document_name
        item1 = table.item(row, 1)
        if item1 is None:
            table.setItem(row, 1, QTableWidgetItem(display_name))
        else:
            item1.setText(display_name)

        # Колонка 2: Время начала
        created_at_str = (
            format_datetime_utc3(job.created_at) if job.created_at else "Только что"
        )
        item2 = table.item(row, 2)
        if item2 is None:
            created_item = SortableTableWidgetItem(created_at_str)
            created_item.setData(Qt.UserRole, job.created_at or "")
            table.setItem(row, 2, created_item)
        else:
            item2.setText(created_at_str)
            item2.setData(Qt.UserRole, job.created_at or "")

        # Колонка 3: Статус
        status_text = self._get_status_text(job.status)
        item3 = table.item(row, 3)
        if item3 is None:
            status_item = QTableWidgetItem(status_text)
            if job.error_message:
                status_item.setToolTip(job.error_message)
            table.setItem(row, 3, status_item)
        else:
            item3.setText(status_text)
            item3.setToolTip(job.error_message or "")

        # Колонка 4: Прогресс
        progress_text = f"{int(job.progress * 100)}%"
        item4 = table.item(row, 4)
        if item4 is None:
            progress_item = SortableTableWidgetItem(progress_text)
            progress_item.setData(Qt.UserRole, job.progress)
            table.setItem(row, 4, progress_item)
        else:
            item4.setText(progress_text)
            item4.setData(Qt.UserRole, job.progress)

        # Колонка 5: Детали
        status_msg = job.status_message or ""
        item5 = table.item(row, 5)
        if item5 is None:
            table.setItem(row, 5, QTableWidgetItem(status_msg))
        else:
            item5.setText(status_msg)

        # Колонка 6: Действия (виджет — всегда пересоздаём, т.к. зависит от статуса)
        actions_widget = self._create_actions_widget(job)
        table.setCellWidget(row, 6, actions_widget)

    def _renumber_rows(self):
        """Пересчитать номера строк (колонка 0) по текущему визуальному порядку."""
        for row in range(self.jobs_table.rowCount()):
            item = self.jobs_table.item(row, 0)
            if item:
                num = row + 1
                item.setText(str(num))
                item.setData(Qt.UserRole, num)

    # ── Основной метод обновления таблицы ────────────────────────────

    def _update_table(self, jobs):
        """Обновить таблицу задач (инкрементально, без пересборки)."""
        table = self.jobs_table

        # 1. Сохранить выбранную задачу
        selected_job_id = None
        selected_items = table.selectedItems()
        if selected_items:
            item = table.item(selected_items[0].row(), 0)
            if item:
                selected_job_id = item.data(JOB_ID_ROLE)

        # 2. Сохранить top visible job_id для восстановления скролла
        top_visible_job_id = None
        top_row = table.rowAt(0)
        if top_row >= 0:
            top_item = table.item(top_row, 0)
            if top_item:
                top_visible_job_id = top_item.data(JOB_ID_ROLE)

        # 3. blockSignals + отключить сортировку
        table.blockSignals(True)
        table.setSortingEnabled(False)

        try:
            # 4. Авто-скачивание результата для текущего документа
            current_node_id = getattr(self.main_window, "_current_node_id", None)
            if current_node_id:
                for job in jobs:
                    if (
                        job.status == "done"
                        and getattr(job, "node_id", None) == current_node_id
                    ):
                        if job.id not in self._downloaded_jobs:
                            if not self.isVisible():
                                from app.gui.toast import show_toast

                                doc_name = job.task_name or job.document_name or ""
                                show_toast(
                                    self.main_window,
                                    f"OCR завершён: {doc_name}",
                                    duration=5000,
                                )
                                logger.info(
                                    f"Задача {job.id[:8]}... завершена "
                                    f"(панель скрыта), показано уведомление"
                                )
                            self._auto_download_result(job.id)
                        break

            # 5. Построить множество incoming job IDs
            incoming_ids = {job.id for job in jobs}

            # 6. Построить карту текущих строк: job_id -> row
            existing_rows = {}
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item:
                    jid = item.data(JOB_ID_ROLE)
                    if jid:
                        existing_rows[jid] = row

            # 7. Удалить строки для задач, которых больше нет (с конца)
            rows_to_remove = [
                row for jid, row in existing_rows.items() if jid not in incoming_ids
            ]
            for row in sorted(rows_to_remove, reverse=True):
                table.removeRow(row)

            # 8. Перестроить карту после удалений
            existing_rows = {}
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item:
                    jid = item.data(JOB_ID_ROLE)
                    if jid:
                        existing_rows[jid] = row

            # 9. Обновить существующие + добавить новые
            for idx, job in enumerate(jobs):
                if job.id in existing_rows:
                    self._update_row_cells(existing_rows[job.id], job, idx + 1)
                else:
                    new_row = table.rowCount()
                    table.insertRow(new_row)
                    self._update_row_cells(new_row, job, idx + 1)

        finally:
            # 10. Включить сортировку
            table.setSortingEnabled(True)

            # 11. Пересчитать номера по визуальному порядку
            self._renumber_rows()

            # 12. Восстановить blockSignals
            table.blockSignals(False)

        # 13. Восстановить выделение
        if selected_job_id:
            sel_row = self._find_row_by_job_id(selected_job_id)
            if sel_row >= 0:
                table.selectRow(sel_row)

        # 14. Восстановить скролл по job identity
        if top_visible_job_id:
            vis_row = self._find_row_by_job_id(top_visible_job_id)
            if vis_row >= 0:
                vis_item = table.item(vis_row, 0)
                if vis_item:
                    table.scrollToItem(
                        vis_item, QAbstractItemView.ScrollHint.PositionAtTop
                    )

    # ── Одиночные операции со строками ───────────────────────────────

    def _add_job_to_table(self, job, at_top: bool = False):
        """Добавить одну задачу в таблицу (для оптимистичного обновления)"""
        logger.info(
            f"_add_job_to_table: job_id={job.id}, at_top={at_top}, "
            f"current_rows={self.jobs_table.rowCount()}"
        )

        self.jobs_table.setSortingEnabled(False)

        row = 0 if at_top else self.jobs_table.rowCount()
        self.jobs_table.insertRow(row)

        num_val = 1 if at_top else self.jobs_table.rowCount()
        self._update_row_cells(row, job, num_val)

        self.jobs_table.setSortingEnabled(True)
        self._renumber_rows()

        item = self.jobs_table.item(row, 0)
        if item:
            self.jobs_table.scrollToItem(
                item, QAbstractItemView.ScrollHint.PositionAtTop
            )

        display_name = job.task_name if job.task_name else job.document_name
        logger.info(
            f"Задача добавлена в таблицу: row={row}, name={display_name}, "
            f"status={job.status}, total_rows={self.jobs_table.rowCount()}"
        )

    def _replace_job_in_table(self, old_job_id: str, new_job):
        """Заменить временную задачу на реальную в таблице"""
        row = self._find_row_by_job_id(old_job_id)
        if row >= 0:
            logger.info(
                f"Найдена временная задача в строке {row}, заменяем на {new_job.id}"
            )
            self.jobs_table.setSortingEnabled(False)
            self._update_row_cells(row, new_job, row + 1)
            self.jobs_table.setSortingEnabled(True)
            self._renumber_rows()
            logger.info(f"Задача заменена: {old_job_id} -> {new_job.id}")
        else:
            logger.warning(
                f"Временная задача {old_job_id} не найдена в таблице, "
                f"добавляем как новую"
            )
            self._add_job_to_table(new_job, at_top=True)

    def _remove_job_from_table(self, job_id: str):
        """Удалить задачу из таблицы по ID"""
        row = self._find_row_by_job_id(job_id)
        if row >= 0:
            self.jobs_table.setSortingEnabled(False)
            self.jobs_table.removeRow(row)
            self.jobs_table.setSortingEnabled(True)
            self._renumber_rows()
            logger.info(f"Задача {job_id} удалена из таблицы")

    # ── Вспомогательные методы ───────────────────────────────────────

    def _get_status_text(self, status: str) -> str:
        """Получить текст статуса с эмодзи"""
        return {
            "uploading": "⬆️ Загрузка...",
            "draft": "📝 Черновик",
            "queued": "⏳ В очереди",
            "processing": "🔄 Обработка",
            "done": "✅ Готово",
            "error": "❌ Ошибка",
            "paused": "⏸️ Пауза",
            "cancelled": "🚫 Отменено",
        }.get(status, status)

    def _create_actions_widget(self, job) -> QWidget:
        """Создать виджет с кнопками действий для задачи"""
        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(1, 1, 1, 1)
        actions_layout.setSpacing(2)

        # Кнопка остановки для активных задач
        if job.status in ("queued", "processing"):
            stop_btn = QPushButton("⏹")
            stop_btn.setToolTip("Отменить задачу")
            stop_btn.setFixedSize(26, 26)
            stop_btn.setStyleSheet(
                "QPushButton { background-color: #c0392b; border: 1px solid #922b21; "
                "border-radius: 4px; color: white; font-weight: bold; } "
                "QPushButton:hover { background-color: #922b21; }"
            )
            stop_btn.clicked.connect(lambda checked, jid=job.id: self._cancel_job(jid))
            actions_layout.addWidget(stop_btn)
        elif job.status == "paused":
            resume_btn = QPushButton("▶")
            resume_btn.setToolTip("Возобновить")
            resume_btn.setFixedSize(26, 26)
            resume_btn.setStyleSheet(
                "QPushButton { background-color: #27ae60; border: 1px solid #1e8449; "
                "border-radius: 4px; color: white; } "
                "QPushButton:hover { background-color: #1e8449; }"
            )
            resume_btn.clicked.connect(
                lambda checked, jid=job.id: self._resume_job(jid)
            )
            actions_layout.addWidget(resume_btn)

        # Кнопка информации
        info_btn = QPushButton("ℹ")
        info_btn.setToolTip("Информация о задаче")
        info_btn.setFixedSize(26, 26)
        info_btn.setStyleSheet(
            "QPushButton { background-color: #3498db; border: 1px solid #2980b9; "
            "border-radius: 4px; color: white; font-weight: bold; } "
            "QPushButton:hover { background-color: #2980b9; }"
        )
        info_btn.clicked.connect(
            lambda checked, jid=job.id: self._show_job_details(jid)
        )
        actions_layout.addWidget(info_btn)

        # Кнопка удаления (без удаления файлов)
        delete_btn = QPushButton("🗑")
        delete_btn.setToolTip("Удалить задачу (файлы сохранятся)")
        delete_btn.setFixedSize(26, 26)
        delete_btn.setStyleSheet(
            "QPushButton { background-color: #7f8c8d; border: 1px solid #5d6d7e; "
            "border-radius: 4px; color: white; } "
            "QPushButton:hover { background-color: #5d6d7e; }"
        )
        delete_btn.clicked.connect(
            lambda checked, jid=job.id: self._delete_job(jid)
        )
        actions_layout.addWidget(delete_btn)

        actions_layout.addStretch()
        return actions_widget
