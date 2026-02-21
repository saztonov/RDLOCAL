"""Виджет дерева проектов с поддержкой Supabase"""
from __future__ import annotations

import logging
from typing import Dict, List

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.sync_check_worker import SyncCheckWorker, SyncStatus
from app.gui.tree_context_menu import TreeContextMenuMixin
from app.gui.tree_delegates import VersionHighlightDelegate
from app.gui.tree_filter_mixin import TreeFilterMixin
from app.gui.tree_node_operations import STATUS_COLORS, TreeNodeOperationsMixin
from app.gui.tree_sync_mixin import TreeSyncMixin
from app.tree_client import NodeType, TreeClient, TreeNode

from .annotation_operations import AnnotationOperations
from .initial_load_worker import InitialLoadWorker
from .pdf_status_manager import PDFStatusManager
from .r2_viewer_integration import R2ViewerIntegration
from .tree_expand_mixin import TreeExpandMixin
from .tree_item_builder import TreeItemBuilder
from .tree_load_mixin import TreeLoadMixin
from .tree_node_cache import TreeNodeCache
from .tree_refresh_worker import TreeRefreshWorker
from .tree_reorder_mixin import TreeReorderMixin

logger = logging.getLogger(__name__)

__all__ = ["ProjectTreeWidget"]


