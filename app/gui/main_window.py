"""
Главное окно приложения
Интеграция компонентов через миксины
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

from PySide6.QtWidgets import QLabel, QMainWindow, QProgressBar, QStatusBar

from app.gui.block_handlers import BlockHandlersMixin
from app.gui.blocks_tree import BlocksTreeManager
from app.gui.file_operations import FileOperationsMixin
from app.gui.menu_setup import MenuSetupMixin
from app.gui.navigation_manager import NavigationManager
from app.gui.panels_setup import PanelsSetupMixin
from app.gui.remote_ocr.panel import RemoteOCRPanel
from app.gui.undo_redo_mixin import UndoRedoMixin
from rd_core.models import Document
from rd_core.pdf_utils import PDFDocument

# Импорт метаданных продукта
try:
    from _metadata import __product__, get_version_info
except ImportError:
    __product__ = "Core Structure"

    def get_version_info():
        return "Core Structure v0.1"


class MainWindow(
    MenuSetupMixin,
    PanelsSetupMixin,
    FileOperationsMixin,
    BlockHandlersMixin,
    UndoRedoMixin,
    QMainWindow,
):
    """Главное окно приложения для аннотирования PDF"""

    def __init__(self):
        super().__init__()

        # Данные приложения
        self.pdf_document: Optional[PDFDocument] = None
        self.annotation_document: Optional[Document] = None
        self.current_page: int = 0
        self.page_images: dict = {}
        self._page_images_order: list = []  # LRU порядок страниц
        self._page_images_max: int = 5  # Максимум страниц в кеше
        self.page_zoom_states: dict = {}
        self._current_pdf_path: Optional[str] = None
        self._current_node_id: Optional[str] = None
        self._current_node_locked: bool = False

        # Undo/Redo стек
        self.undo_stack: list = []  # [(page_num, blocks_copy), ...]
        self.redo_stack: list = []

        # Буфер обмена для блоков
        self._blocks_clipboard: list = []

        # Менеджеры (инициализируются после setup_ui)
        self.blocks_tree_manager = None
        self.navigation_manager = None
        self.remote_ocr_panel = None

        # Настройка UI
        self._setup_menu()
        self._setup_toolbar()
        self._setup_ui()

        # Remote OCR панель
        self._setup_remote_ocr_panel()

        # Добавляем действия панелей в меню
        self._setup_panels_menu()

        # Инициализация менеджеров после создания UI
        self.blocks_tree_manager = BlocksTreeManager(self, self.blocks_tree)
        self.navigation_manager = NavigationManager(self)

        # Подключаем сигналы кеша аннотаций
        self._setup_annotation_cache_signals()

        self.setWindowTitle(__product__)
        self.resize(1200, 800)

        # Статус-бар для отображения прогресса загрузки
        self._setup_status_bar()

        # Восстановить настройки окна
        self._restore_settings()

        # Гарантировать видимость Remote OCR панели после восстановления настроек
        if self.remote_ocr_panel:
            self.remote_ocr_panel.show()

        # Загрузить настроенные горячие клавиши
        self._update_hotkeys_from_settings()

    def _render_current_page(self, update_tree: bool = True):
        """Отрендерить текущую страницу"""
        if not self.pdf_document:
            return

        self.navigation_manager.load_page_image(self.current_page)

        if self.current_page in self.page_images:
            self.navigation_manager.restore_zoom()

            current_page_data = self._get_or_create_page(self.current_page)
            self.page_viewer.set_blocks(
                current_page_data.blocks if current_page_data else []
            )

            if update_tree:
                self.blocks_tree_manager.update_blocks_tree()

    def _update_ui(self):
        """Обновить UI элементы"""
        if self.pdf_document:
            self.page_label.setText(f"/ {self.pdf_document.page_count}")
            self.page_input.setEnabled(True)
            self.page_input.setMaximum(self.pdf_document.page_count)
            self.page_input.blockSignals(True)
            self.page_input.setValue(self.current_page + 1)
            self.page_input.blockSignals(False)
        else:
            self.page_label.setText("/ 0")
            self.page_input.setEnabled(False)
            self.page_input.setMaximum(1)

    def _prev_page(self):
        """Предыдущая страница"""
        self.navigation_manager.prev_page()

    def _next_page(self):
        """Следующая страница"""
        self.navigation_manager.next_page()

    def _goto_page_from_input(self, page_num: int):
        """Перейти на страницу из поля ввода (нумерация с 1)"""
        if self.pdf_document:
            self.navigation_manager.go_to_page(page_num - 1)

    def _zoom_in(self):
        """Увеличить масштаб"""
        self.navigation_manager.zoom_in()

    def _zoom_out(self):
        """Уменьшить масштаб"""
        self.navigation_manager.zoom_out()

    def _zoom_reset(self):
        """Сбросить масштаб"""
        self.navigation_manager.zoom_reset()

    def _fit_to_view(self):
        """Подогнать к окну"""
        self.navigation_manager.fit_to_view()

    def _clear_interface(self):
        """Очистить интерфейс при отсутствии файлов"""
        if self.pdf_document:
            self.pdf_document.close()
        self.pdf_document = None
        self.annotation_document = None
        self._current_pdf_path = None

        # Вернуть логи в папку проектов или дефолтную
        from app.logging_manager import get_logging_manager

        get_logging_manager().switch_to_projects_folder()

        self.page_images.clear()
        self._page_images_order.clear()
        self.page_viewer.set_page_image(None, 0)
        self.page_viewer.set_blocks([])
        if self.blocks_tree_manager:
            self.blocks_tree_manager.update_blocks_tree()
        # Сбросить подсветку документа в дереве
        if hasattr(self, "project_tree_widget"):
            self.project_tree_widget.highlight_document("")
        # Очистить OCR preview
        if hasattr(self, "ocr_preview") and self.ocr_preview:
            self.ocr_preview.clear()
        if hasattr(self, "ocr_preview_inline") and self.ocr_preview_inline:
            self.ocr_preview_inline.clear()
        self._update_ui()

    def _save_settings(self):
        """Сохранить настройки окна"""
        from PySide6.QtCore import QSettings

        settings = QSettings("PDFAnnotationTool", "MainWindow")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())

    def _restore_settings(self):
        """Восстановить настройки окна"""
        from PySide6.QtCore import QSettings

        settings = QSettings("PDFAnnotationTool", "MainWindow")

        geometry = settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

        window_state = settings.value("windowState")
        if window_state:
            self.restoreState(window_state)

    def closeEvent(self, event):
        """Обработка закрытия окна"""
        # Принудительно синхронизировать все несохраненные изменения
        from app.gui.annotation_cache import get_annotation_cache

        cache = get_annotation_cache()
        cache.force_sync_all(synchronous=True)

        self._flush_pending_save()
        self._save_settings()
        event.accept()

    def _setup_panels_menu(self):
        """Добавить действия панелей в меню Вид → Панели"""
        menubar = self.menuBar()
        for action in menubar.actions():
            if action.text() == "&Вид":
                view_menu = action.menu()
                for sub_action in view_menu.actions():
                    if sub_action.menu() and "Панели" in sub_action.text():
                        panels_menu = sub_action.menu()
                        panels_menu.addAction(self.project_dock.toggleViewAction())
                        panels_menu.addAction(self.blocks_dock.toggleViewAction())
                        panels_menu.addAction(
                            self.remote_ocr_panel.toggleViewAction()
                        )
                        break
                break

    # === Remote OCR ===
    def _setup_remote_ocr_panel(self):
        """Инициализировать панель Remote OCR"""
        from PySide6.QtCore import Qt

        self.remote_ocr_panel = RemoteOCRPanel(self, self)
        self.addDockWidget(Qt.RightDockWidgetArea, self.remote_ocr_panel)
        self.resizeDocks([self.remote_ocr_panel], [520], Qt.Horizontal)
        self.remote_ocr_panel.show()

    def _toggle_remote_ocr_panel(self):
        """Показать/скрыть панель Remote OCR"""
        if self.remote_ocr_panel:
            if self.remote_ocr_panel.isVisible():
                self.remote_ocr_panel.hide()
            else:
                self.remote_ocr_panel.show()

    def _show_folder_settings(self):
        """Показать диалог настройки папок"""
        from app.gui.folder_settings_dialog import FolderSettingsDialog

        dialog = FolderSettingsDialog(self)
        dialog.exec()

    def _show_version_settings(self):
        """Показать диалог настройки версионности"""
        from app.gui.folder_settings_dialog import VersionSettingsDialog

        dialog = VersionSettingsDialog(self)
        dialog.exec()

    def _show_hotkeys_dialog(self):
        """Показать диалог настройки горячих клавиш"""
        from app.gui.hotkeys_dialog import HotkeysDialog

        dialog = HotkeysDialog(self)
        dialog.exec()

    def _update_hotkeys_from_settings(self):
        """Обновить горячие клавиши из настроек"""
        from app.gui.hotkeys_dialog import HotkeysDialog

        if hasattr(self, "text_action"):
            self.text_action.setShortcut(HotkeysDialog.get_hotkey("text_block"))
        if hasattr(self, "image_action"):
            self.image_action.setShortcut(HotkeysDialog.get_hotkey("image_block"))
        if hasattr(self, "stamp_action"):
            self.stamp_action.setShortcut(HotkeysDialog.get_hotkey("stamp_block"))

    def _send_to_remote_ocr(self):
        """Отправить выделенные блоки на Remote OCR"""
        if self.remote_ocr_panel:
            self.remote_ocr_panel.show()
            self.remote_ocr_panel.controller.create_job()

    # === Status Bar ===
    def _setup_status_bar(self):
        """Создать статус-бар с прогрессом"""
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        self._status_label = QLabel("")
        self._status_progress = QProgressBar()
        self._status_progress.setMaximumWidth(200)
        self._status_progress.setMaximumHeight(16)
        self._status_progress.setTextVisible(True)
        self._status_progress.hide()

        self._status_bar.addPermanentWidget(self._status_label)
        self._status_bar.addPermanentWidget(self._status_progress)

    def show_transfer_progress(self, message: str, current: int = 0, total: int = 0):
        """Показать прогресс загрузки/скачивания"""
        self._status_label.setText(message)
        if total > 0:
            self._status_progress.setMaximum(total)
            self._status_progress.setValue(current)
            self._status_progress.show()
        else:
            self._status_progress.hide()

    def hide_transfer_progress(self):
        """Скрыть прогресс"""
        self._status_label.setText("")
        self._status_progress.hide()
