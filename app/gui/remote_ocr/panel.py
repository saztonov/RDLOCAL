"""Панель для управления Remote OCR задачами.

Тонкая сборка виджетов — вся логика в JobsController.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, QSortFilterProxyModel
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.gui.remote_ocr.cancel_delegate import CancelButtonDelegate
from app.gui.remote_ocr.jobs_controller import JobsController
from app.gui.remote_ocr.jobs_model import JOB_ID_ROLE, JobsTableModel

if TYPE_CHECKING:
    from app.gui.main_window import MainWindow

logger = logging.getLogger(__name__)


class RemoteOCRPanel(QDockWidget):
    """Dock-панель для Remote OCR задач"""

    def __init__(self, main_window: "MainWindow", parent=None):
        super().__init__("Remote OCR Jobs", parent)
        self.setObjectName("RemoteOCRPanel")
        self.main_window = main_window

        # Controller + Model
        self.controller = JobsController(main_window, parent=self)
        self._model = JobsTableModel(self)
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setSortRole(Qt.UserRole)

        self._download_dialog: Optional[QProgressDialog] = None

        self._setup_ui()
        self._connect_signals()

    def _connect_signals(self):
        """Подключить сигналы контроллера к UI"""
        ctrl = self.controller

        # Обновление таблицы
        ctrl.jobs_updated.connect(self._on_jobs_updated)
        ctrl.connection_status.connect(self._on_connection_status)

        # Создание задач
        ctrl.job_uploading.connect(lambda _: None)  # модель обновится через jobs_updated
        ctrl.job_create_error.connect(self._on_job_create_error)

        # Скачивание
        ctrl.download_started.connect(self._on_download_started)
        ctrl.download_progress.connect(self._on_download_progress)
        ctrl.download_finished.connect(self._on_download_finished)
        ctrl.download_error.connect(self._on_download_error)

    def _setup_ui(self):
        """Настроить UI панели"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)

        # Header
        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("Задачи:"))

        self.status_label = QLabel("🔴 Не подключено")
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)

        self.move_up_btn = QPushButton("▲")
        self.move_up_btn.setMaximumWidth(30)
        self.move_up_btn.setToolTip("Переместить задачу выше в очереди")
        self.move_up_btn.setEnabled(False)
        self.move_up_btn.clicked.connect(lambda: self._reorder_selected("up"))
        header_layout.addWidget(self.move_up_btn)

        self.move_down_btn = QPushButton("▼")
        self.move_down_btn.setMaximumWidth(30)
        self.move_down_btn.setToolTip("Переместить задачу ниже в очереди")
        self.move_down_btn.setEnabled(False)
        self.move_down_btn.clicked.connect(lambda: self._reorder_selected("down"))
        header_layout.addWidget(self.move_down_btn)

        self.cancel_all_btn = QPushButton("⏹")
        self.cancel_all_btn.setMaximumWidth(30)
        self.cancel_all_btn.setToolTip("Отменить все активные задачи")
        self.cancel_all_btn.clicked.connect(self.controller.cancel_all_jobs)
        header_layout.addWidget(self.cancel_all_btn)

        self.clear_all_btn = QPushButton("🗑️")
        self.clear_all_btn.setMaximumWidth(30)
        self.clear_all_btn.setToolTip("Очистить все задачи")
        self.clear_all_btn.clicked.connect(self.controller.clear_all_jobs)
        header_layout.addWidget(self.clear_all_btn)

        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setMaximumWidth(30)
        self.refresh_btn.setToolTip("Обновить список")
        self.refresh_btn.clicked.connect(lambda: self.controller.refresh(manual=True))
        header_layout.addWidget(self.refresh_btn)

        layout.addLayout(header_layout)

        # Stats widget
        from app.gui.remote_ocr.ocr_stats_widget import OCRStatsWidget

        self.stats_widget = OCRStatsWidget()
        layout.addWidget(self.stats_widget)

        # Table (QTableView + proxy model)
        self.jobs_table = QTableView()
        self.jobs_table.setModel(self._proxy)
        self.jobs_table.setSortingEnabled(True)
        self.jobs_table.sortByColumn(2, Qt.DescendingOrder)
        self.jobs_table.setSelectionBehavior(QTableView.SelectRows)
        self.jobs_table.setSelectionMode(QTableView.SingleSelection)
        self.jobs_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.jobs_table.customContextMenuRequested.connect(self._show_context_menu)
        self.jobs_table.selectionModel().selectionChanged.connect(self._update_reorder_buttons)
        self.jobs_table.setMouseTracking(True)

        # Delegate для кнопки отмены в колонке "Действия"
        self._cancel_delegate = CancelButtonDelegate(self.jobs_table)
        self._cancel_delegate.cancel_requested.connect(self.controller.cancel_job)
        self.jobs_table.setItemDelegateForColumn(6, self._cancel_delegate)

        header = self.jobs_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.resizeSection(0, 35)   # №
        header.resizeSection(1, 150)  # Наименование
        header.resizeSection(2, 120)  # Время начала
        header.resizeSection(3, 100)  # Статус
        header.resizeSection(4, 70)   # Прогресс
        header.setSectionResizeMode(5, QHeaderView.Stretch)  # Детали
        header.resizeSection(6, 50)   # Действия

        layout.addWidget(self.jobs_table)

        self.setWidget(widget)
        self.setMinimumWidth(520)
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)

    # ── Slots ──────────────────────────────────────────────────────────

    def _on_jobs_updated(self, jobs):
        """Контроллер прислал обновлённый список задач"""
        # Сохраняем selection
        selected_job_id = self._get_selected_job_id()

        self._model.update_jobs(jobs)

        # Восстанавливаем selection
        if selected_job_id:
            row = self._model.find_row_by_job_id(selected_job_id)
            if row >= 0:
                proxy_idx = self._proxy.mapFromSource(self._model.index(row, 0))
                self.jobs_table.selectRow(proxy_idx.row())

    def _on_connection_status(self, status: str):
        labels = {
            "connected": "🟢 Подключено",
            "disconnected": "🔴 Сервер недоступен",
            "loading": "🔄 Загрузка...",
        }
        self.status_label.setText(labels.get(status, status))

    def _on_job_create_error(self, error_type: str, message: str):
        titles = {
            "auth": "Ошибка авторизации",
            "size": "Файл слишком большой",
            "server": "Ошибка сервера",
            "generic": "Ошибка",
        }
        QMessageBox.critical(self, titles.get(error_type, "Ошибка"), message)

    def _on_download_started(self, job_id: str, total_files: int):
        if self._download_dialog:
            self._download_dialog.close()
            self._download_dialog = None
        self._download_dialog = QProgressDialog(
            f"Скачивание файлов задачи {job_id[:8]}...", None, 0, total_files, self
        )
        self._download_dialog.setWindowTitle("Загрузка результатов")
        self._download_dialog.setWindowModality(Qt.WindowModal)
        self._download_dialog.setMinimumDuration(0)
        self._download_dialog.setValue(0)
        self._download_dialog.show()

    def _on_download_progress(self, job_id: str, current: int, filename: str):
        if self._download_dialog:
            self._download_dialog.setValue(current)
            self._download_dialog.setLabelText(f"Скачивание: {filename}")

    def _on_download_finished(self, job_id: str, extract_dir: str):
        if self._download_dialog:
            self._download_dialog.close()
            self._download_dialog = None
        self.update_ocr_stats()

        from app.gui.toast import show_toast

        show_toast(self.main_window, "OCR завершён, аннотация обновлена")

    def _on_download_error(self, job_id: str, error_msg: str):
        if self._download_dialog:
            self._download_dialog.close()
            self._download_dialog = None
        QMessageBox.critical(self, "Ошибка загрузки", f"Не удалось скачать файлы:\n{error_msg}")

    # ── Context menu ───────────────────────────────────────────────────

    def _show_context_menu(self, pos):
        proxy_idx = self.jobs_table.indexAt(pos)
        if not proxy_idx.isValid():
            return

        source_idx = self._proxy.mapToSource(proxy_idx)
        job = self._model.get_job(source_idx.row())
        if not job:
            return

        node_id = getattr(job, "node_id", None)

        menu = QMenu(self)
        find_action = menu.addAction("🔍 Найти в дереве проектов")
        find_action.setEnabled(bool(node_id))

        action = menu.exec_(self.jobs_table.mapToGlobal(pos))
        if action == find_action and node_id:
            self._navigate_to_project_tree(node_id)

    def _navigate_to_project_tree(self, node_id: str):
        if not hasattr(self.main_window, "project_tree_widget"):
            return

        tree_widget = self.main_window.project_tree_widget
        if hasattr(self.main_window, "project_dock"):
            dock = self.main_window.project_dock
            if not dock.isVisible():
                dock.show()

        if not tree_widget.navigate_to_node(node_id):
            QMessageBox.warning(
                self, "Узел не найден",
                "Не удалось найти узел в дереве проектов.\nВозможно, узел был удалён.",
            )

    # ── Reorder ────────────────────────────────────────────────────────

    def _reorder_selected(self, direction: str):
        job_id = self._get_selected_job_id()
        if job_id:
            self.controller.reorder_job(job_id, direction)

    def _update_reorder_buttons(self):
        job_id = self._get_selected_job_id()
        if not job_id:
            self.move_up_btn.setEnabled(False)
            self.move_down_btn.setEnabled(False)
            return

        job = self.controller._jobs_cache.get(job_id)
        can_reorder = job and job.status == "queued"
        self.move_up_btn.setEnabled(can_reorder)
        self.move_down_btn.setEnabled(can_reorder)

    # ── Helpers ────────────────────────────────────────────────────────

    def _get_selected_job_id(self) -> Optional[str]:
        indexes = self.jobs_table.selectionModel().selectedRows()
        if not indexes:
            return None
        proxy_idx = indexes[0]
        source_idx = self._proxy.mapToSource(proxy_idx)
        return self._model.get_job_id(source_idx.row())

    def update_ocr_stats(self):
        """Пересчитать и обновить статистику OCR для текущего документа."""
        if not self.main_window.annotation_document:
            self.stats_widget.clear_stats()
            return

        from app.gui.remote_ocr.ocr_stats_widget import compute_ocr_stats

        all_blocks = []
        for page in self.main_window.annotation_document.pages:
            if page.blocks:
                all_blocks.extend(page.blocks)

        if not all_blocks:
            self.stats_widget.clear_stats()
            return

        stats = compute_ocr_stats(all_blocks)
        self.stats_widget.update_stats(stats)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self.controller.set_panel_visible(True)
        self.update_ocr_stats()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.controller.set_panel_visible(False)

    def closeEvent(self, event):
        self.controller.shutdown()
        super().closeEvent(event)
