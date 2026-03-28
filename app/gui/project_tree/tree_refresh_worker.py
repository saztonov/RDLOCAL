"""Фоновый воркер для сетевых операций дерева проектов."""
from __future__ import annotations

import logging
import queue
from enum import Enum, auto
from typing import TYPE_CHECKING, Dict, List, Optional

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    from app.gui.project_tree.tree_node_cache import TreeNodeCache
    from app.tree_client import TreeClient

logger = logging.getLogger(__name__)

__all__ = ["TreeRefreshWorker"]


class _TaskType(Enum):
    REFRESH_ROOTS = auto()
    LOAD_CHILDREN = auto()
    AUTO_CHECK = auto()
    LOAD_CHILDREN_BATCH = auto()
    STOP = auto()


class TreeRefreshWorker(QThread):
    """
    Фоновый воркер для всех сетевых операций дерева.

    Принимает задачи через очередь, выполняет в фоновом потоке,
    отправляет результаты через сигналы в UI-поток.
    """

    roots_loaded = Signal(list)  # [TreeNode]
    children_loaded = Signal(str, list)  # parent_id, [TreeNode]
    auto_check_result = Signal(dict)  # {"no_changes": bool, "added": [...], ...}
    children_batch_loaded = Signal(dict)  # {parent_id: [TreeNode]}
    error = Signal(str)

    def __init__(
        self,
        client: "TreeClient",
        cache: "TreeNodeCache",
        parent=None,
    ):
        super().__init__(parent)
        self._client = client
        self._cache = cache
        self._queue: queue.Queue = queue.Queue()
        self._running = True

    def stop(self) -> None:
        self._running = False
        self._queue.put((_TaskType.STOP, None))
        self.wait(5000)

    # === Публичные методы (вызывать из UI-потока) ===

    def request_refresh_roots(self) -> None:
        self._queue.put((_TaskType.REFRESH_ROOTS, None))
        if not self.isRunning():
            self.start()

    def request_load_children(self, parent_id: str) -> None:
        self._queue.put((_TaskType.LOAD_CHILDREN, parent_id))
        if not self.isRunning():
            self.start()

    def request_auto_check(self, known_roots: Dict[str, Optional[str]]) -> None:
        """known_roots: {node_id: updated_at_str}"""
        self._queue.put((_TaskType.AUTO_CHECK, known_roots))
        if not self.isRunning():
            self.start()

    def request_load_children_batch(self, parent_ids: List[str]) -> None:
        self._queue.put((_TaskType.LOAD_CHILDREN_BATCH, parent_ids))
        if not self.isRunning():
            self.start()

    # === Фоновый поток ===

    def run(self) -> None:
        while self._running:
            try:
                task_type, payload = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if task_type == _TaskType.STOP:
                break

            try:
                if task_type == _TaskType.REFRESH_ROOTS:
                    self._do_refresh_roots()
                elif task_type == _TaskType.LOAD_CHILDREN:
                    self._do_load_children(payload)
                elif task_type == _TaskType.AUTO_CHECK:
                    self._do_auto_check(payload)
                elif task_type == _TaskType.LOAD_CHILDREN_BATCH:
                    self._do_load_children_batch(payload)
            except Exception as e:
                logger.error(f"TreeRefreshWorker error ({task_type}): {e}")
                self.error.emit(str(e))

    def _do_refresh_roots(self) -> None:
        roots = self._client.get_root_nodes()
        self._cache.put_root_nodes(roots)
        self.roots_loaded.emit(roots)

    def _do_load_children(self, parent_id: str) -> None:
        children = self._client.get_children(parent_id)
        self._cache.put_children(parent_id, children)
        self.children_loaded.emit(parent_id, children)

    def _do_auto_check(self, known_roots: Dict[str, Optional[str]]) -> None:
        """Сравнить текущие корневые узлы с известными — вернуть diff."""

        try:
            fresh_roots = self._client.get_root_nodes()
        except Exception as e:
            logger.debug(f"Auto-check failed: {e}")
            return

        self._cache.put_root_nodes(fresh_roots)

        fresh_map = {r.id: r for r in fresh_roots}
        known_ids = set(known_roots.keys())
        fresh_ids = set(fresh_map.keys())

        added = [fresh_map[rid] for rid in (fresh_ids - known_ids)]
        removed = list(known_ids - fresh_ids)

        updated = []
        for rid in known_ids & fresh_ids:
            fresh_node = fresh_map[rid]
            old_updated_at = known_roots.get(rid)
            if old_updated_at != fresh_node.updated_at:
                updated.append(fresh_node)

        if not added and not removed and not updated:
            self.auto_check_result.emit({"no_changes": True})
        else:
            self.auto_check_result.emit({
                "no_changes": False,
                "added": added,
                "removed": removed,
                "updated": updated,
            })

    def _do_load_children_batch(self, parent_ids: List[str]) -> None:
        result: Dict[str, list] = {}
        for pid in parent_ids:
            if not self._running:
                break
            # Пропускаем уже закэшированные
            if self._cache.has_children_cached(pid):
                cached = self._cache.get_children(pid)
                if cached is not None:
                    result[pid] = cached
                    continue
            try:
                children = self._client.get_children(pid)
                self._cache.put_children(pid, children)
                result[pid] = children
            except Exception as e:
                logger.debug(f"Batch load children for {pid} failed: {e}")
        self.children_batch_loaded.emit(result)
