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
from app.gui.main_window_state import MainWindowState
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

        # Централизованный state container
        self.state = MainWindowState()

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

    # ── Property-алиасы (обратная совместимость с mixins) ─────────────

    @property
    def pdf_document(self):
        return self.state.pdf_document

    @pdf_document.setter
    def pdf_document(self, value):
        self.state.pdf_document = value

    @property
    def annotation_document(self):
        return self.state.annotation_document

    @annotation_document.setter
    def annotation_document(self, value):
        self.state.annotation_document = value

    @property
    def current_page(self):
        return self.state.current_page

    @current_page.setter
    def current_page(self, value):
        self.state.current_page = value

    @property
    def page_images(self):
        return self.state.page_images

    @page_images.setter
    def page_images(self, value):
        self.state.page_images = value

    @property
    def _page_images_order(self):
        return self.state._page_images_order

    @_page_images_order.setter
    def _page_images_order(self, value):
        self.state._page_images_order = value

    @property
    def _page_images_max(self):
        return self.state._page_images_max

    @property
    def page_zoom_states(self):
        return self.state.page_zoom_states

    @page_zoom_states.setter
    def page_zoom_states(self, value):
        self.state.page_zoom_states = value

    @property
    def _current_pdf_path(self):
        return self.state.current_pdf_path

    @_current_pdf_path.setter
    def _current_pdf_path(self, value):
        self.state.current_pdf_path = value

    @property
    def _current_node_id(self):
        return self.state.current_node_id

    @_current_node_id.setter
    def _current_node_id(self, value):
        self.state.current_node_id = value

    @property
    def _current_node_locked(self):
        return self.state.current_node_locked

    @_current_node_locked.setter
    def _current_node_locked(self, value):
        self.state.current_node_locked = value

    @property
    def _current_temp_dir(self):
        return self.state.current_temp_workspace

    @_current_temp_dir.setter
    def _current_temp_dir(self, value):
        self.state.current_temp_workspace = value

    @property
    def _current_document_origin(self):
        return self.state.current_document_origin

    @_current_document_origin.setter
    def _current_document_origin(self, value):
        self.state.current_document_origin = value

    @property
    def undo_stack(self):
        return self.state.undo_stack

    @undo_stack.setter
    def undo_stack(self, value):
        self.state.undo_stack = value

    @property
    def redo_stack(self):
        return self.state.redo_stack

    @redo_stack.setter
    def redo_stack(self, value):
        self.state.redo_stack = value

    @property
    def _blocks_clipboard(self):
        return self.state.blocks_clipboard

    @_blocks_clipboard.setter
    def _blocks_clipboard(self, value):
        self.state.blocks_clipboard = value

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
        # Flush аннотации в Supabase до закрытия
        self._flush_pending_save()

        # Переключить логи ДО удаления temp
        from app.logging_manager import get_logging_manager

        get_logging_manager().switch_to_projects_folder()

        if self.pdf_document:
            self.pdf_document.close()
        self.pdf_document = None
        self.annotation_document = None
        self._current_pdf_path = None

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

        # Удалить temp-сессию tree-документа
        if self._current_document_origin == "tree_temp" and self._current_temp_dir:
            from app.gui.temp_session import get_temp_session_manager

            get_temp_session_manager().cleanup(self._current_temp_dir)

        # Сброс temp-state
        self._current_temp_dir = None
        self._current_document_origin = "local"
        self._current_r2_key = None
        self._current_node_id = None
        self._current_node_locked = False

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

        # Удалить temp-сессию если есть
        if self._current_document_origin == "tree_temp" and self._current_temp_dir:
            from app.gui.temp_session import get_temp_session_manager

            get_temp_session_manager().cleanup(self._current_temp_dir)

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

    def _do_auto_markup(self, node):
        """Создать текстовый блок на всю страницу для каждой страницы документа"""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox

        from rd_core.models import Block, BlockSource, BlockType

        # Документ должен быть открыт
        if not self._current_node_id or self._current_node_id != node.id:
            QMessageBox.warning(
                self, "Ошибка", "Сначала откройте документ (кликните по нему в дереве)"
            )
            return

        if not self.pdf_document or not self.annotation_document:
            QMessageBox.warning(self, "Ошибка", "PDF документ не загружен")
            return

        # Проверка блокировки
        if self._current_node_locked:
            QMessageBox.warning(self, "Ошибка", "Документ заблокирован")
            return

        # Предупреждение если уже есть блоки
        existing_blocks = sum(len(p.blocks) for p in self.annotation_document.pages)
        if existing_blocks > 0:
            reply = QMessageBox.question(
                self,
                "Авторазметка файла",
                f"В документе уже есть {existing_blocks} блок(ов).\n"
                "Добавить полностраничные блоки на все страницы?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self._save_undo_state()

        page_count = self.pdf_document.page_count
        for page_idx in range(page_count):
            page = self._get_or_create_page(page_idx)
            if not page:
                continue

            block = Block.create(
                page_index=page_idx,
                coords_px=(0, 0, page.width, page.height),
                page_width=page.width,
                page_height=page.height,
                block_type=BlockType.TEXT,
                source=BlockSource.USER,
            )
            page.blocks.append(block)

        # Обновить UI для текущей страницы
        current_page = self.annotation_document.pages[self.current_page]
        self.page_viewer.set_blocks(current_page.blocks)
        QTimer.singleShot(0, self.blocks_tree_manager.update_blocks_tree)
        self._auto_save_annotation()

        logger.info(f"Auto-markup: created {page_count} full-page blocks for '{node.name}'")