class ProjectTreeWidget(
    TreeNodeOperationsMixin,
    TreeSyncMixin,
    TreeFilterMixin,
    TreeContextMenuMixin,
    TreeLoadMixin,
    TreeExpandMixin,
    TreeReorderMixin,
    QWidget,
):
    """Виджет дерева проектов"""

    document_selected = Signal(str, str)  # node_id, r2_key
    file_uploaded_r2 = Signal(str, str)  # node_id, r2_key
    annotation_replaced = Signal(str)  # r2_key

    def __init__(self, parent=None):
        super().__init__(parent)
        self.client = TreeClient()
        self._node_map: Dict[str, QTreeWidgetItem] = {}
        self._stage_types: List = []
        self._section_types: List = []
        self._loading = False
        self._current_document_id: str = ""
        self._auto_refresh_timer: QTimer = None
        self._last_node_count: int = 0
        self._sync_statuses: Dict[str, SyncStatus] = {}
        self._sync_worker: SyncCheckWorker = None
        self._expanded_nodes: set = set()
        self._initial_load_worker: InitialLoadWorker = None

        # Поиск: состояние
        self._search_active = False
        self._pre_search_expanded = set()
        self._pending_batch_parent_ids = []

        # Кэш и фоновый воркер
        self._node_cache = TreeNodeCache(ttl_seconds=120)
        self._refresh_worker = TreeRefreshWorker(self.client, self._node_cache, self)
        self._refresh_worker.roots_loaded.connect(self._on_roots_refreshed)
        self._refresh_worker.children_loaded.connect(self._on_children_loaded)
        self._refresh_worker.auto_check_result.connect(self._on_auto_check_result)
        self._refresh_worker.children_batch_loaded.connect(self._on_batch_children_loaded)

        # Вспомогательные компоненты
        self._pdf_status_manager = PDFStatusManager(self)
        self._annotation_ops = AnnotationOperations(self)
        self._r2_viewer = R2ViewerIntegration(self)
        self._item_builder = TreeItemBuilder(self)

        self._setup_ui()
        self._setup_auto_refresh()
        QTimer.singleShot(100, self._initial_load)

    def _setup_auto_refresh(self):
        """Настроить автообновление"""
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._auto_refresh_tree)
        self._auto_refresh_timer.start(30000)

        self._cache_cleanup_timer = QTimer(self)
        self._cache_cleanup_timer.timeout.connect(self._pdf_status_manager.cleanup_cache)
        self._cache_cleanup_timer.start(60000)

        self._pdf_status_refresh_timer = QTimer(self)
        self._pdf_status_refresh_timer.timeout.connect(self._pdf_status_manager.auto_refresh)
        self._pdf_status_refresh_timer.start(30000)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = self._create_header()
        layout.addWidget(header)

        # Search с debounce
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #3c3c3c; color: #e0e0e0;
                border: 1px solid #555; padding: 6px; border-radius: 2px;
            }
            QLineEdit:focus { border: 1px solid #0e639c; }
        """)

        # Debounce: 300мс задержка перед фильтрацией
        self._pending_search_text = ""
        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.setInterval(300)
        self._search_debounce_timer.timeout.connect(self._do_filter_tree)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        layout.addWidget(self.search_input)

        # Tree
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setFrameShape(QFrame.NoFrame)
        self.tree.setAnimated(True)
        self.tree.setIndentation(20)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemCollapsed.connect(self._on_item_collapsed)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.installEventFilter(self)
        self.tree.setStyleSheet("""
            QTreeWidget { background-color: #1e1e1e; color: #e0e0e0; outline: none; border: none; }
            QTreeWidget::item { padding: 4px; border-radius: 2px; }
            QTreeWidget::item:hover { background-color: #2a2d2e; }
            QTreeWidget::item:selected { background-color: #094771; }
        """)
        self.tree.setItemDelegate(VersionHighlightDelegate(self.tree))
        layout.addWidget(self.tree)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666; font-size: 8pt; padding: 4px;")
        layout.addWidget(self.status_label)

        # Статистика документов
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet(
            "color: #888; font-size: 8pt; padding: 4px; background-color: #252526; "
            "border-top: 1px solid #3e3e42;"
        )
        layout.addWidget(self.stats_label)

    def _on_search_text_changed(self, text: str):
        """Debounced обработчик поиска."""
        self._pending_search_text = text
        self._search_debounce_timer.start()

    def _do_filter_tree(self):
        """Выполнить фильтрацию (после debounce)."""
        self._filter_tree(self._pending_search_text)

    def _on_batch_children_loaded(self, results: dict):
        """Слот: batch-загрузка детей завершена (для поиска)."""
        for parent_id, children in results.items():
            parent_item = self._node_map.get(parent_id)
            if parent_item:
                # Удалить placeholder
                for i in range(parent_item.childCount() - 1, -1, -1):
                    child = parent_item.child(i)
                    data = child.data(0, Qt.UserRole)
                    if data in ("placeholder", "loading"):
                        parent_item.removeChild(child)
                self._populate_children(parent_item, children)

        # Перезапускаем фильтр если был активен поиск
        if self._search_active and self._pending_search_text:
            self._filter_tree(self._pending_search_text)

    def _create_header(self) -> QWidget:
        """Создать заголовок с кнопками"""
        header = QWidget()
        header.setStyleSheet("background-color: #252526; border-bottom: 1px solid #3e3e42;")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(10, 10, 10, 10)
        header_layout.setSpacing(10)

        title_label = QLabel("ДЕРЕВО ПРОЕКТОВ")
        title_label.setStyleSheet("color: #bbbbbb; font-weight: bold; font-size: 9pt;")
        header_layout.addWidget(title_label)

        btns_layout = QHBoxLayout()
        btns_layout.setSpacing(8)

        # Create button
        self.create_btn = QPushButton("+ Проект")
        self.create_btn.setCursor(Qt.PointingHandCursor)
        self.create_btn.setStyleSheet("""
            QPushButton { background-color: #0e639c; color: white; border: none;
                         padding: 6px 16px; border-radius: 4px; font-weight: 500; }
            QPushButton:hover { background-color: #1177bb; }
            QPushButton:pressed { background-color: #0a4d78; }
        """)
        self.create_btn.clicked.connect(self._create_project)

        icon_btn_style = """
            QPushButton { background-color: #3e3e42; color: #cccccc; border: none;
                         border-radius: 4px; font-size: 12px; font-weight: bold; }
            QPushButton:hover { background-color: #505054; color: #ffffff; }
            QPushButton:pressed { background-color: #0e639c; }
        """

        self.expand_btn = self._create_icon_btn("▼", "Развернуть (выбранную папку или всё)", self._expand_selected, icon_btn_style)
        self.collapse_btn = self._create_icon_btn("▲", "Свернуть (выбранную папку или всё)", self._collapse_selected, icon_btn_style)
        self.sync_btn = self._create_icon_btn("🔄", "Синхронизация", self._sync_and_refresh, icon_btn_style)

        btns_layout.addWidget(self.create_btn)
        btns_layout.addWidget(self.expand_btn)
        btns_layout.addWidget(self.collapse_btn)
        btns_layout.addWidget(self.sync_btn)
        header_layout.addLayout(btns_layout)

        return header

    def _create_icon_btn(self, text: str, tooltip: str, callback, style: str) -> QPushButton:
        """Создать иконочную кнопку"""
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.setFixedSize(32, 32)
        btn.setStyleSheet(style)
        btn.clicked.connect(callback)
        return btn

    def refresh_types(self):
        """Обновить кэшированные типы"""
        try:
            self._stage_types = self.client.get_stage_types()
            self._section_types = self.client.get_section_types()
        except Exception as e:
            logger.error(f"Failed to load types: {e}")

    def _sync_and_refresh(self):
        """Синхронизация: обновить дерево и проверить синхронизацию"""
        self._node_cache.clear()
        self._refresh_tree()
        QTimer.singleShot(500, self._start_sync_check)

    def _on_item_expanded(self, item: QTreeWidgetItem):
        """Lazy loading при раскрытии"""
        node = item.data(0, Qt.UserRole)
        if isinstance(node, TreeNode):
            self._expanded_nodes.add(node.id)
            self._save_expanded_state()

        if item.childCount() == 1:
            child = item.child(0)
            if child.data(0, Qt.UserRole) == "placeholder":
                if isinstance(node, TreeNode):
                    item.removeChild(child)
                    self._load_children(item, node)
                    QTimer.singleShot(100, self._start_sync_check)

    def _on_item_collapsed(self, item: QTreeWidgetItem):
        node = item.data(0, Qt.UserRole)
        if isinstance(node, TreeNode):
            self._expanded_nodes.discard(node.id)
            self._save_expanded_state()

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        """Двойной клик - открыть документ"""
        data = item.data(0, Qt.UserRole)
        if isinstance(data, TreeNode) and data.node_type == NodeType.DOCUMENT:
            r2_key = data.attributes.get("r2_key", "")
            if r2_key:
                self.highlight_document(data.id)
                self.document_selected.emit(data.id, r2_key)

    def highlight_document(self, node_id: str):
        """Подсветить текущий открытый документ"""
        if self._current_document_id and self._current_document_id in self._node_map:
            prev_item = self._node_map[self._current_document_id]
            prev_node = prev_item.data(0, Qt.UserRole)
            if isinstance(prev_node, TreeNode):
                prev_item.setBackground(0, QColor("transparent"))
                prev_item.setForeground(0, QColor(STATUS_COLORS.get(prev_node.status, "#e0e0e0")))

        self._current_document_id = node_id
        if node_id and node_id in self._node_map:
            item = self._node_map[node_id]
            item.setBackground(0, QColor("#264f78"))
            item.setForeground(0, QColor("#ffffff"))
            self.tree.scrollToItem(item)

    def eventFilter(self, obj, event):
        if obj == self.tree and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Delete:
                item = self.tree.currentItem()
                if item:
                    node = item.data(0, Qt.UserRole)
                    if isinstance(node, TreeNode):
                        self._delete_node(node)
                        return True
        return super().eventFilter(obj, event)

    # Делегация к компонентам
    def _copy_annotation(self, node: TreeNode):
        self._annotation_ops.copy_annotation(node)

    def _paste_annotation(self, node: TreeNode):
        self._annotation_ops.paste_annotation(node)

    def _detect_and_assign_stamps(self, node: TreeNode):
        self._annotation_ops.detect_and_assign_stamps(node)

    def _upload_annotation_dialog(self, node: TreeNode):
        self._annotation_ops.upload_from_file(node)

    def _view_on_r2(self, node: TreeNode):
        self._r2_viewer.view_on_r2(node)

    def _get_pdf_status_icon(self, status: str) -> str:
        return PDFStatusManager.get_status_icon(status)

    # Управление блокировкой документов
    def _lock_document(self, node: TreeNode):
        try:
            if self.client.lock_document(node.id):
                node.is_locked = True
                self.status_label.setText("🔒 Документ заблокирован")
                self._update_main_window_lock_state(node.id, True)
                self._update_single_item(node.id, is_locked=True)
            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось заблокировать документ")
        except Exception as e:
            logger.error(f"Lock document failed: {e}")
            QMessageBox.critical(self, "Ошибка", f"Ошибка блокировки: {e}")

    def _unlock_document(self, node: TreeNode):
        try:
            if self.client.unlock_document(node.id):
                node.is_locked = False
                self.status_label.setText("🔓 Документ разблокирован")
                self._update_main_window_lock_state(node.id, False)
                self._update_single_item(node.id, is_locked=False)
            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось разблокировать документ")
        except Exception as e:
            logger.error(f"Unlock document failed: {e}")
            QMessageBox.critical(self, "Ошибка", f"Ошибка разблокировки: {e}")

    def _update_main_window_lock_state(self, node_id: str, locked: bool):
        """Обновить состояние блокировки в главном окне"""
        main_window = self.window()
        if hasattr(main_window, "_current_node_id") and main_window._current_node_id == node_id:
            main_window._current_node_locked = locked
            if hasattr(main_window, "page_viewer"):
                main_window.page_viewer.read_only = locked
            if hasattr(main_window, "move_block_up_btn"):
                main_window.move_block_up_btn.setEnabled(not locked)
            if hasattr(main_window, "move_block_down_btn"):
                main_window.move_block_down_btn.setEnabled(not locked)

    def _check_document_locked(self, node: TreeNode) -> bool:
        if node.node_type == NodeType.DOCUMENT and node.is_locked:
            QMessageBox.warning(self, "Документ заблокирован",
                              "Этот документ заблокирован от изменений.\nСначала снимите блокировку.")
            return True
        return False

    def _verify_blocks(self, node: TreeNode):
        from app.gui.block_verification_dialog import BlockVerificationDialog
        r2_key = node.attributes.get("r2_key", "")
        if not r2_key:
            QMessageBox.warning(self, "Ошибка", "Документ не имеет привязки к R2")
            return
        dialog = BlockVerificationDialog(node.name, r2_key, self, node_id=node.id)
        dialog.exec()

    def _view_in_supabase(self, node: TreeNode):
        from app.gui.node_files_dialog import NodeFilesDialog
        dialog = NodeFilesDialog(node, self.client, self)
        dialog.exec()

    def _reconcile_files(self, node: TreeNode):
        """Открыть диалог сверки файлов R2/Supabase"""
        from app.gui.file_reconciliation_dialog import FileReconciliationDialog
        dialog = FileReconciliationDialog(node, self.client, self)
        dialog.exec()

    def navigate_to_node(self, node_id: str) -> bool:
        """Навигация к узлу: раскрытие предков, выделение и скролл.

        Returns:
            True если узел найден и выделен.
        """
        # Быстрый путь: узел уже загружен
        if node_id in self._node_map:
            self._select_and_scroll(node_id)
            return True

        # Получаем цепочку предков (от корня к родителю)
        try:
            ancestors = self.client.get_ancestors(node_id)
        except Exception as e:
            logger.error(f"Failed to get ancestors for {node_id}: {e}")
            return False

        if not ancestors:
            logger.warning(f"Node {node_id} not found in tree and has no ancestors")
            return False

        # Последовательно раскрываем каждого предка
        for ancestor in ancestors:
            if ancestor.id not in self._node_map:
                logger.warning(f"Ancestor {ancestor.id} ({ancestor.name}) not in tree")
                return False

            ancestor_item = self._node_map[ancestor.id]
            if not ancestor_item.isExpanded():
                if (ancestor_item.childCount() == 1
                        and ancestor_item.child(0).data(0, Qt.UserRole) == "placeholder"):
                    ancestor_item.removeChild(ancestor_item.child(0))
                    self._load_children(ancestor_item, ancestor)
                ancestor_item.setExpanded(True)
                self._expanded_nodes.add(ancestor.id)

        # Целевой узел должен быть в _node_map после раскрытия предков
        if node_id in self._node_map:
            self._select_and_scroll(node_id)
            return True

        # Последняя попытка: загрузить детей последнего предка
        last_ancestor = ancestors[-1]
        if last_ancestor.id in self._node_map:
            last_item = self._node_map[last_ancestor.id]
            self._load_children(last_item, last_ancestor)
            if node_id in self._node_map:
                self._select_and_scroll(node_id)
                return True

        logger.warning(f"Node {node_id} not found after expanding ancestors")
        return False

    def _select_and_scroll(self, node_id: str):
        """Выделить узел в дереве и прокрутить к нему."""
        item = self._node_map.get(node_id)
        if not item:
            return
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        self.highlight_document(node_id)

    # Свойство для доступа к скопированной аннотации (для контекстного меню)
    @property
    def _copied_annotation(self) -> Dict:
        return self._annotation_ops._copied_annotation
