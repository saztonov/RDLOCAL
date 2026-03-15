"""Операции с папками документов"""
import logging
import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from app.tree_client import TreeNode

logger = logging.getLogger(__name__)


class TreeFolderOperationsMixin:
    """Миксин для операций с папками документов"""

    def _open_document_folder(self, node: TreeNode):
        """Открыть папку документа в проводнике (скачать с R2 если нет локально)"""
        from app.gui.folder_settings_dialog import get_projects_dir
        from rd_core.r2_storage import R2Storage

        r2_key = node.attributes.get("r2_key", "")
        if not r2_key:
            QMessageBox.warning(self, "Ошибка", "R2 ключ файла не найден")
            return

        projects_dir = get_projects_dir()
        if not projects_dir:
            QMessageBox.warning(self, "Ошибка", "Папка проектов не задана в настройках")
            return

        # Определяем локальную папку (parent от PDF файла)
        if r2_key.startswith("tree_docs/"):
            rel_path = r2_key[len("tree_docs/") :]
        else:
            rel_path = r2_key

        local_file = Path(projects_dir) / "cache" / rel_path
        local_folder = local_file.parent
        local_folder.mkdir(parents=True, exist_ok=True)

        # Скачиваем только PDF (без кропов)
        # Аннотация хранится в Supabase (таблица annotations), не в R2
        self.status_label.setText("Скачивание файлов с R2...")
        try:
            r2 = R2Storage()

            # Список файлов для скачивания: только PDF
            files_to_download = [
                (r2_key, local_file),  # PDF
            ]

            downloaded = 0
            for remote_key, local_path in files_to_download:
                if not local_path.exists():
                    if r2.exists(remote_key):
                        if r2.download_file(remote_key, str(local_path)):
                            downloaded += 1

            self.status_label.setText(f"Скачано файлов: {downloaded}")
            logger.info(f"Downloaded {downloaded} files for document: {r2_key}")

        except Exception as e:
            logger.error(f"Failed to download files from R2: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось скачать файлы:\n{e}")
            return

        # Открываем папку в проводнике
        try:
            if sys.platform == "win32":
                subprocess.run(["explorer", str(local_folder)], check=False)
            elif sys.platform == "darwin":
                subprocess.run(["open", str(local_folder)], check=False)
            else:
                subprocess.run(["xdg-open", str(local_folder)], check=False)

            self.status_label.setText(f"📂 {local_folder.name}")
        except Exception as e:
            logger.error(f"Failed to open folder: {e}")
            QMessageBox.warning(self, "Ошибка", f"Не удалось открыть папку:\n{e}")

    def _remove_stamps_from_document(self, node: TreeNode):
        """Удалить рамки и QR-коды из PDF документа (обработать и заменить оригинал)"""
        # Проверка блокировки документа
        if self._check_document_locked(node):
            return

        # Подтверждение от пользователя
        reply = QMessageBox.question(
            self,
            "Удаление рамок и QR",
            f"Удалить рамки и QR-коды из документа '{node.name}'?\n\n"
            "Оригинальный файл будет заменён очищенной версией.\n"
            "Существующие аннотации (обводки блоков) будут сохранены.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        import shutil

        from app.gui.folder_settings_dialog import get_projects_dir
        from rd_core.pdf_stamp_remover import remove_stamps_from_pdf
        from rd_core.r2_storage import R2Storage

        r2_key = node.attributes.get("r2_key", "")
        if not r2_key:
            QMessageBox.warning(self, "Ошибка", "R2 ключ файла не найден")
            return

        try:
            r2 = R2Storage()
        except Exception as e:
            QMessageBox.critical(
                self, "Ошибка R2", f"Не удалось подключиться к R2:\n{e}"
            )
            return

        projects_dir = get_projects_dir()
        if not projects_dir:
            QMessageBox.warning(self, "Ошибка", "Папка проектов не задана в настройках")
            return

        if r2_key.startswith("tree_docs/"):
            rel_path = r2_key[len("tree_docs/") :]
        else:
            rel_path = r2_key

        local_path = Path(projects_dir) / "cache" / rel_path
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Закрываем файл если открыт в редакторе
        self._close_if_open(r2_key)

        # Скачиваем если нет локально
        if not local_path.exists():
            if not r2.download_file(r2_key, str(local_path)):
                QMessageBox.critical(
                    self, "Ошибка", f"Не удалось скачать файл из R2:\n{r2_key}"
                )
                return

        # Обработка во временный файл (оригинал не трогаем до успешной загрузки в R2)
        output_path = local_path.parent / f"{local_path.stem}_clean{local_path.suffix}"
        success, result = remove_stamps_from_pdf(str(local_path), str(output_path))

        if not success:
            output_path.unlink(missing_ok=True)
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось обработать файл:\n{result}"
            )
            return

        try:
            # Загрузить очищенный PDF в R2 по тому же r2_key (перезапись оригинала)
            if not r2.upload_file(str(output_path), r2_key):
                output_path.unlink(missing_ok=True)
                QMessageBox.critical(
                    self, "Ошибка", "Не удалось загрузить обработанный файл в R2"
                )
                return

            # Заменить локальный файл очищенной версией
            shutil.move(str(output_path), str(local_path))

            # Обновить file_size в атрибутах узла
            new_size = local_path.stat().st_size
            attrs = node.attributes.copy()
            attrs["file_size"] = new_size
            self.client.update_node(node.id, attributes=attrs)
            node.attributes = attrs

            # Обновить file_size в node_files (запись типа PDF)
            try:
                from app.tree_client import FileType

                pdf_files = self.client.get_node_files(node.id, file_type=FileType.PDF)
                for nf in pdf_files:
                    if nf.r2_key == r2_key:
                        self.client.update_node_file(nf.id, file_size=new_size)
                        break
            except Exception as e:
                logger.warning(f"Failed to update node_file size: {e}")

            # Аннотации не трогаем — координаты остаются валидными
            # (геометрия страниц не меняется при удалении штампов)

            logger.info(f"Stamps removed from document {node.id}, r2_key={r2_key}")
            QMessageBox.information(
                self,
                "Готово",
                f"Рамки и QR-коды удалены из документа '{node.name}'.\n"
                f"Оригинальный файл обновлён.",
            )

        except Exception as e:
            logger.exception(f"Error replacing cleaned document: {e}")
            output_path.unlink(missing_ok=True)
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось заменить документ:\n{e}"
            )

    def _auto_markup_entire_file(self, node: TreeNode):
        """Создать текстовый блок на всю страницу для каждой страницы документа"""
        from PySide6.QtCore import QTimer

        from rd_core.models import Block, BlockSource, BlockType

        # Документ должен быть открыт
        if not self._current_node_id or self._current_node_id != node.id:
            QMessageBox.warning(
                self, "Ошибка", "Сначала откройте документ (кликните по нему в дереве)"
            )
            return

        if not self.pdf_document or not self.annotation_document:
            QMessageBox.warning(self, "Ошибка", "PDF документ не загружен")
            return

        # Проверка блокировки
        if self._check_document_locked(node):
            return

        # Предупреждение если уже есть блоки
        existing_blocks = sum(len(p.blocks) for p in self.annotation_document.pages)
        if existing_blocks > 0:
            reply = QMessageBox.question(
                self,
                "Авторазметка файла",
                f"В документе уже есть {existing_blocks} блок(ов).\n"
                "Добавить полностраничные блоки на все страницы?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self._save_undo_state()

        page_count = self.pdf_document.page_count
        for page_idx in range(page_count):
            page = self._get_or_create_page(page_idx)
            if not page:
                continue

            block = Block.create(
                page_index=page_idx,
                coords_px=(0, 0, page.width, page.height),
                page_width=page.width,
                page_height=page.height,
                block_type=BlockType.TEXT,
                source=BlockSource.USER,
            )
            page.blocks.append(block)

        # Обновить UI для текущей страницы
        current_page = self.annotation_document.pages[self.current_page]
        self.page_viewer.set_blocks(current_page.blocks)
        QTimer.singleShot(0, self.blocks_tree_manager.update_blocks_tree)
        self._auto_save_annotation()

        logger.info(f"Auto-markup: created {page_count} full-page blocks for '{node.name}'")
