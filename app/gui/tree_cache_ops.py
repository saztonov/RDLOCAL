"""Операции удаления файлов дерева проектов (R2 + БД)"""
import logging

from app.tree_client import NodeType, TreeNode

logger = logging.getLogger(__name__)


class TreeCacheOperationsMixin:
    """Миксин для операций удаления файлов в R2/БД"""

    def _delete_branch_files(self, node: TreeNode):
        """Рекурсивно удалить все файлы документов в ветке из R2"""
        # Сначала рекурсивно обрабатываем дочерние узлы
        try:
            children = self.client.get_children(node.id)
            for child in children:
                self._delete_branch_files(child)
        except Exception as e:
            logger.error(f"Failed to get children for deletion: {e}")

        # Если это документ - удаляем его файлы
        if node.node_type == NodeType.DOCUMENT:
            self._delete_document_files(node)

        # Если это task_folder - удаляем всю папку из R2
        if node.node_type == NodeType.TASK_FOLDER:
            try:
                from rd_core.r2_storage import R2Storage

                r2 = R2Storage()
                r2_prefix = f"tree_docs/{node.id}/"
                deleted = r2.delete_by_prefix(r2_prefix)
                if deleted:
                    logger.info(f"Deleted {deleted} files from R2 folder: {r2_prefix}")
            except Exception as e:
                logger.error(f"Failed to delete R2 folder: {e}")

    def _delete_document_files(self, node: TreeNode):
        """Удалить файлы документа из R2 и БД"""
        from pathlib import PurePosixPath, Path

        from rd_core.r2_storage import R2Storage

        r2_key = node.attributes.get("r2_key", "")

        # Закрываем файл если он открыт в редакторе
        self._close_if_open(r2_key)

        # Удаляем все файлы из node_files из R2
        if node.id:
            try:
                r2 = R2Storage()
                node_files = self.client.get_node_files(node.id)

                # Собираем все ключи для пакетного удаления
                r2_keys_to_delete = []
                for nf in node_files:
                    if nf.r2_key:
                        r2_keys_to_delete.append(nf.r2_key)

                # Пакетное удаление из R2
                if r2_keys_to_delete:
                    deleted_keys, errors = r2.delete_objects_batch(r2_keys_to_delete)
                    logger.info(f"Deleted {len(deleted_keys)} node_files from R2")
                    if errors:
                        logger.warning(f"Failed to delete {len(errors)} files from R2")

                # Удаляем записи из БД
                for nf in node_files:
                    self.client.delete_node_file(nf.id)
                    logger.info(f"Deleted node_file from DB: {nf.id}")

            except Exception as e:
                logger.error(f"Failed to delete node_files: {e}")

        # Удаляем из R2: PDF, OCR HTML и crops
        if r2_key:
            try:
                r2 = R2Storage()
                r2_prefix = str(PurePosixPath(r2_key).parent)
                pdf_stem = Path(r2_key).stem

                files_to_delete = [
                    r2_key,  # PDF
                    f"{r2_prefix}/{pdf_stem}_ocr.html",  # OCR HTML
                ]

                # Удаляем папку crops по префиксу
                crops_prefix = f"{r2_prefix}/crops/"
                deleted_crops = r2.delete_by_prefix(crops_prefix)
                if deleted_crops:
                    logger.info(f"Deleted {deleted_crops} crops from R2")

                # Пакетное удаление основных файлов
                deleted_keys, errors = r2.delete_objects_batch(files_to_delete)
                logger.info(f"Deleted {len(deleted_keys)} files from R2: {r2_key}")
                if errors:
                    logger.warning(f"Failed to delete {len(errors)} files from R2")

            except Exception as e:
                logger.error(f"Failed to delete from R2: {e}")
