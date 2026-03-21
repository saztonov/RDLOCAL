"""Миксин загрузки и обновления дерева проектов."""

import logging
import time as _time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QTreeWidgetItem

from app.tree_client import NodeType, TreeNode

logger = logging.getLogger(__name__)


class TreeLoadMixin:
    """Начальная загрузка, обновление, lazy loading дерева."""

    def _initial_load(self):
        """Начальная загрузка (асинхронно через QThread)"""
        if not self.client.supabase_url or not self.client.supabase_key:
            self.status_label.setText("⚠ Supabase не настроен")
            return

        self._load_expanded_state()
        self._loading = True
        self._pdf_status_manager.reset()
        self.status_label.setText("⏳ Загрузка дерева...")
        self.tree.clear()
        self._node_map.clear()


        from .initial_load_worker import InitialLoadWorker

        self._initial_load_worker = InitialLoadWorker(self.client, self)
        self._initial_load_worker.roots_loaded.connect(self._on_roots_loaded)
        self._initial_load_worker.stats_loaded.connect(self._on_stats_loaded)
        self._initial_load_worker.statuses_loaded.connect(self._on_statuses_loaded)
        self._initial_load_worker.error.connect(self._on_load_error)
        self._initial_load_worker.finished_all.connect(self._on_load_finished)
        self._initial_load_worker.start()

    def _on_roots_loaded(self, roots: list):
        """Обработка корневых узлов"""
        t0 = _time.time()
        self._last_node_count = len(roots)
        # Кэшируем корневые узлы
        self._node_cache.put_root_nodes(roots)

        self.tree.setUpdatesEnabled(False)
        try:
            for node in roots:
                item = self._item_builder.create_item(node)
                self.tree.addTopLevelItem(item)
                self._item_builder.add_placeholder(item, node)
        finally:
            self.tree.setUpdatesEnabled(True)

        logger.info(f"_on_roots_loaded: {len(roots)} items in {_time.time() - t0:.2f}s")
        self.status_label.setText(f"Проектов: {len(roots)}")

        doc_ids = []
        for node_id, item in self._node_map.items():
            node = item.data(0, Qt.UserRole)
            if isinstance(node, TreeNode) and node.node_type == NodeType.DOCUMENT:
                doc_ids.append(node_id)

        if self._initial_load_worker and doc_ids:
            self._initial_load_worker.set_doc_ids(doc_ids)

    def _on_stats_loaded(self, stats: dict):
        """Обработка статистики дерева"""
        pdf_count = stats.get("pdf_count", 0)
        md_count = stats.get("md_count", 0)
        folders_with_pdf = stats.get("folders_with_pdf", 0)
        self.stats_label.setText(
            f"📄 PDF: {pdf_count}  |  📝 MD: {md_count}  |  📁 Папок с PDF: {folders_with_pdf}"
        )

    def _on_statuses_loaded(self, statuses: dict):
        """Обработка PDF статусов"""
        t0 = _time.time()
        self.tree.setUpdatesEnabled(False)
        try:
            self._pdf_status_manager.apply_statuses(statuses)
        finally:
            self.tree.setUpdatesEnabled(True)
        logger.info(f"_on_statuses_loaded: {len(statuses)} items in {_time.time() - t0:.2f}s")

    def _on_load_error(self, error_msg: str):
        """Обработка ошибки загрузки"""
        logger.error(f"Initial load error: {error_msg}")
        self.status_label.setText(f"⚠ Ошибка: {error_msg[:50]}")
        self._loading = False

    def _on_load_finished(self):
        """Загрузка завершена"""
        self._loading = False
        QTimer.singleShot(100, self._restore_expanded_state)

    # === Обновление дерева ===

    def _refresh_tree(self):
        """Полное обновление дерева (через фоновый воркер)."""
        if self._loading:
            return
        self._loading = True
        self._pdf_status_manager.reset()
        self.status_label.setText("Загрузка...")
        self._node_cache.invalidate_roots()
        self._refresh_worker.request_refresh_roots()

    def _on_roots_refreshed(self, roots: list):
        """Слот: корневые узлы загружены воркером — инкрементальное обновление."""
        self._last_node_count = len(roots)
        self._incremental_refresh_roots(roots)
        self.status_label.setText(f"Проектов: {len(roots)}")

        QTimer.singleShot(100, self._restore_expanded_state)
        QTimer.singleShot(300, self._update_stats)

        if not self._pdf_status_manager.is_loaded:
            QTimer.singleShot(200, self._pdf_status_manager.load_batch)

        self._loading = False

    def _incremental_refresh_roots(self, fresh_roots: list):
        """Инкрементальное обновление корневых узлов без tree.clear()."""
        fresh_map = {r.id: r for r in fresh_roots}
        fresh_ids = set(fresh_map.keys())

        # Текущие корневые ID
        current_ids = set()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            node = item.data(0, Qt.UserRole)
            if isinstance(node, TreeNode):
                current_ids.add(node.id)

        # Удалить пропавшие (рекурсивная очистка _node_map ДО удаления из дерева)
        for i in range(self.tree.topLevelItemCount() - 1, -1, -1):
            item = self.tree.topLevelItem(i)
            node = item.data(0, Qt.UserRole)
            if isinstance(node, TreeNode) and node.id not in fresh_ids:
                self._remove_subtree_from_node_map(item)
                self.tree.takeTopLevelItem(i)

        # Обновить существующие
        for node in fresh_roots:
            if node.id in self._node_map:
                item = self._node_map[node.id]
                self._item_builder.update_item_display(item, node)
            else:
                # Добавить новые
                item = self._item_builder.create_item(node)
                self.tree.addTopLevelItem(item)
                self._item_builder.add_placeholder(item, node)

    # === Автообновление (фоновое) ===

    def _auto_refresh_tree(self):
        """Автоматическое обновление дерева (через фоновый воркер)."""
        if self._loading:
            return

        known_roots = {}
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            node = item.data(0, Qt.UserRole)
            if isinstance(node, TreeNode):
                known_roots[node.id] = node.updated_at

        self._refresh_worker.request_auto_check(known_roots)

    def _on_auto_check_result(self, changes: dict):
        """Слот: результат фоновой проверки автообновления."""
        if changes.get("no_changes"):
            return

        added = changes.get("added", [])
        removed = changes.get("removed", [])
        updated = changes.get("updated", [])

        # Удалить пропавшие корневые узлы (рекурсивная очистка ДО удаления)
        for node_id in removed:
            item = self._node_map.get(node_id)
            if item:
                self._remove_subtree_from_node_map(item)
                idx = self.tree.indexOfTopLevelItem(item)
                if idx >= 0:
                    self.tree.takeTopLevelItem(idx)
            else:
                pass

        # Обновить изменённые
        for node in updated:
            item = self._node_map.get(node.id)
            if item:
                self._item_builder.update_item_display(item, node)

        # Добавить новые
        for node in added:
            item = self._item_builder.create_item(node)
            self.tree.addTopLevelItem(item)
            self._item_builder.add_placeholder(item, node)

        self._last_node_count = self.tree.topLevelItemCount()

    # === Точечное обновление одного элемента ===

    def _update_single_item(self, node_id: str, **fields):
        """Обновить один элемент дерева без перезагрузки."""
        item = self._node_map.get(node_id)
        if not item:
            return

        node = item.data(0, Qt.UserRole)
        if not isinstance(node, TreeNode):
            return

        for key, value in fields.items():
            if hasattr(node, key):
                setattr(node, key, value)

        # Обновляем кэш
        self._node_cache.update_node_fields(node_id, **fields)

        self._item_builder.update_item_display(item, node)

    def _refresh_visible_items(self):
        """Обновить отображение всех видимых элементов (иконки, текст)."""

        def _update_recursive(item: QTreeWidgetItem):
            node = item.data(0, Qt.UserRole)
            if isinstance(node, TreeNode):
                self._item_builder.update_item_display(item, node)
            for i in range(item.childCount()):
                _update_recursive(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            _update_recursive(self.tree.topLevelItem(i))

    def _refresh_siblings(self, parent_id: str):
        """Перезагрузить дочерние узлы одного родителя."""
        parent_item = self._node_map.get(parent_id)
        if not parent_item:
            return

        # Рекурсивно удаляем всех потомков из _node_map (ДО takeChildren)
        for i in range(parent_item.childCount()):
            self._remove_subtree_from_node_map(parent_item.child(i))

        # Очищаем визуальных детей
        parent_item.takeChildren()

        # Инвалидируем кэш
        self._node_cache.invalidate_children(parent_id)

        # Загружаем заново через воркер
        self._refresh_worker.request_load_children(parent_id)

    # === Lazy loading ===

    def _load_children(self, parent_item: QTreeWidgetItem, parent_node):
        """Загрузить дочерние узлы (cache-first, fallback на sync)."""
        # Пробуем кэш
        cached = self._node_cache.get_children(parent_node.id)
        if cached is not None:
            self._populate_children(parent_item, cached)
            return

        # Кэш пуст — загружаем синхронно (быстрее чем async для UX раскрытия)
        try:
            children = self.client.get_children(parent_node.id)
            self._node_cache.put_children(parent_node.id, children)
            self._populate_children(parent_item, children)
        except Exception as e:
            logger.error(f"Failed to load children: {e}")

    def _on_children_loaded(self, parent_id: str, children: list):
        """Слот: дети загружены фоновым воркером."""
        parent_item = self._node_map.get(parent_id)
        if not parent_item:
            return

        # Удалить loading placeholder
        for i in range(parent_item.childCount() - 1, -1, -1):
            child = parent_item.child(i)
            data = child.data(0, Qt.UserRole)
            if data in ("placeholder", "loading"):
                parent_item.removeChild(child)

        self._populate_children(parent_item, children)

    def _populate_children(self, parent_item: QTreeWidgetItem, children: list):
        """Заполнить дочерние элементы из списка TreeNode."""
        for child in children:
            if child.id not in self._node_map:
                child_item = self._item_builder.create_item(child)
                parent_item.addChild(child_item)
                self._item_builder.add_placeholder(child_item, child)

    def _update_stats(self):
        """Обновить статистику документов"""
        try:
            stats = self.client.get_tree_stats()
            pdf_count = stats.get("pdf_count", 0)
            md_count = stats.get("md_count", 0)
            folders_with_pdf = stats.get("folders_with_pdf", 0)
            self.stats_label.setText(
                f"📄 PDF: {pdf_count}  |  📝 MD: {md_count}  |  📁 Папок с PDF: {folders_with_pdf}"
            )
        except Exception as e:
            logger.debug(f"Failed to update stats: {e}")
            self.stats_label.setText("")
