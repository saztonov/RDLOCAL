"""
Application service layer — facade над инфраструктурой (R2, Supabase, Annotations).

GUI модули должны использовать эти функции вместо прямого создания
R2Storage(), TreeClient(), AnnotationDBIO(). Это позволяет:
- Тестировать GUI без реальных R2/Supabase (подмена через mock)
- Единообразный API для всех GUI операций
- Централизованное логирование и error handling

Миграция: GUI файлы постепенно переходят на `from app.services import ...`
вместо `from rd_core.r2_storage import R2Storage; r2 = R2Storage()`.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Artifact Store (R2 Storage facade)
# ═══════════════════════════════════════════════════════════════════


def get_r2() -> "R2Storage":
    """Получить singleton R2Storage.

    Использовать вместо `R2Storage()` в GUI коде.
    """
    from rd_core.r2_storage import R2Storage

    return R2Storage()


def upload_file(local_path: str, r2_key: str, content_type: str | None = None) -> bool:
    """Загрузить файл в R2."""
    return get_r2().upload_file(local_path, r2_key, content_type)


def download_file(r2_key: str, local_path: str, use_cache: bool = True) -> bool:
    """Скачать файл из R2."""
    return get_r2().download_file(r2_key, local_path, use_cache=use_cache)


def file_exists(r2_key: str, use_cache: bool = True) -> bool:
    """Проверить существование файла в R2."""
    return get_r2().exists(r2_key, use_cache=use_cache)


def delete_file(r2_key: str) -> bool:
    """Удалить файл из R2."""
    return get_r2().delete_object(r2_key)


def list_files(prefix: str) -> list[str]:
    """Получить список файлов по префиксу."""
    return get_r2().list_files(prefix)


# ═══════════════════════════════════════════════════════════════════
# Tree Repository (Supabase TreeClient facade)
# ═══════════════════════════════════════════════════════════════════


def get_tree_client() -> "TreeClient":
    """Получить singleton TreeClient.

    Использовать вместо `TreeClient()` в GUI коде.
    """
    from app.tree_client import TreeClient

    return TreeClient()


def get_node_files(node_id: str) -> list:
    """Получить файлы узла дерева."""
    return get_tree_client().get_node_files(node_id)


def add_node_file(node_id: str, file_type: str, r2_key: str, **kwargs) -> Optional[str]:
    """Добавить файл к узлу дерева."""
    return get_tree_client().add_node_file(node_id, file_type, r2_key, **kwargs)


def upsert_node_file(node_id: str, file_type: str, r2_key: str, **kwargs) -> Optional[str]:
    """Добавить или обновить файл узла дерева (upsert по node_id + r2_key)."""
    return get_tree_client().upsert_node_file(node_id, file_type, r2_key, **kwargs)


def delete_node_file(file_id: str) -> bool:
    """Удалить файл узла."""
    return get_tree_client().delete_node_file(file_id)


# ═══════════════════════════════════════════════════════════════════
# Annotation Repository (AnnotationDBIO facade)
# ═══════════════════════════════════════════════════════════════════


def save_annotation_to_db(document: object, node_id: str) -> bool:
    """Сохранить аннотацию в Supabase."""
    from app.annotation_db import AnnotationDBIO

    return AnnotationDBIO.save_to_db(document, node_id)


def load_annotation_from_db(node_id: str) -> Optional[object]:
    """Загрузить аннотацию из Supabase."""
    from app.annotation_db import AnnotationDBIO

    return AnnotationDBIO.load_from_db(node_id)
