"""Операции с локальным кэшем файлов дерева проектов"""
import logging
import shutil
from pathlib import Path

from app.tree_client import NodeType, TreeNode

logger = logging.getLogger(__name__)


class TreeCacheOperationsMixin:
    """Миксин для операций с локальным кэшем"""

    def _copy_to_cache(self, src_path: str, r2_key: str):
        """Скопировать загружаемый файл в локальный кэш"""
        from app.gui.folder_settings_dialog import get_projects_dir

        projects_dir = get_projects_dir()
        if not projects_dir:
            return

        if r2_key.startswith("tree_docs/"):
            rel_path = r2_key[len("tree_docs/") :]
        else:
            rel_path = r2_key

        cache_path = Path(projects_dir) / "cache" / rel_path
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(src_path, cache_path)
            logger.debug(f"Copied to cache: {cache_path}")
        except Exception as e:
            logger.error(f"Failed to copy to cache: {e}")

    def _rename_cache_file(self, old_r2_key: str, new_r2_key: str):
        """Переименовать файл в локальном кэше"""
        from app.gui.folder_settings_dialog import get_projects_dir

        projects_dir = get_projects_dir()
        if not projects_dir:
            return

        # Формируем пути
        def get_cache_path(r2_key: str) -> Path:
            if r2_key.startswith("tree_docs/"):
                rel_path = r2_key[len("tree_docs/") :]
            else:
                rel_path = r2_key
            return Path(projects_dir) / "cache" / rel_path

        old_cache = get_cache_path(old_r2_key)
        new_cache = get_cache_path(new_r2_key)

        if old_cache.exists():
            try:
                new_cache.parent.mkdir(parents=True, exist_ok=True)
                old_cache.rename(new_cache)
                logger.info(f"Renamed in cache: {old_cache} -> {new_cache}")
            except Exception as e:
                logger.error(f"Failed to rename in cache: {e}")

    def _delete_branch_files(self, node: TreeNode):
        """Рекурсивно удалить все файлы документов в ветке из R2 и кэша"""
        from app.gui.folder_settings_dialog import get_projects_dir

        # Сначала рекурсивно обрабатываем дочерние узлы (чтобы закрыть файлы)
        try:
            children = self.client.get_children(node.id)
            for child in children:
                self._delete_branch_files(child)
        except Exception as e:
            logger.error(f"Failed to get children for deletion: {e}")

        # Если это документ - удаляем его файлы
        if node.node_type == NodeType.DOCUMENT:
            self._delete_document_files(node)

        # Если это task_folder - удаляем всю папку из R2 и кэша
        if node.node_type == NodeType.TASK_FOLDER:
            # Удаляем папку из R2
            try:
                from rd_core.r2_storage import R2Storage

                r2 = R2Storage()
                r2_prefix = f"tree_docs/{node.id}/"
                deleted = r2.delete_by_prefix(r2_prefix)
                if deleted:
                    logger.info(f"Deleted {deleted} files from R2 folder: {r2_prefix}")
            except Exception as e:
                logger.error(f"Failed to delete R2 folder: {e}")

            # Удаляем папку из локального кэша
            projects_dir = get_projects_dir()
            if projects_dir:
                cache_folder = Path(projects_dir) / "cache" / node.id
                if cache_folder.exists():
                    try:
                        shutil.rmtree(cache_folder)
                        logger.info(f"Deleted cache folder: {cache_folder}")
                    except Exception as e:
                        logger.error(f"Failed to delete cache folder: {e}")

    def _delete_document_files(self, node: TreeNode):
        """Удалить файлы документа из R2, локального кэша и БД"""
        from pathlib import PurePosixPath

        from app.gui.folder_settings_dialog import get_projects_dir
        from rd_core.r2_storage import R2Storage

        r2_key = node.attributes.get("r2_key", "")

        # Закрываем файл если он открыт в редакторе
        self._close_if_open(r2_key)

        projects_dir = get_projects_dir()

        # Сначала удаляем все файлы из node_files из R2
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

                    # Удаляем из локального кэша
                    if projects_dir:
                        for r2_key in deleted_keys:
                            if r2_key.startswith("tree_docs/"):
                                rel = r2_key[len("tree_docs/") :]
                            else:
                                rel = r2_key
                            cache_path = Path(projects_dir) / "cache" / rel
                            if cache_path.exists():
                                cache_path.unlink()
                                logger.debug(f"Deleted from cache: {cache_path}")

                # Удаляем записи из БД
                for nf in node_files:
                    self.client.delete_node_file(nf.id)
                    logger.info(f"Deleted node_file from DB: {nf.id}")

            except Exception as e:
                logger.error(f"Failed to delete node_files: {e}")

        # Удаляем из R2: PDF, аннотацию и папку crops
        if r2_key:
            try:
                r2 = R2Storage()
                r2_prefix = str(PurePosixPath(r2_key).parent)
                pdf_stem = Path(r2_key).stem

                # Собираем все файлы для пакетного удаления
                # Аннотация хранится в Supabase (таблица annotations), удаляется каскадно
                files_to_delete = [
                    r2_key,  # PDF
                    f"{r2_prefix}/{pdf_stem}_ocr.html",  # OCR HTML
                    f"{r2_prefix}/{pdf_stem}_result.json",  # result JSON
                ]

                # Удаляем папку crops по префиксу (кропы лежат как {node_id}/crops/{block_id}.pdf)
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

        # Удаляем из локального кэша
        if projects_dir and r2_key:
            if r2_key.startswith("tree_docs/"):
                rel_path = r2_key[len("tree_docs/") :]
            else:
                rel_path = r2_key

            cache_file = Path(projects_dir) / "cache" / rel_path
            pdf_stem = cache_file.stem

            # Удаляем PDF
            if cache_file.exists():
                try:
                    cache_file.unlink()
                    logger.info(f"Deleted from cache: {cache_file}")
                except Exception as e:
                    logger.error(f"Failed to delete from cache: {e}")

            # Удаляем _ocr.html
            ocr_html_file = cache_file.parent / f"{pdf_stem}_ocr.html"
            if ocr_html_file.exists():
                try:
                    ocr_html_file.unlink()
                    logger.info(f"Deleted OCR HTML from cache: {ocr_html_file}")
                except Exception as e:
                    logger.error(f"Failed to delete OCR HTML from cache: {e}")

            # Удаляем _result.json
            result_json_file = cache_file.parent / f"{pdf_stem}_result.json"
            if result_json_file.exists():
                try:
                    result_json_file.unlink()
                    logger.info(f"Deleted result.json from cache: {result_json_file}")
                except Exception as e:
                    logger.error(f"Failed to delete result.json from cache: {e}")

            # Удаляем папку crops (кропы лежат в {node_id}/crops/)
            crops_folder = cache_file.parent / "crops"
            if crops_folder.exists():
                try:
                    shutil.rmtree(crops_folder, ignore_errors=True)
                    logger.info(f"Deleted crops folder from cache: {crops_folder}")
                except Exception as e:
                    logger.error(f"Failed to delete crops folder: {e}")

            # Удаляем пустую родительскую папку
            if cache_file.parent.exists() and not any(cache_file.parent.iterdir()):
                try:
                    cache_file.parent.rmdir()
                except Exception as e:
                    logger.error(f"Failed to delete empty cache folder: {e}")
