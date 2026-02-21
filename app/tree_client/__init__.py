"""
Клиент для работы с деревом проектов в Supabase.

Модуль разбит на компоненты:
- core.py - HTTP методы и connection pooling
- nodes.py - CRUD операции с узлами
- status.py - PDF статусы и блокировка
- files.py - Работа с node_files
- categories.py - Категории изображений
- path_v2.py - Materialized path операции
- annotations.py - CRUD аннотаций (таблица annotations)
"""
from __future__ import annotations

from dataclasses import dataclass

from app.tree_models import (
    ALLOWED_CHILDREN,
    FileType,
    NodeFile,
    NodeStatus,
    NodeType,
    SectionType,
    StageType,
    TreeNode,
)

from .annotations import TreeAnnotationsMixin
from .categories import TreeCategoriesMixin
from .core import TreeClientCore
from .files import TreeFilesMixin
from .nodes import TreeNodesMixin
from .path_v2 import TreePathMixin
from .status import TreeStatusMixin

# Реэкспорт для обратной совместимости
__all__ = [
    "NodeType",
    "NodeStatus",
    "FileType",
    "NodeFile",
    "TreeNode",
    "StageType",
    "SectionType",
    "TreeClient",
    "ALLOWED_CHILDREN",
]


@dataclass
class TreeClient(
    TreeClientCore,
    TreeNodesMixin,
    TreeStatusMixin,
    TreeFilesMixin,
    TreeCategoriesMixin,
    TreePathMixin,
    TreeAnnotationsMixin,
):
    """
    Клиент для работы с деревом проектов.

    Композиция миксинов:
    - TreeClientCore: HTTP методы (_headers, _request, is_available)
    - TreeNodesMixin: CRUD узлов (get_node, create_node, update_node, delete_node)
    - TreeStatusMixin: PDF статусы (get_pdf_status, update_pdf_status, lock/unlock)
    - TreeFilesMixin: Файлы узлов (add_node_file, get_node_files, upsert_node_file)
    - TreeCategoriesMixin: Категории изображений
    - TreePathMixin: Операции с materialized path (get_descendants, get_ancestors)
    - TreeAnnotationsMixin: CRUD аннотаций (таблица annotations)
    """
    pass
