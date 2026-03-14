"""Модели данных для дерева проектов"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class NodeType(str, Enum):
    """Тип узла дерева (v2: folder/document вместо 5 фиксированных типов)"""

    FOLDER = "folder"
    DOCUMENT = "document"

    # Legacy aliases для обратной совместимости
    PROJECT = "folder"
    STAGE = "folder"
    SECTION = "folder"
    TASK_FOLDER = "folder"

    @classmethod
    def from_value(cls, value: str) -> "NodeType":
        """Конвертирует legacy значения в новые типы."""
        legacy_map = {
            "project": cls.FOLDER,
            "stage": cls.FOLDER,
            "section": cls.FOLDER,
            "task_folder": cls.FOLDER,
            "document": cls.DOCUMENT,
            "folder": cls.FOLDER,
        }
        return legacy_map.get(value, cls.FOLDER)


class NodeStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


# Определяем какие дочерние типы могут быть у родительского
# V2: Произвольная вложенность - folder может содержать folder или document
ALLOWED_CHILDREN: Dict[Optional[NodeType], List[NodeType]] = {
    None: [NodeType.FOLDER],  # Корневые узлы - только папки
    NodeType.FOLDER: [NodeType.FOLDER, NodeType.DOCUMENT],  # Папки могут содержать папки и документы
    NodeType.DOCUMENT: [],  # Документы - листовые узлы
}


class FileType(str, Enum):
    PDF = "pdf"
    ANNOTATION = "annotation"
    CROP = "crop"
    IMAGE = "image"
    OCR_HTML = "ocr_html"
    RESULT_JSON = "result_json"
    RESULT_MD = "result_md"
    RESULT_ZIP = "result_zip"
    CROPS_FOLDER = "crops_folder"
    BLOCKS_INDEX = "blocks_index"


@dataclass
class NodeFile:
    """Файл привязанный к узлу дерева"""

    id: str
    node_id: str
    file_type: FileType
    r2_key: str
    file_name: str
    file_size: int = 0
    mime_type: str = "application/octet-stream"
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_dict(cls, data: dict) -> "NodeFile":
        # Безопасное преобразование file_type
        raw_file_type = data["file_type"]
        try:
            # Нормализуем значение: убираем пробелы и приводим к нижнему регистру
            normalized_type = (
                raw_file_type.strip().lower()
                if isinstance(raw_file_type, str)
                else raw_file_type
            )
            file_type = FileType(normalized_type)
        except ValueError as e:
            # Если значение не валидно, логируем предупреждение и используем fallback
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                f"Invalid file_type '{raw_file_type}' for node_file {data.get('id')}, using 'pdf' as fallback: {e}"
            )
            file_type = FileType.PDF

        return cls(
            id=data["id"],
            node_id=data["node_id"],
            file_type=file_type,
            r2_key=data["r2_key"],
            file_name=data["file_name"],
            file_size=data.get("file_size", 0),
            mime_type=data.get("mime_type", "application/octet-stream"),
            metadata=data.get("metadata") or {},
            created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
            if data.get("created_at")
            else None,
            updated_at=datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
            if data.get("updated_at")
            else None,
        )


@dataclass
class TreeNode:
    """Узел дерева проектов"""

    id: str
    parent_id: Optional[str]
    node_type: NodeType
    name: str
    code: Optional[str] = None
    version: int = 1
    status: NodeStatus = NodeStatus.ACTIVE
    attributes: Dict[str, Any] = field(default_factory=dict)
    sort_order: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    children: List["TreeNode"] = field(default_factory=list)
    pdf_status: Optional[str] = None
    pdf_status_message: Optional[str] = None
    is_locked: bool = False

    # Новые поля v2 для оптимизации
    path: Optional[str] = None  # Materialized path: uuid1.uuid2.uuid3
    depth: int = 0  # Глубина от корня (0 = корневой)
    children_count: int = 0  # Количество прямых дочерних
    descendants_count: int = 0  # Количество всех потомков
    files_count: int = 0  # Количество файлов в node_files

    @property
    def legacy_node_type(self) -> Optional[str]:
        """Получить legacy тип узла из attributes (для обратной совместимости)."""
        return self.attributes.get("legacy_node_type")

    @property
    def is_folder(self) -> bool:
        """Проверить является ли узел папкой."""
        return self.node_type == NodeType.FOLDER

    @property
    def is_document(self) -> bool:
        """Проверить является ли узел документом."""
        return self.node_type == NodeType.DOCUMENT

    @classmethod
    def from_dict(cls, data: dict) -> "TreeNode":
        # Конвертируем legacy node_type в новый формат
        raw_node_type = data["node_type"]
        node_type = NodeType.from_value(raw_node_type)

        return cls(
            id=data["id"],
            parent_id=data.get("parent_id"),
            node_type=node_type,
            name=data["name"],
            code=data.get("code"),
            version=data.get("version", 1),
            status=NodeStatus(data.get("status", "active")),
            attributes=data.get("attributes") or {},
            sort_order=data.get("sort_order", 0),
            created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
            if data.get("created_at")
            else None,
            updated_at=datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
            if data.get("updated_at")
            else None,
            pdf_status=data.get("pdf_status"),
            pdf_status_message=data.get("pdf_status_message"),
            is_locked=data.get("is_locked", False),
            # Новые поля v2
            path=data.get("path"),
            depth=data.get("depth", 0),
            children_count=data.get("children_count", 0),
            descendants_count=data.get("descendants_count", 0),
            files_count=data.get("files_count", 0),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "node_type": self.node_type.value,
            "name": self.name,
            "code": self.code,
            "version": self.version,
            "status": self.status.value,
            "attributes": self.attributes,
            "sort_order": self.sort_order,
            # Новые поля v2 (только для чтения, не отправляем при создании)
            "path": self.path,
            "depth": self.depth,
            "children_count": self.children_count,
            "descendants_count": self.descendants_count,
            "files_count": self.files_count,
        }

    def get_allowed_child_types(self) -> List[NodeType]:
        return ALLOWED_CHILDREN.get(self.node_type, [])
