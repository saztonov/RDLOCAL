"""
DB-операции для аннотаций (Supabase).

Вынесено из rd_core/annotation_io.py для соблюдения архитектурных границ:
rd_core не должен зависеть от app.tree_client.
"""

import logging
from typing import Optional

from rd_core.annotation_io import (
    ANNOTATION_FORMAT_VERSION,
    AnnotationIO,
    is_flat_format,
    migrate_annotation_data,
    migrate_flat_to_structured,
)
from rd_core.models import Document

logger = logging.getLogger(__name__)


class AnnotationDBIO:
    """Класс для работы с аннотациями в Supabase (таблица annotations)."""

    @staticmethod
    def save_to_db(document: Document, node_id: str) -> bool:
        """
        Сохранить аннотацию в Supabase (таблица annotations).

        Args:
            document: экземпляр Document
            node_id: ID узла в tree_nodes

        Returns:
            True при успехе
        """
        try:
            from app.tree_client import TreeClient

            data = document.to_dict()
            data["format_version"] = ANNOTATION_FORMAT_VERSION

            client = TreeClient()
            success = client.save_annotation(node_id, data, ANNOTATION_FORMAT_VERSION)
            if success:
                logger.info(f"Аннотация сохранена в БД: node_id={node_id}")
            return success
        except Exception as e:
            logger.error(f"Ошибка сохранения аннотации в БД: {e}")
            return False

    @staticmethod
    def load_from_db(node_id: str) -> Optional[Document]:
        """
        Загрузить аннотацию из Supabase (таблица annotations).

        Args:
            node_id: ID узла в tree_nodes

        Returns:
            Document или None
        """
        try:
            from app.tree_client import TreeClient

            client = TreeClient()
            data = client.get_annotation(node_id)

            if data is None:
                return None

            # Проверка на плоский формат v0 (legacy)
            format_migrated = False
            if is_flat_format(data):
                data = migrate_flat_to_structured(data)
                format_migrated = True

            # Миграция v1 → v2
            migrated_data, result = migrate_annotation_data(data)
            if not result.success:
                logger.error(f"Миграция аннотации из БД не удалась: {result.errors}")
                return None

            format_migrated = format_migrated or result.migrated
            doc, ids_migrated = Document.from_dict(migrated_data, migrate_ids=True)
            setattr(doc, "_prefer_coords_px", format_migrated)

            # Безопасно автосохраняем только миграцию ID.
            if ids_migrated and not format_migrated:
                AnnotationDBIO.save_to_db(doc, node_id)
                logger.info(
                    f"Аннотация пересохранена после миграции ID: node_id={node_id}"
                )

            logger.info(f"Аннотация загружена из БД: node_id={node_id}")
            return doc
        except Exception as e:
            logger.error(f"Ошибка загрузки аннотации из БД: {e}")
            return None

    @staticmethod
    def migrate_file_to_db(file_path: str, node_id: str) -> bool:
        """
        Мигрировать аннотацию из JSON файла в Supabase и удалить файл.

        Args:
            file_path: путь к JSON-файлу
            node_id: ID узла в tree_nodes

        Returns:
            True при успехе
        """
        from pathlib import Path

        try:
            # Загрузить из файла
            loaded_doc, result = AnnotationIO.load_and_migrate(file_path)
            if not result.success or not loaded_doc:
                logger.error(f"Не удалось загрузить файл для миграции: {result.errors}")
                return False

            # Сохранить в БД
            success = AnnotationDBIO.save_to_db(loaded_doc, node_id)
            if not success:
                logger.error(f"Не удалось сохранить аннотацию в БД при миграции")
                return False

            # Удалить JSON файл
            try:
                Path(file_path).unlink()
                logger.info(f"JSON файл удалён после миграции: {file_path}")
            except Exception as e:
                logger.warning(f"Не удалось удалить JSON файл: {e}")

            logger.info(f"Аннотация мигрирована из файла в БД: {file_path} → {node_id}")
            return True
        except Exception as e:
            logger.error(f"Ошибка миграции аннотации: {e}")
            return False
