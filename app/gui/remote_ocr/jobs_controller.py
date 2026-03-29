"""Контроллер бизнес-логики OCR задач (локальный режим).

Чистый QObject, владеющий состоянием и фоновыми операциями.
Не зависит от UI-виджетов напрямую — общается через Qt-сигналы.

Использует LocalOcrRunner (multiprocessing) вместо HTTP+Celery+Redis.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, QTimer, Signal

if TYPE_CHECKING:
    from app.gui.main_window import MainWindow

logger = logging.getLogger(__name__)


class JobsController(QObject):
    """Контроллер OCR задач (локальный режим).

    Владеет:
      - LocalOcrRunner (multiprocessing) для выполнения OCR
      - Кэшем задач для UI
      - Result application (merge ocr_text в annotation)
    """

    # ── Сигналы (для UI) ──────────────────────────────────────────────

    jobs_updated = Signal(list)                # полный список задач
    connection_status = Signal(str)            # "connected" / "disconnected" / "loading"
    job_uploading = Signal(object)             # temp job (status="uploading")
    job_created = Signal(object)               # real job
    job_create_error = Signal(str, str)        # error_type, message
    download_started = Signal(str, int)        # job_id, total_files
    download_progress = Signal(str, int, str)  # job_id, current, filename
    download_finished = Signal(str, str)       # job_id, extract_dir
    download_error = Signal(str, str)          # job_id, error

    def __init__(self, main_window: MainWindow, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.main_window = main_window

        # OCR runner
        from app.ocr.local_runner import LocalOcrRunner

        self._runner = LocalOcrRunner(parent=self)
        self._runner.job_created.connect(self._on_runner_job_created)
        self._runner.job_updated.connect(self._on_runner_job_updated)
        self._runner.job_finished.connect(self._on_runner_job_finished)

        # State
        self._panel_visible: bool = False
        self._last_output_dir: Optional[str] = None
        self._last_engine: Optional[str] = None
        self._pending_output_dir: Optional[str] = None
        self._is_correction_mode: bool = False
        self._downloaded_jobs: set[str] = set()
        self._has_active_jobs: bool = False

        # Для совместимости с panel.py (snapshot)
        self._jobs_cache: dict = {}

        # Connection status — локальный режим всегда "connected"
        QTimer.singleShot(100, lambda: self.connection_status.emit("connected"))

    # ══════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════

    def set_panel_visible(self, visible: bool) -> None:
        """Уведомить контроллер о видимости панели."""
        self._panel_visible = visible
        if visible:
            self._emit_jobs_list()

    def refresh(self, *, force_full: bool = False, show_loading: bool = False) -> None:
        """Обновить список задач в UI."""
        self._emit_jobs_list()

    def create_job(self) -> None:
        """Показать диалог создания задачи и запустить OCR локально."""
        from PySide6.QtWidgets import QDialog, QMessageBox

        from app.gui.ocr_dialog import OCRDialog

        mw = self.main_window

        if not mw.pdf_document or not mw.annotation_document:
            QMessageBox.warning(mw, "Ошибка", "Откройте PDF документ")
            return

        if getattr(mw, "_current_node_locked", False):
            QMessageBox.warning(
                mw,
                "Документ заблокирован",
                "Этот документ заблокирован от изменений.\nСначала снимите блокировку.",
            )
            return

        pdf_path = mw.annotation_document.pdf_path
        if not pdf_path or not Path(pdf_path).exists():
            if getattr(mw, "_current_pdf_path", None):
                pdf_path = mw._current_pdf_path
                mw.annotation_document.pdf_path = pdf_path

        if not pdf_path or not Path(pdf_path).exists():
            QMessageBox.warning(mw, "Ошибка", "PDF файл не найден")
            return

        node_id = getattr(mw, "_current_node_id", None) or None

        task_name = Path(pdf_path).stem if pdf_path else ""
        dialog = OCRDialog(mw, task_name=task_name, pdf_path=pdf_path)
        if dialog.exec() != QDialog.Accepted:
            return

        self._last_output_dir = dialog.output_dir
        self._last_engine = dialog.ocr_backend
        self._pending_output_dir = dialog.output_dir

        all_blocks = self._get_selected_blocks()
        if not all_blocks:
            QMessageBox.warning(mw, "Ошибка", "Нет блоков для распознавания")
            return

        blocks_needing = self._get_blocks_needing_ocr()
        has_previous = len(all_blocks) > len(blocks_needing)

        if has_previous and blocks_needing:
            from app.gui.smart_ocr_mode_dialog import SmartOCRModeDialog

            mode_dialog = SmartOCRModeDialog(
                mw,
                total_count=len(all_blocks),
                needs_ocr_count=len(blocks_needing),
                successful_count=len(all_blocks) - len(blocks_needing),
            )
            if mode_dialog.exec() != QDialog.Accepted:
                return

            if mode_dialog.selected_mode == SmartOCRModeDialog.MODE_SMART:
                selected_blocks = blocks_needing
                self._is_correction_mode = True
                cleanup_blocks = [b.id for b in selected_blocks]
                self._clear_ocr_text_in_memory(blocks_to_reprocess=cleanup_blocks)
            else:
                selected_blocks = all_blocks
                self._is_correction_mode = False
                self._clear_ocr_text_in_memory()

        elif has_previous and not blocks_needing:
            QMessageBox.information(
                mw,
                "Все распознано",
                "Все блоки уже успешно распознаны.\n"
                "Добавьте новые блоки или пометьте для корректировки.",
            )
            return
        else:
            selected_blocks = all_blocks
            self._is_correction_mode = False
            self._clear_ocr_text_in_memory()

        # Serialize blocks для передачи в subprocess
        blocks_data = [b.to_dict() for b in selected_blocks]

        # В correction mode: передаём ВСЕ блоки для полной генерации HTML/MD.
        # _clear_ocr_text_in_memory уже очистила ocr_text для корректируемых блоков,
        # поэтому full_blocks_data содержит старые ocr_text для успешных + None для новых.
        full_blocks_data = (
            [b.to_dict() for b in all_blocks] if self._is_correction_mode else None
        )

        output_dir = dialog.output_dir or str(Path(pdf_path).parent)

        from app.gui.toast import show_toast

        show_toast(mw, "Запуск локального OCR...", duration=1500)

        # Запуск через LocalOcrRunner
        # .env может содержать host.docker.internal (для Docker) — десктоп-клиент
        # всегда работает на хосте, поэтому заменяем на localhost
        chandra_url = os.getenv("CHANDRA_BASE_URL", "http://localhost:1234")
        chandra_url = chandra_url.replace("host.docker.internal", "localhost")
        qwen_url = os.getenv("QWEN_BASE_URL", "") or chandra_url
        qwen_url = qwen_url.replace("host.docker.internal", "localhost")

        self._runner.submit_job(
            pdf_path=pdf_path,
            blocks_data=blocks_data,
            output_dir=output_dir,
            engine="lmstudio",
            chandra_base_url=chandra_url,
            qwen_base_url=qwen_url,
            is_correction_mode=self._is_correction_mode,
            node_id=node_id,
            task_name=task_name,
            full_blocks_data=full_blocks_data,
        )

    def force_recognize_block(self, block_id: str) -> None:
        """Принудительно пере-распознать один блок через correction pipeline.

        V1: только для документов из дерева проектов (node_id есть).
        """
        from PySide6.QtWidgets import QMessageBox

        mw = self.main_window

        # ── Валидация ────────────────────────────────────────────────
        if not mw.pdf_document or not mw.annotation_document:
            QMessageBox.warning(mw, "Ошибка", "Откройте PDF документ")
            return

        node_id = getattr(mw, "_current_node_id", None) or None
        if not node_id:
            QMessageBox.warning(
                mw, "Ошибка",
                "Принудительное распознавание доступно только\n"
                "для документов из дерева проектов.",
            )
            return

        if getattr(mw, "_current_node_locked", False):
            QMessageBox.warning(mw, "Документ заблокирован",
                                "Снимите блокировку перед распознаванием.")
            return

        if self._has_active_jobs:
            QMessageBox.warning(mw, "OCR занят",
                                "Дождитесь завершения текущей OCR-задачи.")
            return

        pdf_path = mw.annotation_document.pdf_path
        if not pdf_path or not Path(pdf_path).exists():
            if getattr(mw, "_current_pdf_path", None):
                pdf_path = mw._current_pdf_path
            else:
                QMessageBox.warning(mw, "Ошибка", "PDF файл не найден")
                return

        # ── Найти блок ───────────────────────────────────────────────
        target_block = None
        for page in mw.annotation_document.pages:
            for block in page.blocks:
                if block.id == block_id:
                    target_block = block
                    break
            if target_block:
                break

        if not target_block:
            logger.warning(f"Block {block_id} not found for force recognize")
            return

        # ── Flush autosave ───────────────────────────────────────────
        self._flush_autosave(node_id)

        # ── Очистить старые OCR-поля целевого блока ──────────────────
        target_block.ocr_text = None
        target_block.ocr_html = None
        target_block.ocr_json = None
        target_block.ocr_meta = None
        target_block.is_correction = True

        # ── Подготовка данных ────────────────────────────────────────
        blocks_data = [target_block.to_dict()]
        all_blocks = self._get_selected_blocks()
        full_blocks_data = [b.to_dict() for b in all_blocks]

        output_dir = str(Path(pdf_path).parent)
        task_name = f"Блок {block_id[:9]}"

        chandra_url = os.getenv("CHANDRA_BASE_URL", "http://localhost:1234")
        chandra_url = chandra_url.replace("host.docker.internal", "localhost")
        qwen_url = os.getenv("QWEN_BASE_URL", "") or chandra_url
        qwen_url = qwen_url.replace("host.docker.internal", "localhost")

        # ── Запуск ───────────────────────────────────────────────────
        self._is_correction_mode = True

        from app.gui.toast import show_toast
        show_toast(mw, f"Принудительное OCR блока {block_id[:9]}...", duration=2000)

        self._runner.submit_job(
            pdf_path=pdf_path,
            blocks_data=blocks_data,
            output_dir=output_dir,
            engine="lmstudio",
            chandra_base_url=chandra_url,
            qwen_base_url=qwen_url,
            is_correction_mode=True,
            node_id=node_id,
            task_name=task_name,
            full_blocks_data=full_blocks_data,
        )

    def cancel_job(self, job_id: str) -> None:
        """Отменить задачу."""
        self._runner.cancel_job(job_id)
        self._emit_jobs_list()

    def cancel_all_jobs(self) -> None:
        """Отменить все активные задачи."""
        for job_id, job in list(self._runner.jobs.items()):
            if job.status in ("queued", "processing"):
                self._runner.cancel_job(job_id)
        self._emit_jobs_list()

    def clear_all_jobs(self) -> None:
        """Очистить все задачи из списка."""
        from PySide6.QtWidgets import QMessageBox

        if not self._runner.jobs:
            from app.gui.toast import show_toast
            show_toast(self.main_window, "Нет задач для очистки")
            return

        reply = QMessageBox.question(
            self.main_window,
            "Очистка задач",
            f"Удалить все задачи ({len(self._runner.jobs)} шт.)?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        for job_id in list(self._runner.jobs.keys()):
            self._runner.remove_job(job_id)
        self._emit_jobs_list()

    def resume_job(self, job_id: str) -> None:
        """Resume не поддерживается в локальном режиме."""
        pass

    def reorder_job(self, job_id: str, direction: str) -> None:
        """Reorder не поддерживается в локальном режиме."""
        pass

    def delete_job(self, job_id: str) -> None:
        """Удалить задачу."""
        self._runner.remove_job(job_id)
        self._emit_jobs_list()

    def show_job_details(self, job_id: str) -> None:
        """Показать детальную информацию о задаче."""
        job = self._runner.jobs.get(job_id)
        if not job:
            return

        from app.gui.job_details_dialog import JobDetailsDialog

        details = {
            "id": job.id,
            "status": job.status,
            "progress": job.progress,
            "document_name": job.document_name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(job.created_at)),
            "status_message": job.status_message,
            "error_message": job.error_message or "",
            "recognized": job.recognized,
            "total_blocks": job.total_blocks,
            "output_dir": job.output_dir,
            "mode": "local",
        }
        dialog = JobDetailsDialog(details, self.main_window)
        dialog.exec()

    def auto_download_result(self, job_id: str) -> None:
        """Для локального OCR — результаты уже на диске, сразу применяем."""
        job = self._runner.jobs.get(job_id)
        if not job or not job.output_dir:
            return
        if job_id in self._downloaded_jobs:
            return

        self._downloaded_jobs.add(job_id)
        self._reload_annotation_from_result(job.output_dir)
        self._refresh_document_in_tree()
        self.update_ocr_stats()
        self.download_finished.emit(job_id, job.output_dir)

    def mark_node_downloads_complete(self, node_id: str) -> None:
        """Пометить done-джобы для node как скачанные."""
        for job_id, job in self._runner.jobs.items():
            if job.status in ("done", "partial") and job.node_id == node_id:
                self._downloaded_jobs.add(job_id)

    def update_ocr_stats(self) -> None:
        """Пересчитать и обновить статистику OCR."""
        mw = self.main_window
        if not mw.annotation_document:
            return
        panel = getattr(mw, "remote_ocr_panel", None)
        if panel and hasattr(panel, "update_ocr_stats"):
            panel.update_ocr_stats()

    def get_cached_job(self, job_id: str):
        """Получить задачу по ID."""
        return self._runner.jobs.get(job_id)

    def has_snapshot(self) -> bool:
        """Есть ли задачи для показа."""
        return bool(self._runner.jobs)

    def get_snapshot_jobs(self) -> list:
        """Получить задачи для начального показа."""
        return self._build_job_list()

    def shutdown(self) -> None:
        """Освободить ресурсы."""
        for job_id in list(self._runner.jobs.keys()):
            if self._runner.jobs[job_id].status in ("queued", "processing"):
                self._runner.cancel_job(job_id)

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Runner signal handlers
    # ══════════════════════════════════════════════════════════════════

    def _on_runner_job_created(self, job) -> None:
        """LocalOcrRunner создал задачу."""
        self._emit_jobs_list()
        self.job_created.emit(self._to_job_info(job))

    def _on_runner_job_updated(self, job) -> None:
        """Прогресс обновлён."""
        self._emit_jobs_list()

    def _on_runner_job_finished(self, job) -> None:
        """Задача завершена — применяем результаты."""
        self._has_active_jobs = self._runner.has_active_jobs
        self._emit_jobs_list()

        if job.status in ("done", "partial") and job.output_dir:
            # Автоматическое применение результатов
            self.auto_download_result(job.id)

            from app.gui.toast import show_toast

            if job.status == "partial":
                msg = f"OCR частично: {job.recognized}/{job.total_blocks}"
            else:
                msg = f"OCR завершён: {job.recognized}/{job.total_blocks}"
            show_toast(self.main_window, msg, duration=5000)
        elif job.status == "error":
            from app.gui.toast import show_toast
            show_toast(self.main_window, f"OCR ошибка: {job.error_message or 'неизвестная'}", duration=5000)

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Autosave helpers
    # ══════════════════════════════════════════════════════════════════

    def _flush_autosave(self, node_id: str | None = None) -> None:
        """Синхронный flush AnnotationCache перед стартом OCR."""
        try:
            from app.gui.annotation_cache import get_annotation_cache

            cache = get_annotation_cache()
            nid = node_id or getattr(self.main_window, "_current_node_id", None)
            if nid:
                cache.flush_for_ocr(nid)
        except Exception as e:
            logger.debug(f"Flush autosave failed (non-fatal): {e}")

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Block helpers
    # ══════════════════════════════════════════════════════════════════

    def _get_selected_blocks(self) -> list:
        """Получить все блоки для OCR."""
        blocks = []
        if self.main_window.annotation_document:
            for page in self.main_window.annotation_document.pages:
                if page.blocks:
                    blocks.extend(page.blocks)
        return blocks

    def _get_blocks_needing_ocr(self) -> list:
        """Получить только блоки, нуждающиеся в OCR."""
        from rd_core.ocr_block_status import needs_ocr

        blocks = []
        if self.main_window.annotation_document:
            for page in self.main_window.annotation_document.pages:
                for block in page.blocks or []:
                    if needs_ocr(block):
                        blocks.append(block)
        return blocks

    def _clear_ocr_text_in_memory(
        self,
        blocks_to_reprocess: list | None = None,
    ) -> int:
        """Быстрая очистка ocr_text в памяти."""
        reprocess_set = set(blocks_to_reprocess) if blocks_to_reprocess else None
        cleared = 0
        if self.main_window.annotation_document:
            for page in self.main_window.annotation_document.pages:
                for block in page.blocks:
                    if hasattr(block, "ocr_text") and block.ocr_text:
                        if reprocess_set is None or block.id in reprocess_set:
                            block.ocr_text = None
                            cleared += 1
        return cleared

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Result handling
    # ══════════════════════════════════════════════════════════════════

    def _reload_annotation_from_result(self, extract_dir: str) -> None:
        """Обновить OCR-поля в блоках из результата pipeline."""
        try:
            pdf_path = getattr(self.main_window, "_current_pdf_path", None)
            if not pdf_path:
                return

            pdf_stem = Path(pdf_path).stem
            ann_path = Path(extract_dir) / "annotation.json"

            # Также проверяем формат {pdf_stem}_annotation.json
            if not ann_path.exists():
                ann_path = Path(extract_dir) / f"{pdf_stem}_annotation.json"
            if not ann_path.exists():
                logger.warning(f"Файл аннотации не найден: {extract_dir}")
                return

            import json as _json
            from rd_core.annotation_io import (
                is_flat_format,
                migrate_annotation_data,
                migrate_flat_to_structured,
            )
            from rd_core.models import Document

            with open(str(ann_path), "r", encoding="utf-8") as _f:
                _data = _json.load(_f)

            if is_flat_format(_data):
                _data = migrate_flat_to_structured(_data)
            _data, _result = migrate_annotation_data(_data)
            if not _result.success:
                logger.warning(f"Не удалось загрузить OCR результат: {_result.errors}")
                return
            loaded_doc, _ = Document.from_dict(_data, migrate_ids=True)
            if not loaded_doc:
                logger.warning("Не удалось загрузить OCR результат: Document.from_dict вернул None")
                return

            current_doc = self.main_window.annotation_document
            if not current_doc:
                return

            # Собираем все OCR-поля по ID блоков из результата
            ocr_results: dict[str, dict] = {}
            for page in loaded_doc.pages:
                for block in page.blocks:
                    if block.ocr_text:
                        ocr_results[block.id] = {
                            "ocr_text": block.ocr_text,
                            "ocr_html": getattr(block, "ocr_html", None),
                            "ocr_json": getattr(block, "ocr_json", None),
                            "ocr_meta": getattr(block, "ocr_meta", None),
                        }

            # Обновляем все OCR-поля в существующих блоках
            updated_count = 0
            for page in current_doc.pages:
                for block in page.blocks:
                    if block.id in ocr_results:
                        result = ocr_results[block.id]
                        block.ocr_text = result["ocr_text"]
                        block.ocr_html = result["ocr_html"]
                        block.ocr_json = result["ocr_json"]
                        block.ocr_meta = result["ocr_meta"]
                        if block.is_correction:
                            block.is_correction = False
                        updated_count += 1

            self.main_window._render_current_page()
            if (
                hasattr(self.main_window, "blocks_tree_manager")
                and self.main_window.blocks_tree_manager
            ):
                self.main_window.blocks_tree_manager.update_blocks_tree()

            if updated_count > 0:
                self.main_window._auto_save_annotation()

            # Перезагружаем OCR preview
            if hasattr(self.main_window, "_load_ocr_preview_data"):
                self.main_window._load_ocr_preview_data()

            for preview_attr in ("ocr_preview", "ocr_preview_inline"):
                preview = getattr(self.main_window, preview_attr, None)
                if preview and getattr(preview, "_current_block_id", None):
                    preview.show_block(preview._current_block_id)

            logger.info(f"OCR результаты применены: {updated_count} блоков обновлено")
        except Exception as e:
            logger.error(f"Ошибка применения OCR результатов: {e}")

    def _refresh_document_in_tree(self) -> None:
        """Обновить узел документа в дереве проектов — инвалидировать R2 кэши."""
        node_id = getattr(self.main_window, "_current_node_id", None)
        if not node_id:
            return
        # Инвалидировать R2 кэши (metadata + disk) ��ля этого узла
        try:
            from rd_core.r2_utils import invalidate_r2_cache
            invalidate_r2_cache(f"tree_docs/{node_id}/", prefix=True)
        except Exception:
            pass
        logger.info(f"Refreshed document in tree: {node_id}")

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Job list helpers
    # ══════════════════════════════════════════════════════════════════

    def _emit_jobs_list(self) -> None:
        """Emit текущий список задач для UI."""
        job_list = self._build_job_list()
        self._jobs_cache = {self._job_id(j): j for j in job_list}
        self.jobs_updated.emit(job_list)

    def _build_job_list(self) -> list:
        """Построить список задач в формате, совместимом с JobsTableModel."""
        jobs = []
        for job in self._runner.jobs.values():
            jobs.append(self._to_job_info(job))
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    def _to_job_info(self, job) -> _LocalJobInfo:
        """Конвертировать LocalJob в объект, совместимый с JobsTableModel."""
        return _LocalJobInfo(
            id=job.id,
            status=job.status,
            progress=job.progress,
            document_id="",
            document_name=job.document_name,
            task_name=job.document_name,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(job.created_at)),
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time())),
            error_message=job.error_message,
            node_id=job.node_id,
            status_message=job.status_message,
            priority=0,
        )

    @staticmethod
    def _job_id(job) -> str:
        return job.id if hasattr(job, "id") else ""


class _LocalJobInfo:
    """Lightweight замена app.ocr_client.models.JobInfo для UI совместимости."""

    __slots__ = (
        "id", "status", "progress", "document_id", "document_name",
        "task_name", "created_at", "updated_at", "error_message",
        "node_id", "status_message", "priority",
    )

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
