"""Контроллер бизнес-логики OCR задач.

Чистый QObject, владеющий состоянием и фоновыми операциями.
Не зависит от UI-виджетов напрямую — общается через Qt-сигналы.

Подключается к LM Studio напрямую через LMSTUDIO_BASE_URL (reverse proxy).
OCR выполняется локально через LocalOcrRunner, бэкенды обращаются к LM Studio.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QMetaObject, QObject, Qt, QTimer, Signal, Slot

if TYPE_CHECKING:
    from app.gui.main_window import MainWindow

logger = logging.getLogger(__name__)


def _get_client_id() -> str:
    """Стабильный UUID клиента, сохранённый в ~/.rd_cache/client_id."""
    path = Path.home() / ".rd_cache" / "client_id"
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        cid = str(uuid.uuid4())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cid, encoding="utf-8")
        return cid
    except Exception:
        return "local-unknown"


class JobsController(QObject):
    """Контроллер OCR задач.

    Использует LMStudioClient для health check и LocalOcrRunner для
    выполнения OCR. Бэкенды (Chandra, Qwen) подключаются к LM Studio
    через reverse proxy (LMSTUDIO_BASE_URL).

    Владеет:
      - LMStudioClient (health check LM Studio)
      - LocalOcrRunner (выполнение OCR задач)
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

    # Health check interval (ms)
    HEALTH_CHECK_INTERVAL = 30_000

    def __init__(self, main_window: MainWindow, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.main_window = main_window

        # State (shared)
        self._panel_visible: bool = False
        self._last_output_dir: Optional[str] = None
        self._last_engine: Optional[str] = None
        self._pending_output_dir: Optional[str] = None
        self._is_correction_mode: bool = False
        self._downloaded_jobs: set[str] = set()
        self._has_active_jobs: bool = False
        self._jobs_cache: dict = {}
        self._supabase_history: dict = {}

        self._client_id = _get_client_id()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ocr")

        # ── Инициализация LM Studio + LocalOcrRunner ─────────────────
        self._init_lmstudio()

    # ── Init helpers ──────────────────────────────────────────────────

    def _init_lmstudio(self) -> None:
        """Инициализация: LMStudioClient для health + LocalOcrRunner для OCR."""
        from rd_core.ocr.lmstudio_client import LMStudioClient

        self._lmstudio = LMStudioClient()
        self.connection_status.emit("loading")

        # LocalOcrRunner для выполнения OCR задач
        from app.ocr.local_runner import LocalOcrRunner

        self._runner = LocalOcrRunner(self)
        self._runner.job_created.connect(self._on_runner_job_created)
        self._runner.job_updated.connect(self._on_runner_job_updated)
        self._runner.job_finished.connect(self._on_runner_job_finished)
        self._runner.job_error.connect(self._on_runner_job_error)

        # Загрузить историю задач из Supabase
        self._load_history_from_supabase()

        # Health check timer
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._health_check)
        self._health_timer.setInterval(self.HEALTH_CHECK_INTERVAL)

        # Первоначальная проверка — с задержкой
        QTimer.singleShot(500, self._health_check)

    # ── LocalOcrRunner signal handlers ────────────────────────────────

    @Slot(object)
    def _on_runner_job_created(self, local_job) -> None:
        """LocalOcrRunner создал задачу."""
        job_info = self._local_job_to_job_info(local_job)
        self._jobs_cache[job_info.id] = job_info
        self._has_active_jobs = True
        self.job_created.emit(job_info)
        self._emit_jobs_list()

    @Slot(object)
    def _on_runner_job_updated(self, local_job) -> None:
        """LocalOcrRunner обновил прогресс."""
        job_info = self._local_job_to_job_info(local_job)
        self._jobs_cache[job_info.id] = job_info
        self._emit_jobs_list()

    @Slot(object)
    def _on_runner_job_finished(self, local_job) -> None:
        """LocalOcrRunner завершил задачу."""
        job_info = self._local_job_to_job_info(local_job)
        self._jobs_cache[job_info.id] = job_info
        self._has_active_jobs = any(
            getattr(j, "status", "") in ("queued", "processing")
            for j in self._jobs_cache.values()
        )
        self._emit_jobs_list()

        # Применить результаты OCR
        node_id = getattr(local_job, "node_id", None)
        if node_id and local_job.status in ("done", "partial"):
            self._apply_ocr_results(job_info.id, node_id)

    @Slot(str, str)
    def _on_runner_job_error(self, job_id: str, error_message: str) -> None:
        """LocalOcrRunner сообщил об ошибке."""
        self.job_create_error.emit("ocr", error_message)

    @staticmethod
    def _local_job_to_job_info(local_job):
        """Конвертировать LocalJob → JobInfo для совместимости с UI."""
        from app.ocr_client.models import JobInfo

        return JobInfo(
            id=local_job.id,
            status=local_job.status,
            progress=local_job.progress,
            document_id=local_job.node_id or "",
            document_name=local_job.document_name,
            task_name=getattr(local_job, "document_name", ""),
            created_at=time.strftime(
                "%Y-%m-%dT%H:%M:%S",
                time.localtime(local_job.created_at),
            ),
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            error_message=local_job.error_message,
            node_id=local_job.node_id,
            status_message=local_job.status_message,
            priority=0,
        )

    # ══════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════

    def set_panel_visible(self, visible: bool) -> None:
        """Уведомить контроллер о видимости панели."""
        self._panel_visible = visible
        if visible:
            if hasattr(self, "_health_timer") and not self._health_timer.isActive():
                self._health_timer.start()
            self._health_check()
        else:
            if hasattr(self, "_health_timer"):
                self._health_timer.stop()

    def refresh(self, *, force_full: bool = False, show_loading: bool = False) -> None:
        """Обновить список задач в UI."""
        if show_loading:
            self.connection_status.emit("loading")
        self._health_check()
        self._emit_jobs_list()

    def create_job(self) -> None:
        """Создать OCR-задачу (выполняется локально через LocalOcrRunner)."""
        from PySide6.QtWidgets import QDialog, QMessageBox

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

        node_id = getattr(mw, "_current_node_id", None) or None
        if not node_id:
            QMessageBox.warning(
                mw, "Ошибка",
                "OCR доступен только для документов из дерева проектов.\n"
                "Откройте документ через дерево проектов.",
            )
            return

        all_blocks = self._get_selected_blocks()
        if not all_blocks:
            QMessageBox.warning(mw, "Ошибка", "Нет блоков для распознавания")
            return

        # Smart vs Full OCR
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
                self._is_correction_mode = True
                for b in blocks_needing:
                    b.is_correction = True
                cleanup_blocks = [b.id for b in blocks_needing]
                self._clear_ocr_text_in_memory(blocks_to_reprocess=cleanup_blocks)
            else:
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
            self._is_correction_mode = False
            self._clear_ocr_text_in_memory()

        # Сохранить annotation в Supabase перед OCR
        self._flush_autosave(node_id)
        self._save_annotation_to_db(node_id)

        # Запуск OCR через LocalOcrRunner
        self._submit_local_ocr(node_id=node_id)

    def force_recognize_block(self, block_id: str) -> None:
        """Принудительно пере-распознать один блок."""
        from PySide6.QtWidgets import QMessageBox

        mw = self.main_window

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

        # Найти блок
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

        # Mutation: очистить OCR, пометить для correction
        target_block.ocr_text = None
        target_block.ocr_html = None
        target_block.ocr_json = None
        target_block.ocr_meta = None
        target_block.is_correction = True

        self._flush_autosave(node_id)
        self._save_annotation_to_db(node_id)

        self._is_correction_mode = True

        from app.gui.toast import show_toast
        show_toast(mw, f"Принудительное OCR блока {block_id[:9]}...", duration=2000)

        self._submit_local_ocr(node_id=node_id)

    def cancel_job(self, job_id: str) -> None:
        if hasattr(self, "_runner"):
            self._runner.cancel_job(job_id)

    def cancel_all_jobs(self) -> None:
        if hasattr(self, "_runner"):
            for job_id in list(self._runner.jobs.keys()):
                job = self._runner.jobs[job_id]
                if job.status in ("queued", "processing"):
                    self._runner.cancel_job(job_id)

    def clear_all_jobs(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if not self._jobs_cache:
            from app.gui.toast import show_toast
            show_toast(self.main_window, "Нет задач для очистки")
            return

        reply = QMessageBox.question(
            self.main_window,
            "Очистка задач",
            f"Удалить все задачи ({len(self._jobs_cache)} шт.)?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._jobs_cache.clear()
        self._emit_jobs_list()

    def resume_job(self, job_id: str) -> None:
        logger.info(f"Resume not supported for local jobs: {job_id}")

    def reorder_job(self, job_id: str, direction: str) -> None:
        logger.info(f"Reorder not supported for local jobs: {job_id}")

    def delete_job(self, job_id: str) -> None:
        self._jobs_cache.pop(job_id, None)
        self._emit_jobs_list()

    def show_job_details(self, job_id: str) -> None:
        job = self._jobs_cache.get(job_id)
        if not job:
            return
        from app.gui.job_details_dialog import JobDetailsDialog
        details = {
            "id": job.id,
            "status": job.status,
            "progress": job.progress,
            "document_name": job.document_name,
            "created_at": job.created_at,
            "status_message": job.status_message or "",
            "error_message": job.error_message or "",
            "recognized": 0,
            "total_blocks": 0,
            "output_dir": "",
            "mode": "lmstudio",
        }
        dialog = JobDetailsDialog(details, self.main_window)
        dialog.exec()

    def auto_download_result(self, job_id: str) -> None:
        """Для совместимости. Результаты применяются автоматически."""
        pass

    def mark_node_downloads_complete(self, node_id: str) -> None:
        for job_id, job in self._jobs_cache.items():
            if getattr(job, "status", "") in ("done", "partial") and getattr(job, "node_id", None) == node_id:
                self._downloaded_jobs.add(job_id)

    def update_ocr_stats(self) -> None:
        mw = self.main_window
        if not mw.annotation_document:
            return
        panel = getattr(mw, "remote_ocr_panel", None)
        if panel and hasattr(panel, "update_ocr_stats"):
            panel.update_ocr_stats()

    def get_cached_job(self, job_id: str):
        return self._jobs_cache.get(job_id)

    def has_snapshot(self) -> bool:
        return bool(self._jobs_cache) or bool(self._supabase_history)

    def get_snapshot_jobs(self) -> list:
        merged = dict(self._supabase_history)
        merged.update(self._jobs_cache)
        return list(merged.values())

    def shutdown(self) -> None:
        if hasattr(self, "_health_timer"):
            self._health_timer.stop()
        if hasattr(self, "_executor"):
            self._executor.shutdown(wait=False)
        if hasattr(self, "_lmstudio"):
            self._lmstudio.close()
        if hasattr(self, "_runner"):
            self._runner.shutdown()

    # ══════════════════════════════════════════════════════════════════
    # HEALTH CHECK
    # ══════════════════════════════════════════════════════════════════

    def _health_check(self) -> None:
        """Проверка доступности LM Studio (фоновый поток)."""
        self._executor.submit(self._health_check_bg)

    def _health_check_bg(self) -> None:
        """Фоновый поток: health check LM Studio."""
        try:
            ok = self._lmstudio.health_check()
            status = "connected" if ok else "disconnected"
        except Exception:
            status = "disconnected"
        QMetaObject.invokeMethod(
            self, "_on_health_result",
            Qt.QueuedConnection,
        )
        self._pending_health_status = status

    _pending_health_status: str = "disconnected"

    @Slot()
    def _on_health_result(self) -> None:
        """GUI thread: обработка результата health check."""
        self.connection_status.emit(self._pending_health_status)

    # ══════════════════════════════════════════════════════════════════
    # LOCAL OCR: Submit job
    # ══════════════════════════════════════════════════════════════════

    def _submit_local_ocr(self, *, node_id: str) -> None:
        """Запуск OCR через LocalOcrRunner."""
        from app.gui.toast import show_toast

        mw = self.main_window
        doc = mw.annotation_document
        if not doc or not doc.pdf_path:
            return

        pdf_path = doc.pdf_path
        lmstudio_url = os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234")

        # Подготовить данные блоков
        blocks_data = []
        full_blocks_data = []
        for page in doc.pages:
            for block in page.blocks:
                bd = block.to_dict() if hasattr(block, "to_dict") else {}
                full_blocks_data.append(bd)
                if self._is_correction_mode:
                    if block.is_correction:
                        blocks_data.append(bd)
                else:
                    blocks_data.append(bd)

        if not blocks_data:
            show_toast(mw, "Нет блоков для распознавания")
            return

        # Output directory
        output_dir = str(Path(pdf_path).parent / ".ocr_output")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        show_toast(mw, f"Запуск OCR ({len(blocks_data)} блоков)...", duration=2000)

        self._runner.submit_job(
            pdf_path=pdf_path,
            blocks_data=blocks_data,
            output_dir=output_dir,
            engine="lmstudio",
            chandra_base_url=lmstudio_url,
            qwen_base_url=lmstudio_url,
            chandra_http_timeout=int(
                int(os.getenv("LMSTUDIO_TIMEOUT_MS", "300000")) / 1000
            ),
            qwen_http_timeout=int(
                int(os.getenv("LMSTUDIO_TIMEOUT_MS", "300000")) / 1000
            ),
            is_correction_mode=self._is_correction_mode,
            node_id=node_id,
            task_name=Path(pdf_path).stem,
            full_blocks_data=full_blocks_data if self._is_correction_mode else None,
        )

    # ══════════════════════════════════════════════════════════════════
    # RESULT APPLICATION
    # ══════════════════════════════════════════════════════════════════

    def _apply_ocr_results(self, job_id: str, node_id: str) -> None:
        """Применить результаты OCR из Supabase к текущему документу."""
        if job_id in self._downloaded_jobs:
            return
        self._downloaded_jobs.add(job_id)
        self._executor.submit(self._apply_ocr_results_bg, job_id, node_id)

    def _apply_ocr_results_bg(self, job_id: str, node_id: str) -> None:
        """Фоновый поток: загрузка аннотации из Supabase."""
        try:
            from app.annotation_db import AnnotationDBIO

            loaded_doc = AnnotationDBIO.load_from_db(node_id)
            if not loaded_doc:
                logger.warning(f"Не удалось загрузить аннотацию из Supabase: {node_id}")
                return

            self._pending_result = (job_id, node_id)
            QMetaObject.invokeMethod(
                self, "_on_ocr_result_loaded",
                Qt.QueuedConnection,
            )

        except Exception as e:
            logger.error(f"Apply OCR results failed for {job_id}: {e}", exc_info=True)

    _pending_result: tuple[str, str] | None = None

    @Slot()
    def _on_ocr_result_loaded(self) -> None:
        """GUI thread: применить загруженные результаты."""
        pending = self._pending_result
        self._pending_result = None
        if not pending:
            return
        job_id, node_id = pending

        try:
            from app.annotation_db import AnnotationDBIO

            loaded_doc = AnnotationDBIO.load_from_db(node_id)
            if not loaded_doc:
                return

            current_doc = self.main_window.annotation_document
            if not current_doc:
                return

            current_node = getattr(self.main_window, "_current_node_id", None)
            if current_node != node_id:
                logger.info(f"Skipping result apply: current node {current_node} != job node {node_id}")
                return

            # Собираем OCR-поля из загруженного документа
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

            if not ocr_results:
                logger.info(f"No OCR results in loaded document for node {node_id}")
                return

            # Обновляем блоки в текущем документе
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

            if hasattr(self.main_window, "_load_ocr_preview_data"):
                self.main_window._load_ocr_preview_data()

            for preview_attr in ("ocr_preview", "ocr_preview_inline"):
                preview = getattr(self.main_window, preview_attr, None)
                if preview and getattr(preview, "_current_block_id", None):
                    preview.show_block(preview._current_block_id)

            self._refresh_document_in_tree()
            self.update_ocr_stats()
            self.download_finished.emit(job_id, "")

            logger.info(f"OCR результаты применены: {updated_count} блоков обновлено")

            from app.gui.toast import show_toast
            show_toast(self.main_window, f"OCR завершён: {updated_count} блоков обновлено", duration=5000)

        except Exception as e:
            logger.error(f"Ошибка применения OCR результатов: {e}", exc_info=True)

    # ══════════════════════════════════════════════════════════════════
    # SUPABASE HISTORY
    # ══════════════════════════════════════════════════════════════════

    def _save_annotation_to_db(self, node_id: str) -> bool:
        """Сохранить текущую annotation в Supabase (синхронно)."""
        try:
            from app.annotation_db import AnnotationDBIO
            doc = self.main_window.annotation_document
            if doc and node_id:
                AnnotationDBIO.save_to_db(doc, node_id)
                logger.info(f"Annotation saved to Supabase for node {node_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to save annotation to Supabase: {e}", exc_info=True)
        return False

    def _load_history_from_supabase(self) -> None:
        """Загрузить историю задач напрямую из Supabase."""
        try:
            url = os.getenv("SUPABASE_URL", "").strip()
            key = os.getenv("SUPABASE_KEY", "").strip()
            if not url or not key:
                return
            import httpx
            from app.ocr_client.models import JobInfo

            headers = {
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
            resp = httpx.get(
                f"{url}/rest/v1/jobs"
                f"?select=id,document_id,document_name,task_name,status,progress,"
                f"created_at,updated_at,error_message,status_message,node_id,priority"
                f"&order=created_at.desc"
                f"&limit=100",
                headers=headers,
                timeout=5.0,
            )
            if resp.status_code != 200:
                logger.warning(f"Supabase history: HTTP {resp.status_code}")
                return
            rows = resp.json()
            loaded = 0
            for row in rows:
                job_id = row.get("id", "")
                if not job_id or job_id in self._jobs_cache or job_id in self._supabase_history:
                    continue
                self._supabase_history[job_id] = JobInfo(
                    id=job_id,
                    status=row.get("status", ""),
                    progress=row.get("progress", 0.0),
                    document_id=row.get("document_id", ""),
                    document_name=row.get("document_name", ""),
                    task_name=row.get("task_name", ""),
                    created_at=row.get("created_at", ""),
                    updated_at=row.get("updated_at", ""),
                    error_message=row.get("error_message"),
                    node_id=row.get("node_id"),
                    status_message=row.get("status_message"),
                    priority=row.get("priority", 0),
                )
                loaded += 1
            if loaded:
                logger.info(f"Supabase history: загружено {loaded} задач")
        except Exception:
            logger.debug("Supabase history: не удалось загрузить", exc_info=True)

    # ══════════════════════════════════════════════════════════════════
    # JOB LIST HELPER
    # ══════════════════════════════════════════════════════════════════

    def _emit_jobs_list(self) -> None:
        """Emit список задач для UI (cache + supabase history + runner jobs)."""
        jobs = list(self._jobs_cache.values())
        cache_ids = {j.id for j in jobs}

        # Добавляем Supabase-историю
        for job_id, job_info in self._supabase_history.items():
            if job_id not in cache_ids:
                jobs.append(job_info)
                cache_ids.add(job_id)

        jobs.sort(key=lambda j: (getattr(j, "priority", 0), getattr(j, "created_at", "")))
        self.jobs_updated.emit(jobs)

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Shared helpers
    # ══════════════════════════════════════════════════════════════════

    def _flush_autosave(self, node_id: str | None = None) -> None:
        try:
            from app.gui.annotation_cache import get_annotation_cache
            cache = get_annotation_cache()
            nid = node_id or getattr(self.main_window, "_current_node_id", None)
            if nid:
                cache.flush_for_ocr(nid)
        except Exception as e:
            logger.debug(f"Flush autosave failed (non-fatal): {e}")

    def _get_selected_blocks(self) -> list:
        blocks = []
        if self.main_window.annotation_document:
            for page in self.main_window.annotation_document.pages:
                if page.blocks:
                    blocks.extend(page.blocks)
        return blocks

    def _get_blocks_needing_ocr(self) -> list:
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

    def _refresh_document_in_tree(self) -> None:
        node_id = getattr(self.main_window, "_current_node_id", None)
        if not node_id:
            return
        try:
            from rd_core.r2_utils import invalidate_r2_cache
            invalidate_r2_cache(f"tree_docs/{node_id}/", prefix=True)
        except Exception:
            pass
        logger.info(f"Refreshed document in tree: {node_id}")

    @staticmethod
    def _job_id(job) -> str:
        return job.id if hasattr(job, "id") else ""
