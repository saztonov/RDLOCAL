"""Контроллер бизнес-логики OCR задач (dual-mode: local / remote).

Чистый QObject, владеющий состоянием и фоновыми операциями.
Не зависит от UI-виджетов напрямую — общается через Qt-сигналы.

Режим определяется env var REMOTE_OCR_BASE_URL:
  - Задан → remote mode (HTTP-клиент к серверу)
  - Не задан → local mode (LocalOcrRunner / multiprocessing)
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
    """Контроллер OCR задач (dual-mode: local / remote).

    Владеет:
      - LocalOcrRunner (local mode) или RemoteOCRClient (remote mode)
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

    # Polling intervals (ms)
    POLL_INTERVAL_ACTIVE = 3_000
    POLL_INTERVAL_IDLE = 15_000
    POLL_INTERVAL_ERROR = 10_000

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

        # ── Определяем режим ─────────────────────────────────────────
        remote_url = os.getenv("REMOTE_OCR_BASE_URL", "").strip()
        if remote_url:
            self._mode = "remote"
            self._init_remote(remote_url)
        else:
            self._mode = "local"
            self._init_local()

    # ── Init helpers ──────────────────────────────────────────────────

    def _init_local(self) -> None:
        """Инициализация local mode (multiprocessing)."""
        from app.ocr.local_runner import LocalOcrRunner

        self._runner = LocalOcrRunner(parent=self)
        self._runner.job_created.connect(self._on_runner_job_created)
        self._runner.job_updated.connect(self._on_runner_job_updated)
        self._runner.job_finished.connect(self._on_runner_job_finished)

        QTimer.singleShot(100, lambda: self.connection_status.emit("connected"))

    def _init_remote(self, base_url: str) -> None:
        """Инициализация remote mode (HTTP-клиент)."""
        from app.ocr_client import RemoteOCRClient
        from rd_core.ocr.http_utils import get_remote_ocr_auth

        auth = get_remote_ocr_auth()
        logger.info(f"Remote OCR mode: {base_url} (auth={'yes' if auth else 'no'})")
        self._client = RemoteOCRClient(base_url, auth=auth)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="remote-ocr")
        self._client_id = _get_client_id()

        # Polling state
        self._last_server_time: str | None = None
        self._is_fetching: bool = False
        self._consecutive_errors: int = 0
        self._force_full_refresh: bool = True
        self._optimistic_jobs: dict = {}  # job_id → (JobInfo, timestamp)

        # Загрузить snapshot с прошлого запуска (до первого poll)
        self._load_snapshot()

        # Thread-safe data passing (write in bg thread, read in GUI slot)
        self._pending_error: tuple[str, str] | None = None  # (error_type, message)
        self._pending_result: tuple[str, str] | None = None  # (job_id, node_id)

        # Polling timer
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._remote_poll)
        self._poll_timer.setInterval(self.POLL_INTERVAL_ACTIVE)

        # Первоначальная проверка — с задержкой, чтобы UI успел инициализироваться
        QTimer.singleShot(500, self._remote_poll)

    # ══════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════

    def set_panel_visible(self, visible: bool) -> None:
        """Уведомить контроллер о видимости панели."""
        self._panel_visible = visible
        if self._mode == "remote":
            if visible:
                if not self._poll_timer.isActive():
                    self._poll_timer.start()
                self._remote_poll()
            else:
                self._poll_timer.stop()
        else:
            if visible:
                self._emit_jobs_list()

    def refresh(self, *, force_full: bool = False, show_loading: bool = False) -> None:
        """Обновить список задач в UI."""
        if self._mode == "remote":
            self._force_full_refresh = force_full
            if show_loading:
                self.connection_status.emit("loading")
            self._remote_poll()
        else:
            self._emit_jobs_list()

    def create_job(self) -> None:
        """Создать OCR-задачу на сервере (server-only flow для tree-документов)."""
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
                # Smart: пометить нераспознанные блоки is_correction
                self._is_correction_mode = True
                for b in blocks_needing:
                    b.is_correction = True
                cleanup_blocks = [b.id for b in blocks_needing]
                self._clear_ocr_text_in_memory(blocks_to_reprocess=cleanup_blocks)
            else:
                # Full: очистить всё
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
            # Первый запуск — полный OCR
            self._is_correction_mode = False
            self._clear_ocr_text_in_memory()

        # Сохранить annotation в Supabase (с мутациями) перед отправкой на сервер
        document_name = Path(mw.annotation_document.pdf_path or "").name
        task_name = Path(mw.annotation_document.pdf_path or "").stem

        self._flush_autosave(node_id)
        self._save_annotation_to_db(node_id)

        if self._mode == "remote":
            self._server_create_job(
                node_id=node_id,
                document_name=document_name,
                task_name=task_name,
                is_correction_mode=self._is_correction_mode,
            )
        else:
            # Legacy local mode fallback
            pdf_path = mw.annotation_document.pdf_path
            selected_blocks = blocks_needing if self._is_correction_mode else all_blocks
            blocks_data = [b.to_dict() for b in selected_blocks]
            full_blocks_data = (
                [b.to_dict() for b in all_blocks] if self._is_correction_mode else None
            )
            output_dir = str(Path(pdf_path).parent) if pdf_path else ""
            self._local_create_job(
                pdf_path=pdf_path,
                blocks_data=blocks_data,
                full_blocks_data=full_blocks_data,
                output_dir=output_dir,
                node_id=node_id,
                task_name=task_name,
            )

    def force_recognize_block(self, block_id: str) -> None:
        """Принудительно пере-распознать один блок (server-only)."""
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

        # Сохранить annotation в Supabase перед отправкой
        self._flush_autosave(node_id)
        self._save_annotation_to_db(node_id)

        document_name = Path(mw.annotation_document.pdf_path or "").name
        task_name = f"Блок {block_id[:9]}"
        self._is_correction_mode = True

        from app.gui.toast import show_toast
        show_toast(mw, f"Принудительное OCR блока {block_id[:9]}...", duration=2000)

        if self._mode == "remote":
            self._server_create_job(
                node_id=node_id,
                document_name=document_name,
                task_name=task_name,
                is_correction_mode=True,
            )
        else:
            # Legacy local fallback
            pdf_path = mw.annotation_document.pdf_path
            blocks_data = [target_block.to_dict()]
            all_blocks = self._get_selected_blocks()
            full_blocks_data = [b.to_dict() for b in all_blocks]
            output_dir = str(Path(pdf_path).parent) if pdf_path else ""
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
                is_correction_mode=True,
                node_id=node_id,
                task_name=task_name,
                full_blocks_data=full_blocks_data,
            )

    def cancel_job(self, job_id: str) -> None:
        if self._mode == "remote":
            self._executor.submit(self._remote_cancel_job, job_id)
        else:
            self._runner.cancel_job(job_id)
            self._emit_jobs_list()

    def cancel_all_jobs(self) -> None:
        if self._mode == "remote":
            for job_id, job in list(self._jobs_cache.items()):
                if getattr(job, "status", "") in ("queued", "processing"):
                    self._executor.submit(self._remote_cancel_job, job_id)
        else:
            for job_id, job in list(self._runner.jobs.items()):
                if job.status in ("queued", "processing"):
                    self._runner.cancel_job(job_id)
            self._emit_jobs_list()

    def clear_all_jobs(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if self._mode == "remote":
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

            for job_id in list(self._jobs_cache.keys()):
                self._executor.submit(self._remote_delete_job, job_id)
        else:
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
        if self._mode == "remote":
            self._executor.submit(self._remote_resume_job, job_id)
        # Не поддерживается в local mode

    def reorder_job(self, job_id: str, direction: str) -> None:
        if self._mode == "remote":
            self._executor.submit(self._remote_reorder_job, job_id, direction)
        # Не поддерживается в local mode

    def delete_job(self, job_id: str) -> None:
        if self._mode == "remote":
            self._executor.submit(self._remote_delete_job, job_id)
        else:
            self._runner.remove_job(job_id)
            self._emit_jobs_list()

    def show_job_details(self, job_id: str) -> None:
        if self._mode == "remote":
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
                "mode": "remote",
            }
            dialog = JobDetailsDialog(details, self.main_window)
            dialog.exec()
        else:
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
        if self._mode == "remote":
            self._remote_download_result(job_id)
        else:
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
        if self._mode == "remote":
            for job_id, job in self._jobs_cache.items():
                if getattr(job, "status", "") in ("done", "partial") and getattr(job, "node_id", None) == node_id:
                    self._downloaded_jobs.add(job_id)
        else:
            for job_id, job in self._runner.jobs.items():
                if job.status in ("done", "partial") and job.node_id == node_id:
                    self._downloaded_jobs.add(job_id)

    def update_ocr_stats(self) -> None:
        mw = self.main_window
        if not mw.annotation_document:
            return
        panel = getattr(mw, "remote_ocr_panel", None)
        if panel and hasattr(panel, "update_ocr_stats"):
            panel.update_ocr_stats()

    def get_cached_job(self, job_id: str):
        if self._mode == "remote":
            return self._jobs_cache.get(job_id)
        return self._runner.jobs.get(job_id)

    def has_snapshot(self) -> bool:
        if self._mode == "remote":
            return bool(self._jobs_cache)
        return bool(self._runner.jobs)

    def get_snapshot_jobs(self) -> list:
        if self._mode == "remote":
            return list(self._jobs_cache.values())
        return self._build_job_list()

    def shutdown(self) -> None:
        if self._mode == "remote":
            self._poll_timer.stop()
            self._executor.shutdown(wait=False)
            self._client.close()
        else:
            for job_id in list(self._runner.jobs.keys()):
                if self._runner.jobs[job_id].status in ("queued", "processing"):
                    self._runner.cancel_job(job_id)

    # ══════════════════════════════════════════════════════════════════
    # REMOTE MODE: Snapshot persistence
    # ══════════════════════════════════════════════════════════════════

    _SNAPSHOT_PATH = Path.home() / ".rd_cache" / "jobs_snapshot.json"

    def _load_snapshot(self) -> None:
        """Загрузить кэш задач с прошлого запуска."""
        try:
            if not self._SNAPSHOT_PATH.exists():
                return
            raw = self._SNAPSHOT_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("version") != 1:
                return
            from app.ocr_client.models import JobInfo
            for jd in data.get("jobs", []):
                try:
                    job = JobInfo.from_dict(jd)
                    self._jobs_cache[job.id] = job
                except Exception:
                    continue
            self._downloaded_jobs = set(data.get("downloaded_jobs", []))
            self._last_server_time = data.get("server_time") or None
            logger.info(f"Snapshot loaded: {len(self._jobs_cache)} jobs")
        except Exception as e:
            logger.warning(f"Failed to load snapshot: {e}")

    def _save_snapshot(self) -> None:
        """Сохранить кэш задач на диск (атомарно)."""
        try:
            jobs_list = [j.to_dict() for j in self._jobs_cache.values()]
            data = {
                "version": 1,
                "server_time": self._last_server_time or "",
                "downloaded_jobs": list(self._downloaded_jobs),
                "jobs": jobs_list,
            }
            tmp_path = self._SNAPSHOT_PATH.with_suffix(".tmp")
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_path, self._SNAPSHOT_PATH)
        except Exception as e:
            logger.warning(f"Failed to save snapshot: {e}")

    # ══════════════════════════════════════════════════════════════════
    # SERVER-ONLY: Create job (node-backed, без upload)
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

    def _server_create_job(
        self,
        *,
        node_id: str,
        document_name: str,
        task_name: str,
        is_correction_mode: bool = False,
    ) -> None:
        """Создать OCR-задачу для node-backed документа (без upload PDF/blocks).

        Сервер сам берёт PDF из R2 и annotation из Supabase.
        Лёгкий POST без файлов.
        """
        from app.gui.toast import show_toast
        from app.ocr_client.models import JobInfo

        document_id = node_id  # Стабильный серверный идентификатор

        # Optimistic job для немедленного показа в UI
        temp_id = str(uuid.uuid4())
        temp_job = JobInfo(
            id=temp_id,
            status="uploading",
            progress=0.0,
            document_id=document_id,
            document_name=document_name,
            task_name=task_name,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            node_id=node_id,
            status_message="Отправка задачи...",
        )
        self._optimistic_jobs[temp_id] = (temp_job, time.time())
        self.job_uploading.emit(temp_job)
        self._emit_remote_jobs_list()

        show_toast(self.main_window, "Отправка задачи на сервер...", duration=2000)

        self._executor.submit(
            self._server_create_job_bg,
            temp_id=temp_id,
            node_id=node_id,
            document_id=document_id,
            document_name=document_name,
            task_name=task_name,
            is_correction_mode=is_correction_mode,
        )

    def _server_create_job_bg(
        self,
        *,
        temp_id: str,
        node_id: str,
        document_id: str,
        document_name: str,
        task_name: str,
        is_correction_mode: bool,
    ) -> None:
        """Фоновый поток: лёгкий POST на сервер (без файлов)."""
        from app.ocr_client import RemoteOCRError
        from app.ocr_client.models import JobInfo

        try:
            result = self._client.create_node_job(
                node_id=node_id,
                document_id=document_id,
                document_name=document_name,
                client_id=self._client_id,
                task_name=task_name,
                is_correction_mode=is_correction_mode,
            )

            # Заменяем optimistic job на реальный
            self._optimistic_jobs.pop(temp_id, None)
            real_job = JobInfo.from_dict(result)
            self._optimistic_jobs[real_job.id] = (real_job, time.time())

            QMetaObject.invokeMethod(
                self, "_on_remote_job_created",
                Qt.QueuedConnection,
            )

        except RemoteOCRError as e:
            self._optimistic_jobs.pop(temp_id, None)
            error_type = "server"
            if e.status_code == 400:
                error_type = "validation"
            elif e.status_code == 503:
                error_type = "server"
            self._pending_error = (error_type, str(e))
            QMetaObject.invokeMethod(
                self, "_on_remote_job_create_error",
                Qt.QueuedConnection,
            )
        except Exception as e:
            self._optimistic_jobs.pop(temp_id, None)
            logger.error(f"Server create_node_job failed: {e}", exc_info=True)
            self._pending_error = ("generic", str(e))
            QMetaObject.invokeMethod(
                self, "_on_remote_job_create_error",
                Qt.QueuedConnection,
            )

    # ══════════════════════════════════════════════════════════════════
    # REMOTE MODE: Create job (legacy, с upload PDF)
    # ══════════════════════════════════════════════════════════════════

    def _remote_create_job(
        self,
        *,
        pdf_path: str,
        blocks_data: list[dict],
        full_blocks_data: list[dict] | None,
        node_id: str | None,
        task_name: str,
        is_correction_mode: bool | None = None,
    ) -> None:
        """Отправить OCR-задачу на удалённый сервер (async)."""
        from app.gui.toast import show_toast
        from app.ocr_client.models import JobInfo

        correction = is_correction_mode if is_correction_mode is not None else self._is_correction_mode
        document_name = Path(pdf_path).name
        document_id = hashlib.md5(pdf_path.encode()).hexdigest()

        # Optimistic job для немедленного показа в UI
        temp_id = str(uuid.uuid4())
        temp_job = JobInfo(
            id=temp_id,
            status="uploading",
            progress=0.0,
            document_id=document_id,
            document_name=document_name,
            task_name=task_name,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            node_id=node_id,
            status_message="Загрузка на сервер...",
        )
        self._optimistic_jobs[temp_id] = (temp_job, time.time())
        self.job_uploading.emit(temp_job)
        self._emit_remote_jobs_list()

        show_toast(self.main_window, "Отправка на сервер...", duration=2000)

        # Данные для передачи блоков: если есть full_blocks_data (correction mode),
        # отправляем полный annotation (сервер сам разберётся)
        send_blocks = full_blocks_data if full_blocks_data else blocks_data

        self._executor.submit(
            self._remote_create_job_bg,
            temp_id=temp_id,
            pdf_path=pdf_path,
            document_id=document_id,
            document_name=document_name,
            task_name=task_name,
            node_id=node_id or "",
            is_correction_mode=correction,
            blocks_data=send_blocks,
        )

    def _remote_create_job_bg(
        self,
        *,
        temp_id: str,
        pdf_path: str,
        document_id: str,
        document_name: str,
        task_name: str,
        node_id: str,
        is_correction_mode: bool,
        blocks_data: list[dict] | dict,
    ) -> None:
        """Фоновый поток: отправка задачи на сервер."""
        from app.ocr_client import RemoteOCRError
        from app.ocr_client.models import JobInfo

        try:
            result = self._client.create_job(
                document_id=document_id,
                document_name=document_name,
                client_id=self._client_id,
                task_name=task_name,
                engine="lmstudio",
                node_id=node_id,
                is_correction_mode=is_correction_mode,
                pdf_path=pdf_path,
                blocks_data=blocks_data,
            )

            # Заменяем optimistic job на реальный
            self._optimistic_jobs.pop(temp_id, None)
            real_job = JobInfo.from_dict(result)
            self._optimistic_jobs[real_job.id] = (real_job, time.time())

            # Emit signals в GUI потоке
            QMetaObject.invokeMethod(
                self, "_on_remote_job_created",
                Qt.QueuedConnection,
            )

        except RemoteOCRError as e:
            self._optimistic_jobs.pop(temp_id, None)
            error_type = "server"
            if e.status_code == 413:
                error_type = "size"
            elif e.status_code == 503:
                error_type = "server"
            self._pending_error = (error_type, str(e))
            QMetaObject.invokeMethod(
                self, "_on_remote_job_create_error",
                Qt.QueuedConnection,
            )
        except Exception as e:
            self._optimistic_jobs.pop(temp_id, None)
            logger.error(f"Remote create_job failed: {e}", exc_info=True)
            self._pending_error = ("generic", str(e))
            QMetaObject.invokeMethod(
                self, "_on_remote_job_create_error",
                Qt.QueuedConnection,
            )

    @Slot()
    def _on_remote_job_created(self) -> None:
        self._emit_remote_jobs_list()
        self._force_full_refresh = True
        self._remote_poll()

    @Slot()
    def _on_remote_job_create_error(self) -> None:
        self._emit_remote_jobs_list()
        error_type, message = self._pending_error or ("generic", "Unknown error")
        self._pending_error = None
        self.job_create_error.emit(error_type, message)

    # ══════════════════════════════════════════════════════════════════
    # REMOTE MODE: Polling
    # ══════════════════════════════════════════════════════════════════

    def _remote_poll(self) -> None:
        """Запустить фоновый poll (если не в процессе)."""
        if self._is_fetching:
            return

        # При множественных ошибках — health check перед poll
        if self._consecutive_errors >= 3:
            self._executor.submit(self._remote_health_then_poll)
            return

        self._is_fetching = True
        self._executor.submit(self._remote_fetch_jobs_bg)

    def _remote_health_then_poll(self) -> None:
        """Health check, затем poll при успехе."""
        try:
            if self._client.health():
                logger.info("Health check OK, сброс backoff")
                self._consecutive_errors = 0
                self._force_full_refresh = True
                self._is_fetching = True
                self._remote_fetch_jobs_bg()
                return
        except Exception:
            pass
        # Health check failed
        QMetaObject.invokeMethod(
            self, "_on_remote_poll_error",
            Qt.QueuedConnection,
        )

    def _remote_fetch_jobs_bg(self) -> None:
        """Фоновый поток: загрузка списка задач с сервера."""
        use_delta = False
        try:
            use_delta = bool(
                self._last_server_time
                and self._jobs_cache
                and not self._force_full_refresh
            )

            if use_delta:
                jobs, server_time = self._client.list_jobs(since=self._last_server_time)
                if jobs:
                    for job in jobs:
                        self._jobs_cache[job.id] = job
                if server_time:
                    self._last_server_time = server_time
            else:
                jobs, server_time = self._client.list_jobs()
                self._jobs_cache = {j.id: j for j in jobs}
                if server_time:
                    self._last_server_time = server_time

            QMetaObject.invokeMethod(
                self, "_on_remote_poll_success",
                Qt.QueuedConnection,
            )

        except Exception as e:
            logger.error(f"Poll error: {e}")
            if use_delta:
                self._force_full_refresh = True
            QMetaObject.invokeMethod(
                self, "_on_remote_poll_error",
                Qt.QueuedConnection,
            )

    @Slot()
    def _on_remote_poll_success(self) -> None:
        """GUI thread: обработка результатов poll."""
        self._is_fetching = False
        self._force_full_refresh = False
        self._consecutive_errors = 0

        # Merge optimistic jobs
        current_time = time.time()
        for job_id, (job_info, timestamp) in list(self._optimistic_jobs.items()):
            if job_id in self._jobs_cache:
                self._optimistic_jobs.pop(job_id, None)
            elif current_time - timestamp > 60:
                self._optimistic_jobs.pop(job_id, None)

        self._emit_remote_jobs_list()
        self._save_snapshot()
        self.connection_status.emit("connected")

        # Проверяем есть ли завершённые задачи для автоскачивания
        for job_id, job in self._jobs_cache.items():
            if getattr(job, "status", "") in ("done", "partial") and job_id not in self._downloaded_jobs:
                node_id = getattr(job, "node_id", None)
                if node_id:
                    self.auto_download_result(job_id)

        # Обновляем интервал polling
        self._has_active_jobs = any(
            getattr(j, "status", "") in ("queued", "processing")
            for j in self._jobs_cache.values()
        )
        new_interval = self.POLL_INTERVAL_ACTIVE if self._has_active_jobs else self.POLL_INTERVAL_IDLE
        if self._poll_timer.interval() != new_interval:
            self._poll_timer.setInterval(new_interval)

    @Slot()
    def _on_remote_poll_error(self) -> None:
        """GUI thread: ошибка poll."""
        self._is_fetching = False
        self._consecutive_errors += 1
        self.connection_status.emit("disconnected")

        backoff = min(
            self.POLL_INTERVAL_ERROR * (2 ** min(self._consecutive_errors - 1, 3)),
            180_000,
        )
        if self._poll_timer.interval() != backoff:
            self._poll_timer.setInterval(backoff)

    # ══════════════════════════════════════════════════════════════════
    # REMOTE MODE: Job actions (background)
    # ══════════════════════════════════════════════════════════════════

    def _remote_cancel_job(self, job_id: str) -> None:
        try:
            self._client.cancel_job(job_id)
            self._force_full_refresh = True
            QMetaObject.invokeMethod(self, "_remote_poll", Qt.QueuedConnection)
        except Exception as e:
            logger.error(f"Cancel job failed: {e}")

    def _remote_delete_job(self, job_id: str) -> None:
        try:
            self._client.delete_job(job_id)
            self._jobs_cache.pop(job_id, None)
            self._force_full_refresh = True
            QMetaObject.invokeMethod(self, "_remote_poll", Qt.QueuedConnection)
        except Exception as e:
            logger.error(f"Delete job failed: {e}")

    def _remote_resume_job(self, job_id: str) -> None:
        try:
            self._client.resume_job(job_id)
            self._force_full_refresh = True
            QMetaObject.invokeMethod(self, "_remote_poll", Qt.QueuedConnection)
        except Exception as e:
            logger.error(f"Resume job failed: {e}")

    def _remote_reorder_job(self, job_id: str, direction: str) -> None:
        try:
            self._client.reorder_job(job_id, direction)
            self._force_full_refresh = True
            QMetaObject.invokeMethod(self, "_remote_poll", Qt.QueuedConnection)
        except Exception as e:
            logger.error(f"Reorder job failed: {e}")

    # ══════════════════════════════════════════════════════════════════
    # REMOTE MODE: Result download
    # ══════════════════════════════════════════════════════════════════

    def _remote_download_result(self, job_id: str) -> None:
        """Для remote mode: загрузить результаты OCR из Supabase."""
        if job_id in self._downloaded_jobs:
            return

        job = self._jobs_cache.get(job_id)
        if not job:
            return

        node_id = getattr(job, "node_id", None)
        if not node_id:
            logger.warning(f"Remote job {job_id} has no node_id, cannot download results")
            return

        self._downloaded_jobs.add(job_id)
        self._executor.submit(self._remote_download_result_bg, job_id, node_id)

    def _remote_download_result_bg(self, job_id: str, node_id: str) -> None:
        """Фоновый поток: загрузка аннотации из Supabase."""
        try:
            from app.annotation_db import AnnotationDBIO

            loaded_doc = AnnotationDBIO.load_from_db(node_id)
            if not loaded_doc:
                logger.warning(f"Не удалось загрузить аннотацию из Supabase: {node_id}")
                return

            self._pending_result = (job_id, node_id)
            QMetaObject.invokeMethod(
                self, "_on_remote_result_loaded",
                Qt.QueuedConnection,
            )

        except Exception as e:
            logger.error(f"Remote download failed for {job_id}: {e}", exc_info=True)

    @Slot()
    def _on_remote_result_loaded(self) -> None:
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

            # Проверяем что текущий документ соответствует node_id
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

            logger.info(f"Remote OCR результаты применены: {updated_count} блоков обновлено")

            from app.gui.toast import show_toast
            show_toast(self.main_window, f"OCR завершён: {updated_count} блоков обновлено", duration=5000)

        except Exception as e:
            logger.error(f"Ошибка применения remote OCR результатов: {e}", exc_info=True)

    # ══════════════════════════════════════════════════════════════════
    # REMOTE MODE: Job list helper
    # ══════════════════════════════════════════════════════════════════

    def _emit_remote_jobs_list(self) -> None:
        """Emit список задач для remote mode (cache + optimistic)."""
        jobs = list(self._jobs_cache.values())

        # Добавляем optimistic jobs, которых ещё нет в кэше
        cache_ids = {j.id for j in jobs}
        for job_id, (job_info, _) in self._optimistic_jobs.items():
            if job_id not in cache_ids:
                jobs.insert(0, job_info)

        jobs.sort(key=lambda j: (getattr(j, "priority", 0), getattr(j, "created_at", "")))
        self.jobs_updated.emit(jobs)

    # ══════════════════════════════════════════════════════════════════
    # LOCAL MODE: Create job
    # ══════════════════════════════════════════════════════════════════

    def _local_create_job(
        self,
        *,
        pdf_path: str,
        blocks_data: list[dict],
        full_blocks_data: list[dict] | None,
        output_dir: str,
        node_id: str | None,
        task_name: str,
    ) -> None:
        from app.gui.toast import show_toast

        show_toast(self.main_window, "Запуск локального OCR...", duration=1500)

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

    # ══════════════════════════════════════════════════════════════════
    # LOCAL MODE: Runner signal handlers
    # ══════════════════════════════════════════════════════════════════

    def _on_runner_job_created(self, job) -> None:
        self._emit_jobs_list()
        self.job_created.emit(self._to_job_info(job))

    def _on_runner_job_updated(self, job) -> None:
        self._emit_jobs_list()

    def _on_runner_job_finished(self, job) -> None:
        self._has_active_jobs = self._runner.has_active_jobs
        self._emit_jobs_list()

        if job.status in ("done", "partial") and job.output_dir:
            self.auto_download_result(job.id)

            from app.gui.toast import show_toast

            is_corr = getattr(job, "is_correction_mode", False)
            if is_corr:
                rec_doc = getattr(job, "recognized_document", job.recognized)
                doc_total = getattr(job, "document_total_blocks", job.total_blocks)
                msg = (
                    f"Корректировка: {job.recognized}/{job.total_blocks}"
                    f" | Документ: {rec_doc}/{doc_total}"
                )
            elif job.status == "partial":
                msg = f"OCR частично: {job.recognized}/{job.total_blocks}"
            else:
                msg = f"OCR завершён: {job.recognized}/{job.total_blocks}"
            show_toast(self.main_window, msg, duration=5000)
        elif job.status == "error":
            from app.gui.toast import show_toast
            show_toast(self.main_window, f"OCR ошибка: {job.error_message or 'неизвестная'}", duration=5000)

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

    def _reload_annotation_from_result(self, extract_dir: str) -> None:
        """Обновить OCR-поля в блоках из результата pipeline (local mode)."""
        try:
            pdf_path = getattr(self.main_window, "_current_pdf_path", None)
            if not pdf_path:
                return

            pdf_stem = Path(pdf_path).stem
            ann_path = Path(extract_dir) / "annotation.json"

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

            logger.info(f"OCR результаты применены: {updated_count} блоков обновлено")
        except Exception as e:
            logger.error(f"Ошибка применения OCR результатов: {e}")

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

    # ── Local mode job list helpers ───────────────────────────────────

    def _emit_jobs_list(self) -> None:
        job_list = self._build_job_list()
        self._jobs_cache = {self._job_id(j): j for j in job_list}
        self.jobs_updated.emit(job_list)

    def _build_job_list(self) -> list:
        jobs = []
        for job in self._runner.jobs.values():
            jobs.append(self._to_job_info(job))
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    def _to_job_info(self, job) -> _LocalJobInfo:
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
