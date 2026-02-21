"""Миксин для разделения PDF документов в дереве проектов."""
import json
import logging
import shutil
from pathlib import Path

import fitz
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.tree_client import TreeNode
from app.tree_models import FileType

logger = logging.getLogger(__name__)


class TreeSplitMixin:
    """Миксин для операции разделения PDF документа на части."""

    def _split_document(self, node: TreeNode):
        """
        Разделить PDF документ на N частей.

        Поток:
        1. Проверки (блокировка, r2_key, количество страниц)
        2. Диалог SplitDocumentDialog
        3. Разделение PDF + аннотации
        4. Загрузка частей в R2, создание узлов в дереве
        """
        # 1. Проверки
        if self._check_document_locked(node):
            return

        r2_key = node.attributes.get("r2_key", "")
        if not r2_key or not r2_key.lower().endswith(".pdf"):
            QMessageBox.warning(
                self, "Ошибка", "Файл не является PDF документом"
            )
            return

        # Найти родительский узел
        item = self._node_map.get(node.id)
        if not item:
            return
        parent_item = item.parent()
        if not parent_item:
            QMessageBox.warning(
                self, "Ошибка", "Не найден родительский узел"
            )
            return
        parent_node = parent_item.data(0, Qt.UserRole)
        if not isinstance(parent_node, TreeNode):
            QMessageBox.warning(
                self, "Ошибка", "Не найден родительский узел"
            )
            return

        # 2. Скачивание PDF из R2
        from app.gui.folder_settings_dialog import get_projects_dir
        from rd_core.r2_storage import R2Storage

        projects_dir = get_projects_dir()
        if not projects_dir:
            QMessageBox.warning(
                self, "Ошибка", "Папка проектов не задана в настройках"
            )
            return

        if r2_key.startswith("tree_docs/"):
            rel_path = r2_key[len("tree_docs/"):]
        else:
            rel_path = r2_key

        local_path = Path(projects_dir) / "cache" / rel_path
        local_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            r2 = R2Storage()
        except Exception as e:
            QMessageBox.critical(
                self, "Ошибка R2", f"Не удалось подключиться к R2:\n{e}"
            )
            return

        if not local_path.exists():
            self.status_label.setText("Скачивание PDF из R2...")
            QApplication.processEvents()
            if not r2.download_file(r2_key, str(local_path)):
                QMessageBox.critical(
                    self, "Ошибка",
                    f"Не удалось скачать файл из R2:\n{r2_key}",
                )
                return

        # 3. Определение количества страниц
        try:
            doc = fitz.open(str(local_path))
            page_count = len(doc)
            doc.close()
        except Exception as e:
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось открыть PDF:\n{e}"
            )
            return

        if page_count < 2:
            QMessageBox.warning(
                self,
                "Невозможно разделить",
                "Документ содержит только 1 страницу и не может быть разделён.",
            )
            return

        # 4. Показ диалога
        from app.gui.split_document_dialog import SplitDocumentDialog

        dialog = SplitDocumentDialog(node.name, page_count, self)
        if dialog.exec() != QDialog.Accepted:
            return

        num_parts = dialog.num_parts

        # 5. Разделение PDF
        from rd_core.pdf_split import split_pdf

        output_dir = local_path.parent / f"{local_path.stem}_split"

        self.status_label.setText("Разделение PDF...")
        QApplication.processEvents()

        success, parts, error = split_pdf(
            str(local_path), str(output_dir), num_parts
        )
        if not success:
            QMessageBox.critical(
                self, "Ошибка разделения", f"Не удалось разделить PDF:\n{error}"
            )
            return

        # 6. Разделение аннотации (если есть)
        ann_results = None
        all_broken_links = []
        all_broken_groups = []

        try:
            ann_results = self._split_annotation_if_exists(
                node.id, local_path, r2, parts
            )
            if ann_results:
                for res in ann_results:
                    all_broken_links.extend(res.broken_links)
                    all_broken_groups.extend(res.broken_groups)
        except Exception as e:
            logger.warning(f"Ошибка разделения аннотации: {e}")

        # 7. Загрузка в R2 + создание узлов
        try:
            created_nodes = self._upload_split_parts(
                parts, ann_results, node, parent_node, r2
            )
        except Exception as e:
            logger.exception(f"Ошибка загрузки частей: {e}")
            QMessageBox.critical(
                self, "Ошибка",
                f"Ошибка при загрузке частей в R2:\n{e}",
            )
            return
        finally:
            # Очистка временных файлов
            shutil.rmtree(str(output_dir), ignore_errors=True)

        # 8. Результат
        if all_broken_links or all_broken_groups:
            warnings = []
            if all_broken_links:
                warnings.append(
                    f"Разорвано связей между блоками: {len(all_broken_links)}"
                )
            if all_broken_groups:
                unique_groups = list(set(all_broken_groups))
                warnings.append(
                    f"Групп, разделённых между частями: {len(unique_groups)}"
                )
            QMessageBox.warning(
                self,
                "Разделение завершено с предупреждениями",
                f"Документ разделён на {num_parts} частей.\n\n"
                + "\n".join(warnings),
            )
        else:
            QMessageBox.information(
                self,
                "Готово",
                f"Документ разделён на {num_parts} частей.",
            )

        self.status_label.setText(
            f"Разделено на {num_parts} частей: {node.name}"
        )

    def _split_annotation_if_exists(self, node_id, local_path, r2, parts):
        """Загрузить и разделить аннотацию, если она существует."""
        from rd_core.annotation_io import AnnotationIO
        from rd_core.annotation_split import split_annotation

        # Загружаем аннотацию из Supabase (привязана к node_id)
        source_doc = None
        if node_id:
            source_doc = AnnotationIO.load_from_db(node_id)

        if not source_doc or not source_doc.pages:
            return None

        # Проверяем что есть хотя бы один блок
        has_blocks = any(len(p.blocks) > 0 for p in source_doc.pages)
        if not has_blocks:
            return None

        page_ranges = [part.page_range for part in parts]
        part_pdf_paths = [part.file_path for part in parts]

        return split_annotation(source_doc, page_ranges, part_pdf_paths)

    def _upload_split_parts(
        self, parts, ann_results, node, parent_node, r2
    ):
        """Загрузить части в R2 и создать узлы в дереве."""
        created_nodes = []
        num_parts = len(parts)

        for i, part in enumerate(parts):
            self.status_label.setText(
                f"Загрузка части {i + 1} из {num_parts}..."
            )
            QApplication.processEvents()

            # Имя части
            part_name = f"Часть {i + 1}. {node.name}"
            if not part_name.lower().endswith(".pdf"):
                part_name += ".pdf"

            # Проверка уникальности имени
            final_name = part_name
            suffix = 1
            while not self._check_name_unique(parent_node.id, final_name):
                stem = part_name[:-4] if part_name.lower().endswith(".pdf") else part_name
                final_name = f"{stem}_{suffix}.pdf"
                suffix += 1

            new_r2_key = f"tree_docs/{parent_node.id}/{final_name}"

            # Загрузка PDF в R2
            if not r2.upload_file(str(part.file_path), new_r2_key):
                raise RuntimeError(
                    f"Не удалось загрузить часть {i + 1} в R2"
                )

            # Создание узла в дереве (Supabase)
            doc_node = self.client.add_document(
                parent_id=parent_node.id,
                name=final_name,
                r2_key=new_r2_key,
                file_size=part.file_size,
            )

            # Загрузка аннотации (если есть)
            if ann_results and ann_results[i].document:
                try:
                    self._upload_split_annotation(
                        ann_results[i], doc_node, new_r2_key, r2
                    )
                except Exception as e:
                    logger.warning(
                        f"Ошибка загрузки аннотации для части {i + 1}: {e}"
                    )

            # Добавление в дерево UI
            # Re-fetch parent_item каждую итерацию, т.к. processEvents()
            # может вызвать перестроение дерева и удаление старых QTreeWidgetItem
            current_parent_item = self._node_map.get(parent_node.id)
            if current_parent_item:
                child_item = self._item_builder.create_item(doc_node)
                current_parent_item.addChild(child_item)
                current_parent_item.setExpanded(True)

            created_nodes.append(doc_node)
            logger.info(
                f"Часть {i + 1}/{num_parts} создана: "
                f"{doc_node.id}, r2_key={new_r2_key}"
            )

        return created_nodes

    def _upload_split_annotation(self, ann_result, doc_node, pdf_r2_key, r2):
        """Сохранить аннотацию для одной части в Supabase."""
        from rd_core.annotation_io import AnnotationIO

        success = AnnotationIO.save_to_db(ann_result.document, doc_node.id)
        if not success:
            raise RuntimeError("Не удалось сохранить аннотацию в Supabase")

        # Обновляем has_annotation в attributes
        attrs = doc_node.attributes.copy() if doc_node.attributes else {}
        attrs["has_annotation"] = True
        self.client.update_node(doc_node.id, attributes=attrs)
