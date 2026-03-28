"""In-memory кэш узлов дерева проектов с TTL и точечной инвалидацией."""
from __future__ import annotations

import logging
import time
import threading
from typing import Dict, List, Optional, Tuple

from app.tree_models import TreeNode

logger = logging.getLogger(__name__)

__all__ = ["TreeNodeCache"]


class TreeNodeCache:
    """
    Потокобезопасный in-memory кэш узлов дерева.

    Хранит:
    - Узлы по ID с TTL
    - Связи parent_id → [child_ids] с TTL
    - Список корневых узлов с TTL
    """

    def __init__(self, ttl_seconds: int = 120, max_size: int = 5000):
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._lock = threading.Lock()

        # node_id → (TreeNode, timestamp)
        self._nodes: Dict[str, Tuple[TreeNode, float]] = {}
        # parent_id → ([child_ids], timestamp)
        self._children: Dict[str, Tuple[List[str], float]] = {}
        # (root_ids, timestamp) или None
        self._root_ids: Optional[Tuple[List[str], float]] = None

    def _is_expired(self, timestamp: float) -> bool:
        if self._ttl <= 0:
            return False
        return time.time() - timestamp > self._ttl

    def _ensure_size(self) -> None:
        """Удалить старейшие записи если кэш переполнен (вызывать под lock)."""
        if len(self._nodes) < self._max_size:
            return
        items = sorted(self._nodes.items(), key=lambda x: x[1][1])
        to_remove = len(self._nodes) // 10 or 1
        for key, _ in items[:to_remove]:
            del self._nodes[key]

    # === Чтение ===

    def get_node(self, node_id: str) -> Optional[TreeNode]:
        with self._lock:
            entry = self._nodes.get(node_id)
            if entry is None:
                return None
            node, ts = entry
            if self._is_expired(ts):
                del self._nodes[node_id]
                return None
            return node

    def get_children(self, parent_id: str) -> Optional[List[TreeNode]]:
        """Вернуть дочерние узлы из кэша или None если нет/expired."""
        with self._lock:
            entry = self._children.get(parent_id)
            if entry is None:
                return None
            child_ids, ts = entry
            if self._is_expired(ts):
                del self._children[parent_id]
                return None
            result = []
            for cid in child_ids:
                node_entry = self._nodes.get(cid)
                if node_entry is None:
                    # Кэш неконсистентен — инвалидируем
                    del self._children[parent_id]
                    return None
                result.append(node_entry[0])
            return result

    def get_root_nodes(self) -> Optional[List[TreeNode]]:
        with self._lock:
            if self._root_ids is None:
                return None
            root_ids, ts = self._root_ids
            if self._is_expired(ts):
                self._root_ids = None
                return None
            result = []
            for rid in root_ids:
                node_entry = self._nodes.get(rid)
                if node_entry is None:
                    self._root_ids = None
                    return None
                result.append(node_entry[0])
            return result

    def has_children_cached(self, parent_id: str) -> bool:
        """Проверить есть ли валидные дети в кэше (без копирования)."""
        with self._lock:
            entry = self._children.get(parent_id)
            if entry is None:
                return False
            _, ts = entry
            return not self._is_expired(ts)

    def get_all_node_names(self) -> Dict[str, str]:
        """Вернуть {node_id: name} для всех закэшированных узлов (для поиска)."""
        now = time.time()
        with self._lock:
            return {
                nid: entry[0].name
                for nid, entry in self._nodes.items()
                if not (self._ttl > 0 and now - entry[1] > self._ttl)
            }

    # === Запись ===

    def put_node(self, node: TreeNode) -> None:
        with self._lock:
            self._nodes[node.id] = (node, time.time())
            self._ensure_size()

    def put_children(self, parent_id: str, children: List[TreeNode]) -> None:
        now = time.time()
        with self._lock:
            child_ids = []
            for child in children:
                self._nodes[child.id] = (child, now)
                child_ids.append(child.id)
            self._children[parent_id] = (child_ids, now)
            self._ensure_size()

    def put_root_nodes(self, roots: List[TreeNode]) -> None:
        now = time.time()
        with self._lock:
            root_ids = []
            for root in roots:
                self._nodes[root.id] = (root, now)
                root_ids.append(root.id)
            self._root_ids = (root_ids, now)
            self._ensure_size()

    # === Инвалидация ===

    def invalidate_node(self, node_id: str) -> None:
        with self._lock:
            self._nodes.pop(node_id, None)

    def invalidate_children(self, parent_id: str) -> None:
        """Инвалидировать список детей (сами узлы остаются)."""
        with self._lock:
            self._children.pop(parent_id, None)

    def invalidate_roots(self) -> None:
        with self._lock:
            self._root_ids = None

    def invalidate_subtree(self, node_id: str) -> None:
        """Рекурсивно инвалидировать узел и всех потомков."""
        with self._lock:
            self._invalidate_subtree_locked(node_id)

    def _invalidate_subtree_locked(self, node_id: str) -> None:
        """Рекурсивная инвалидация (вызывать под lock)."""
        entry = self._children.pop(node_id, None)
        if entry:
            child_ids, _ = entry
            for cid in child_ids:
                self._invalidate_subtree_locked(cid)
        self._nodes.pop(node_id, None)

    # === Обновление полей узла (без перезагрузки) ===

    def update_node_fields(self, node_id: str, **fields) -> Optional[TreeNode]:
        """Обновить поля узла в кэше. Возвращает обновлённый узел или None."""
        with self._lock:
            entry = self._nodes.get(node_id)
            if entry is None:
                return None
            node, _ = entry
            for key, value in fields.items():
                if hasattr(node, key):
                    setattr(node, key, value)
            self._nodes[node_id] = (node, time.time())
            return node

    def clear(self) -> None:
        with self._lock:
            self._nodes.clear()
            self._children.clear()
            self._root_ids = None

    def stats(self) -> dict:
        with self._lock:
            return {
                "nodes": len(self._nodes),
                "children_lists": len(self._children),
                "has_roots": self._root_ids is not None,
            }
