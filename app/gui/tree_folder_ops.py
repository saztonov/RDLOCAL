"""Операции с папками документов"""
import logging
import shutil
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from app.tree_client import TreeNode

logger = logging.getLogger(__name__)


class TreeFolderOperationsMixin:
    """Миксин для операций с документами (удаление штампов, авторазметка)"""

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

        # Работаем в одноразовой temp-папке
        work_dir = Path(tempfile.mkdtemp(prefix="rd_stamps_"))
        local_path = work_dir / Path(r2_key).name

        # Закрываем файл если открыт в редакторе
        self._close_if_open(r2_key)

        try:
            # Скачиваем PDF
            if not r2.download_file(r2_key, str(local_path), use_cache=False):
                QMessageBox.critical(
                    self, "Ошибка", f"Не удалось скачать файл из R2:\n{r2_key}"
                )
                return

            # Обработка во временный файл
            output_path = work_dir / f"{local_path.stem}_clean{local_path.suffix}"
            success, result = remove_stamps_from_pdf(str(local_path), str(output_path))

            if not success:
                QMessageBox.critical(
                    self, "Ошибка", f"Не удалось обработать файл:\n{result}"
                )
                return

            # Загрузить очищенный PDF в R2 по тому же r2_key (перезапись оригинала)
            if not r2.upload_file(str(output_path), r2_key):
                QMessageBox.critical(
                    self, "Ошибка", "Не удалось загрузить обработанный файл в R2"
                )
                return

            # Обновить file_size в атрибутах узла
            new_size = output_path.stat().st_size
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

            logger.info(f"Stamps removed from document {node.id}, r2_key={r2_key}")
            QMessageBox.information(
                self,
                "Готово",
                f"Рамки и QR-коды удалены из документа '{node.name}'.\n"
                f"Оригинальный файл обновлён.",
            )

        except Exception as e:
            logger.exception(f"Error replacing cleaned document: {e}")
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось заменить документ:\n{e}"
            )
        finally:
            # Всегда удаляем temp-папку
            shutil.rmtree(work_dir, ignore_errors=True)

    def _auto_markup_entire_file(self, node: TreeNode):
        """Делегировать авторазметку на MainWindow"""
        main_window = self.window()
        if not hasattr(main_window, "_do_auto_markup"):
            QMessageBox.warning(self, "Ошибка", "Функция авторазметки недоступна")
            return
        main_window._do_auto_markup(node)
