"""QAbstractTableModel для таблицы задач Remote OCR."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from app.gui.utils import format_datetime_utc3
from app.ocr_client.models import JobInfo

COLUMNS = ["№", "Наименование", "Время начала", "Статус", "Прогресс", "Детали", "Действия"]

JOB_ID_ROLE = Qt.UserRole + 1


def _get_status_text(status: str) -> str:
    """Человекочитаемый текст статуса задачи."""
    return {
        "uploading": "⬆️ Загрузка...",
        "draft": "📝 Черновик",
        "queued": "⏳ В очереди",
        "processing": "🔄 Обработка",
        "done": "✅ Готово",
        "partial": "⚠️ Частично",
        "error": "❌ Ошибка",
        "paused": "⏸️ Пауза",
        "cancelled": "🚫 Отменено",
    }.get(status, status)


class JobsTableModel(QAbstractTableModel):
    """Табличная модель задач Remote OCR.

    Хранит список ``JobInfo`` и предоставляет данные для QTableView
    через стандартный Model/View интерфейс Qt.
    """

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._jobs: list[JobInfo] = []

    # ------------------------------------------------------------------
    # QAbstractTableModel interface
    # ------------------------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._jobs)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> object | None:
        if not index.isValid() or index.row() >= len(self._jobs):
            return None

        job = self._jobs[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:  # №
                return str(index.row() + 1)
            elif col == 1:  # Наименование
                return job.task_name or job.document_name or ""
            elif col == 2:  # Время начала
                return format_datetime_utc3(job.created_at) if job.created_at else "Только что"
            elif col == 3:  # Статус
                return _get_status_text(job.status)
            elif col == 4:  # Прогресс
                return f"{int(job.progress * 100)}%"
            elif col == 5:  # Детали
                return job.status_message or ""
            elif col == 6:  # Действия (delegate рисует кнопку)
                return ""

        elif role == Qt.ToolTipRole:
            if col == 3 and job.error_message:
                return job.error_message

        elif role == Qt.UserRole:
            # Сырые значения для сортировки
            if col == 0:
                return index.row()
            elif col == 2:
                return job.created_at or ""
            elif col == 4:
                return job.progress
            elif col == 6:  # Статус для delegate
                return job.status

        elif role == JOB_ID_ROLE:
            return job.id

        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.DisplayRole,
    ) -> str | None:
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            if 0 <= section < len(COLUMNS):
                return COLUMNS[section]
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_jobs(self, jobs: list[JobInfo]) -> None:
        """Массовое обновление списка задач.

        Выполняет полный reset модели
        (``beginResetModel`` / ``endResetModel``).
        """
        self.beginResetModel()
        self._jobs = list(jobs)
        self.endResetModel()

    def get_job_id(self, row: int) -> str | None:
        """Получить ``job_id`` по номеру строки."""
        if 0 <= row < len(self._jobs):
            return self._jobs[row].id
        return None

    def get_job(self, row: int) -> JobInfo | None:
        """Получить ``JobInfo`` по номеру строки."""
        if 0 <= row < len(self._jobs):
            return self._jobs[row]
        return None

    def find_row_by_job_id(self, job_id: str) -> int:
        """Найти строку по ``job_id``.

        Returns:
            Индекс строки или ``-1`` если задача не найдена.
        """
        for i, job in enumerate(self._jobs):
            if job.id == job_id:
                return i
        return -1
