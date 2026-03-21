"""Контроллер бизнес-логики Remote OCR задач.

Чистый QObject, владеющий состоянием, polling-таймером и фоновыми операциями.
Не зависит от UI-виджетов напрямую — общается через Qt-сигналы.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

if TYPE_CHECKING:
    from app.gui.main_window import MainWindow
    from app.ocr_client import JobInfo, RemoteOCRClient

logger = logging.getLogger(__name__)


class JobsController(QObject):
    """Контроллер состояния и бизнес-логики Remote OCR задач.

    Владеет:
      - кешем задач, оптимистичными задачами, множеством скачанных
      - polling-таймером с адаптивными интервалами
      - ThreadPoolExecutor для фоновых операций
    """

    # ── Сигналы (для UI) ──────────────────────────────────────────────

    jobs_updated = Signal(list)                # полный список JobInfo для модели
    connection_status = Signal(str)            # "connected" / "disconnected" / "loading"
    job_uploading = Signal(object)             # temp JobInfo (status="uploading")
    job_created = Signal(object)               # real JobInfo
    job_create_error = Signal(str, str)        # error_type, message
    download_started = Signal(str, int)        # job_id, total_files
    download_progress = Signal(str, int, str)  # job_id, current, filename
    download_finished = Signal(str, str)       # job_id, extract_dir
    download_error = Signal(str, str)          # job_id, error

    # ── Polling-интервалы ─────────────────────────────────────────────

    POLL_VISIBLE_ACTIVE = 5000       # видимая + активные задачи
    POLL_VISIBLE_IDLE = 30000        # видимая + нет активных
    POLL_HIDDEN_ACTIVE = 15000       # скрытая + активные (авто-скачивание)
    POLL_ERROR_BASE = 120000         # базовый backoff при ошибке
    POLL_ERROR_MAX = 300000          # максимальный backoff

    # ── Внутренний объект сигналов для thread-safe emit ────────────────

    class _WorkerSignals(QObject):
        """Промежуточные сигналы из ThreadPoolExecutor."""

        jobs_loaded = Signal(list, str)         # (jobs, server_time)
        jobs_error = Signal(str)
        job_created = Signal(object)
        job_create_error = Signal(str, str)
        lifecycle_result = Signal(str, bool, str)  # (op_name, success, message)
        download_started = Signal(str, int)
        download_progress = Signal(str, int, str)
        download_finished = Signal(str, str)
        download_error = Signal(str, str)
        job_details_loaded = Signal(dict)  # job_details dict

    # ── __init__ ──────────────────────────────────────────────────────

    def __init__(self, main_window: MainWindow, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.main_window = main_window

        # Состояние
        self._client: Optional[RemoteOCRClient] = None
        self._jobs_cache: dict[str, JobInfo] = {}
        self._optimistic_jobs: dict[str, tuple[JobInfo, float]] = {}
        self._downloaded_jobs: set[str] = set()
        self._downloading_jobs: set[str] = set()  # guard: загрузки в процессе
        self._is_fetching: bool = False
        self._is_manual_refresh: bool = False
        self._consecutive_errors: int = 0
        self._last_server_time: Optional[str] = None
        self._force_full_refresh: bool = False
        self._has_active_jobs: bool = False
        self._panel_visible: bool = False

        # Контекст последнего создания
        self._last_output_dir: Optional[str] = None
        self._last_engine: Optional[str] = None
        self._pending_output_dir: Optional[str] = None
        self._is_correction_mode: bool = False

        # Executor + worker signals
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._worker = self._WorkerSignals()
        self._connect_worker_signals()

        # Polling timer
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._on_poll_tick)
        # Не запускаем — панель пока не видима

        # Загружаем snapshot для мгновенного показа
        self._load_snapshot()

    # ── Подключение worker-сигналов ───────────────────────────────────

    def _connect_worker_signals(self) -> None:
        self._worker.jobs_loaded.connect(self._on_jobs_loaded)
        self._worker.jobs_error.connect(self._on_jobs_error)
        self._worker.job_created.connect(self._on_job_created)
        self._worker.job_create_error.connect(self._on_job_create_error)
        self._worker.download_started.connect(self._on_download_started)
        self._worker.download_progress.connect(self._on_download_progress)
        self._worker.download_finished.connect(self._on_download_finished)
        self._worker.download_error.connect(self._on_download_error)
        self._worker.lifecycle_result.connect(self._on_lifecycle_result)
        self._worker.job_details_loaded.connect(self._on_job_details_loaded)

    # ══════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════

    def set_panel_visible(self, visible: bool) -> None:
        """Уведомить контроллер о видимости панели — управляет polling."""
        self._panel_visible = visible
        if visible:
            has_snapshot = bool(self._jobs_cache and self._last_server_time)
            self.refresh(force_full=not has_snapshot, show_loading=not has_snapshot)
        self._adjust_poll_interval()

    def refresh(self, *, force_full: bool = False, show_loading: bool = False) -> None:
        """Обновить список задач.

        Args:
            force_full: Принудительно полная перезагрузка (кнопка refresh).
            show_loading: Показать статус "loading" в UI.
        """
        if self._is_fetching:
            return

        # При множественных ошибках сначала проверяем health
        if not force_full and not show_loading and not self._try_health_check_before_poll():
            return

        self._is_fetching = True
        self._is_manual_refresh = force_full

        if force_full:
            self._force_full_refresh = True
        if show_loading:
            self.connection_status.emit("loading")

        self._executor.submit(self._fetch_bg)

    def create_job(self) -> None:
        """Показать диалог создания задачи и отправить на сервер."""
        from PySide6.QtWidgets import QMessageBox

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
        r2_key = getattr(mw, "_current_r2_key", None) or None

        # r2.exists() проверка перенесена в _create_job_bg (background thread)

        from PySide6.QtWidgets import QDialog

        from app.gui.ocr_dialog import OCRDialog

        task_name = Path(pdf_path).stem if pdf_path else ""
        dialog = OCRDialog(mw, task_name=task_name, pdf_path=pdf_path)
        if dialog.exec() != QDialog.Accepted:
            return

        self._last_output_dir = dialog.output_dir
        self._last_engine = dialog.ocr_backend

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
                cleanup_blocks = None
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
            cleanup_blocks = None
            self._clear_ocr_text_in_memory()

        client = self._get_client()
        if client is None:
            QMessageBox.warning(mw, "Ошибка", "Клиент не инициализирован")
            return

        engine = dialog.ocr_backend if dialog.ocr_backend in (
            "openrouter", "datalab", "chandra", "qwen"
        ) else "openrouter"

        self._pending_output_dir = dialog.output_dir

        from app.gui.toast import show_toast

        show_toast(mw, "Отправка задачи...", duration=1500)

        logger.info(
            f"Отправка задачи на сервер: engine={engine}, blocks={len(selected_blocks)}, "
            f"image_model={getattr(dialog, 'image_model', None)}, "
            f"stamp_model={getattr(dialog, 'stamp_model', None)}, node_id={node_id}"
        )

        temp_job_id = f"uploading-{uuid.uuid4().hex[:12]}"

        from app.ocr_client import JobInfo

        temp_job = JobInfo(
            id=temp_job_id,
            status="uploading",
            progress=0.0,
            document_id="",
            document_name=Path(pdf_path).name,
            task_name=task_name,
            status_message="Загрузка на сервер...",
        )
        self.job_uploading.emit(temp_job)

        # Снимок annotation_document для фонового save_to_db
        annotation_doc = mw.annotation_document

        self._executor.submit(
            self._create_job_bg,
            client,
            pdf_path,
            selected_blocks,
            task_name,
            engine,
            getattr(dialog, "text_model", None),
            getattr(dialog, "table_model", None),
            getattr(dialog, "image_model", None),
            getattr(dialog, "stamp_model", None),
            node_id,
            temp_job_id,
            self._is_correction_mode,
            r2_key,
            cleanup_blocks,
            annotation_doc,
        )

    def cancel_job(self, job_id: str) -> None:
        """Отменить задачу (в background)."""
        client = self._get_client()
        if client is None:
            return
        # Оптимистичное обновление статуса в кэше
        cached = self._jobs_cache.get(job_id)
        if cached:
            cached.status = "cancelled"
            self._emit_jobs_list()
        self._executor.submit(self._lifecycle_op_bg, "cancel", client.cancel_job, job_id)

    def resume_job(self, job_id: str) -> None:
        """Возобновить задачу с паузы (в background)."""
        client = self._get_client()
        if client is None:
            return
        cached = self._jobs_cache.get(job_id)
        if cached:
            cached.status = "queued"
            self._emit_jobs_list()
        self._executor.submit(self._lifecycle_op_bg, "resume", client.resume_job, job_id)

    def delete_job(self, job_id: str) -> None:
        """Удалить задачу (в background)."""
        client = self._get_client()
        if client is None:
            return
        # Оптимистичное удаление из кэша
        self._jobs_cache.pop(job_id, None)
        self._emit_jobs_list()
        self._executor.submit(self._lifecycle_op_bg, "delete", client.delete_job, job_id)

    def cancel_all_jobs(self) -> None:
        """Отменить все активные задачи (queued/processing/paused)."""
        from PySide6.QtWidgets import QMessageBox

        client = self._get_client()
        if client is None:
            return

        cached_jobs = list(self._jobs_cache.values()) if self._jobs_cache else []
        active_jobs = [
            j for j in cached_jobs if j.status in ("queued", "processing", "paused")
        ]

        if not active_jobs:
            from app.gui.toast import show_toast

            show_toast(self.main_window, "Нет активных задач для отмены")
            return

        reply = QMessageBox.question(
            self.main_window,
            "Отмена задач",
            f"Отменить все активные задачи ({len(active_jobs)} шт.)?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        # Оптимистичное обновление
        for job in active_jobs:
            job.status = "cancelled"
        self._emit_jobs_list()

        job_ids = [j.id for j in active_jobs]
        self._executor.submit(self._cancel_all_bg, client, job_ids)

    def clear_all_jobs(self) -> None:
        """Очистить все задачи."""
        from PySide6.QtWidgets import QMessageBox

        client = self._get_client()
        if client is None:
            QMessageBox.warning(self.main_window, "Ошибка", "Клиент не инициализирован")
            return

        reply = QMessageBox.question(
            self.main_window,
            "Очистка задач",
            "Удалить все задачи из списка?\n\n"
            "- Файлы документов из дерева проектов сохранятся\n"
            "- Legacy файлы (без привязки к дереву) будут удалены",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        job_ids = [j.id for j in self._jobs_cache.values()]
        # Оптимистичная очистка
        self._jobs_cache.clear()
        self._emit_jobs_list()

        self._executor.submit(self._clear_all_bg, client, job_ids)

    def reorder_job(self, job_id: str, direction: str) -> None:
        """Переместить задачу вверх/вниз в очереди обработки (в background)."""
        cached_job = self._jobs_cache.get(job_id)
        if not cached_job or cached_job.status != "queued":
            return

        client = self._get_client()
        if client is None:
            return

        label = "вверх" if direction == "up" else "вниз"
        self._executor.submit(
            self._lifecycle_op_bg, f"reorder_{label}",
            client.reorder_job, job_id, direction,
        )

    # ── Background lifecycle helpers ───────────────────────────────────

    def _lifecycle_op_bg(self, op_name: str, fn, *args) -> None:
        """Выполнить lifecycle-операцию в background и emit результат."""
        try:
            ok = fn(*args)
            self._worker.lifecycle_result.emit(
                op_name, bool(ok),
                "" if ok else f"Операция {op_name} не выполнена",
            )
        except Exception as e:
            logger.error(f"Ошибка lifecycle-операции {op_name}: {e}")
            self._worker.lifecycle_result.emit(op_name, False, str(e))

    def _cancel_all_bg(self, client, job_ids: list[str]) -> None:
        """Отменить список задач в background."""
        cancelled = 0
        errors = 0
        for jid in job_ids:
            try:
                if client.cancel_job(jid):
                    cancelled += 1
                else:
                    errors += 1
            except Exception as e:
                logger.warning(f"Ошибка отмены задачи {jid}: {e}")
                errors += 1
        msg = f"Отменено {cancelled}" + (f", ошибок: {errors}" if errors else "")
        self._worker.lifecycle_result.emit("cancel_all", errors == 0, msg)

    def _clear_all_bg(self, client, job_ids: list[str]) -> None:
        """Удалить список задач в background."""
        deleted = 0
        errors = 0
        for jid in job_ids:
            try:
                if client.delete_job(jid):
                    deleted += 1
                else:
                    errors += 1
            except Exception as e:
                logger.warning(f"Ошибка удаления задачи {jid}: {e}")
                errors += 1
        msg = f"Удалено {deleted}" + (f", ошибок: {errors}" if errors else "")
        self._worker.lifecycle_result.emit("clear_all", errors == 0, msg)

    def _emit_jobs_list(self) -> None:
        """Эмитить текущий кэш задач для обновления UI."""
        all_jobs = list(self._jobs_cache.values())
        all_jobs.sort(key=lambda j: (j.priority, j.created_at))
        self.jobs_updated.emit(all_jobs)

    def _on_lifecycle_result(self, op_name: str, success: bool, message: str) -> None:
        """Слот: результат lifecycle-операции из background."""
        from app.gui.toast import show_toast

        if message:
            show_toast(self.main_window, message)
        # Синхронизируем с сервером после любой операции
        self.refresh(force_full=True)

    def show_job_details(self, job_id: str) -> None:
        """Показать детальную информацию о задаче (загрузка в background)."""
        client = self._get_client()
        if client is None:
            return

        pdf_path = getattr(self.main_window, "_current_pdf_path", None)
        self._executor.submit(self._fetch_job_details_bg, client, job_id, pdf_path)

    def _fetch_job_details_bg(
        self, client: RemoteOCRClient, job_id: str, pdf_path: str | None
    ) -> None:
        """Фоновая загрузка деталей задачи."""
        try:
            job_details = client.get_job_details(job_id)
            if pdf_path:
                job_details["client_output_dir"] = str(Path(pdf_path).parent)
            self._worker.job_details_loaded.emit(job_details)
        except Exception as e:
            logger.error(f"Ошибка получения информации о задаче: {e}")
            self._worker.job_details_loaded.emit({"_error": str(e)})

    def _on_job_details_loaded(self, job_details: dict) -> None:
        """Слот: детали задачи загружены — показать диалог."""
        error = job_details.get("_error")
        if error:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self.main_window, "Ошибка", f"Не удалось получить информацию:\n{error}"
            )
            return

        from app.gui.job_details_dialog import JobDetailsDialog
        dialog = JobDetailsDialog(job_details, self.main_window)
        dialog.exec()

    def auto_download_result(self, job_id: str) -> None:
        """Запустить скачивание результата из R2 в папку текущего документа."""
        if job_id in self._downloading_jobs:
            logger.debug(f"Download already in progress: {job_id}")
            return

        client = self._get_client()
        if client is None:
            return

        pdf_path = getattr(self.main_window, "_current_pdf_path", None)
        if not pdf_path:
            logger.warning(
                f"Нет открытого документа для сохранения результатов job {job_id}"
            )
            return

        self._downloading_jobs.add(job_id)
        extract_dir = str(Path(pdf_path).parent)

        # Вся работа (включая get_job_details) в background thread
        self._executor.submit(
            self._auto_download_bg, client, job_id, extract_dir
        )

    def _auto_download_bg(
        self, client: RemoteOCRClient, job_id: str, extract_dir: str
    ) -> None:
        """Фоновая подготовка и запуск скачивания."""
        try:
            job_details = client.get_job_details(job_id)
            r2_prefix = job_details.get("r2_prefix")

            if not r2_prefix:
                logger.warning(f"Задача {job_id} не имеет r2_prefix")
                self._downloading_jobs.discard(job_id)
                return

            self._download_result_bg(job_id, r2_prefix, extract_dir)
        except Exception as e:
            logger.error(f"Ошибка подготовки скачивания {job_id}: {e}")
            self._downloading_jobs.discard(job_id)
            logger.error(f"Ошибка подготовки скачивания {job_id}: {e}")

    def mark_node_downloads_complete(self, node_id: str) -> None:
        """Пометить done-джобы для node как скачанные (вызывается из file_download)."""
        for job_id, job in self._jobs_cache.items():
            if job.status == "done" and getattr(job, "node_id", None) == node_id:
                self._downloaded_jobs.add(job_id)

    def update_ocr_stats(self) -> None:
        """Пересчитать и обновить статистику OCR для текущего документа.

        Эмитит сигнал stats_updated, если нужно. Но так как stats_widget
        принадлежит панели, вызываем напрямую через main_window.
        """
        mw = self.main_window
        if not mw.annotation_document:
            return

        # Панель сама вызовет stats_widget — контроллер просто помощник.
        panel = getattr(mw, "remote_ocr_panel", None)
        if panel and hasattr(panel, "update_ocr_stats"):
            panel.update_ocr_stats()

    def get_cached_job(self, job_id: str) -> Optional[JobInfo]:
        """Получить задачу из кеша по ID."""
        return self._jobs_cache.get(job_id)

    def shutdown(self) -> None:
        """Освободить ресурсы."""
        self._poll_timer.stop()
        self._executor.shutdown(wait=False)

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Snapshot persistence
    # ══════════════════════════════════════════════════════════════════

    _SNAPSHOT_KEY = "remote_ocr/jobs_snapshot"

    def _save_snapshot(self) -> None:
        """Сохранить текущий кэш задач в QSettings для мгновенного старта."""
        try:
            from dataclasses import asdict

            jobs_data = [asdict(j) for j in self._jobs_cache.values()]
            payload = json.dumps({
                "jobs": jobs_data,
                "server_time": self._last_server_time or "",
                "saved_at": time.time(),
            }, ensure_ascii=False)

            settings = QSettings()
            settings.setValue(self._SNAPSHOT_KEY, payload)
        except Exception as e:
            logger.debug(f"Не удалось сохранить snapshot: {e}")

    def _load_snapshot(self) -> None:
        """Загрузить snapshot из QSettings в кэш."""
        try:
            settings = QSettings()
            raw = settings.value(self._SNAPSHOT_KEY)
            if not raw:
                return

            data = json.loads(raw)
            saved_at = data.get("saved_at", 0)

            # Snapshot старше 24 часов — игнорируем
            if time.time() - saved_at > 86400:
                logger.debug("Snapshot слишком старый, пропускаем")
                return

            from app.ocr_client.models import JobInfo

            jobs = []
            for j in data.get("jobs", []):
                jobs.append(JobInfo(
                    id=j["id"],
                    status=j["status"],
                    progress=j["progress"],
                    document_id=j["document_id"],
                    document_name=j["document_name"],
                    task_name=j.get("task_name", ""),
                    created_at=j.get("created_at", ""),
                    updated_at=j.get("updated_at", ""),
                    error_message=j.get("error_message"),
                    node_id=j.get("node_id"),
                    status_message=j.get("status_message"),
                    priority=j.get("priority", 0),
                ))

            if jobs:
                self._jobs_cache = {j.id: j for j in jobs}
                self._last_server_time = data.get("server_time") or None
                logger.info(
                    f"Snapshot загружен: {len(jobs)} задач, "
                    f"server_time={self._last_server_time}"
                )
        except Exception as e:
            logger.debug(f"Не удалось загрузить snapshot: {e}")

    def has_snapshot(self) -> bool:
        """Есть ли данные из snapshot для мгновенного показа."""
        return bool(self._jobs_cache)

    def get_snapshot_jobs(self) -> list:
        """Получить задачи из snapshot для начального показа."""
        jobs = list(self._jobs_cache.values())
        jobs.sort(key=lambda j: (j.priority, j.created_at))
        return jobs

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Client
    # ══════════════════════════════════════════════════════════════════

    def _get_client(self) -> RemoteOCRClient | None:
        """Получить или создать клиент."""
        if self._client is None:
            try:
                import os

                from app.ocr_client import RemoteOCRClient

                base_url = os.getenv("REMOTE_OCR_BASE_URL", "http://localhost:8000")
                api_key = os.getenv("REMOTE_OCR_API_KEY")
                logger.info(
                    f"Creating RemoteOCRClient: REMOTE_OCR_BASE_URL={base_url}, "
                    f"API_KEY={'set' if api_key else 'NOT SET'}"
                )
                self._client = RemoteOCRClient()
                logger.info(f"Client created: base_url={self._client.base_url}")
            except Exception as e:
                logger.error(f"Ошибка создания клиента: {e}", exc_info=True)
                return None
        return self._client

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
        self._attach_prompts_to_blocks(blocks)
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
        self._attach_prompts_to_blocks(blocks)
        return blocks

    def _attach_prompts_to_blocks(self, blocks: list) -> None:
        """Промпты берутся из категорий в Supabase на стороне сервера."""
        pass

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Polling
    # ══════════════════════════════════════════════════════════════════

    def _on_poll_tick(self) -> None:
        """Слот таймера — инициирует refresh."""
        self.refresh()

    def _adjust_poll_interval(self) -> None:
        """Адаптировать интервал polling на основе видимости и активности."""
        if self._panel_visible:
            interval = self.POLL_VISIBLE_ACTIVE if self._has_active_jobs else self.POLL_VISIBLE_IDLE
        else:
            if self._has_active_jobs:
                interval = self.POLL_HIDDEN_ACTIVE
            else:
                self._poll_timer.stop()
                return

        if self._poll_timer.interval() != interval:
            self._poll_timer.setInterval(interval)
        if not self._poll_timer.isActive():
            self._poll_timer.start()

    def _try_health_check_before_poll(self) -> bool:
        """При множественных ошибках проверяем health перед полным poll.

        Returns:
            True если сервер доступен и можно делать poll.
        """
        if self._consecutive_errors < 3:
            return True

        client = self._get_client()
        if client is None:
            return False

        if client.health():
            logger.info("Health check OK, сброс backoff")
            self._consecutive_errors = 0
            self._force_full_refresh = True
            self._adjust_poll_interval()
            return True

        return False

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Fetch (background)
    # ══════════════════════════════════════════════════════════════════

    def _fetch_bg(self) -> None:
        """Фоновая загрузка задач (полная или дельта через единый endpoint)."""
        client = self._get_client()
        if client is None:
            self._worker.jobs_error.emit("Ошибка клиента")
            return

        force_full = self._force_full_refresh
        use_delta = (
            self._last_server_time
            and self._jobs_cache
            and not force_full
        )

        try:
            if use_delta:
                logger.debug(f"Fetching job changes since {self._last_server_time}")
                jobs, server_time = client.list_jobs(since=self._last_server_time)
                logger.debug(f"Fetched {len(jobs)} changed jobs")

                if jobs:
                    logger.info(f"Получено {len(jobs)} изменений с сервера")

                # Обновляем кеш изменёнными задачами
                for job in jobs:
                    self._jobs_cache[job.id] = job

                if server_time:
                    self._last_server_time = server_time

                # Отправляем полный список из кеша
                all_jobs = list(self._jobs_cache.values())
                all_jobs.sort(key=lambda j: (j.priority, j.created_at))
                self._worker.jobs_loaded.emit(
                    all_jobs, server_time or self._last_server_time or ""
                )
            else:
                logger.debug(f"Fetching full jobs list from {client.base_url}")
                jobs, server_time = client.list_jobs(document_id=None)
                logger.debug(f"Fetched {len(jobs)} jobs, server_time={server_time}")
                self._worker.jobs_loaded.emit(jobs, server_time)

        except Exception as e:
            logger.error(f"Ошибка получения задач: {e}", exc_info=True)
            if use_delta:
                self._force_full_refresh = True
            self._worker.jobs_error.emit(str(e))

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Jobs loaded / error (main thread)
    # ══════════════════════════════════════════════════════════════════

    def _on_jobs_loaded(self, jobs: list, server_time: str = "") -> None:
        """Слот: список задач получен."""
        self._is_fetching = False
        self._force_full_refresh = False

        # Логируем изменения статусов задач
        for job in jobs:
            cached = self._jobs_cache.get(job.id)
            if cached and cached.status != job.status:
                logger.info(
                    f"Статус задачи {job.id[:8]}... изменился: "
                    f"{cached.status} -> {job.status} (progress={job.progress:.0%})"
                )

        # При первой полной загрузке инициализируем кеш и server_time
        if self._is_manual_refresh or not self._last_server_time:
            self._jobs_cache = {j.id: j for j in jobs}
            if server_time:
                self._last_server_time = server_time
            logger.debug(
                f"Jobs cache initialized with {len(self._jobs_cache)} jobs, "
                f"server_time={self._last_server_time}"
            )

        # Merge optimistic
        jobs_ids = {j.id for j in jobs}
        merged_jobs = list(jobs)
        current_time = time.time()

        for job_id, (job_info, timestamp) in list(self._optimistic_jobs.items()):
            if job_id in jobs_ids:
                logger.info(
                    f"Задача {job_id[:8]}... найдена в ответе сервера, "
                    "удаляем из оптимистичного списка"
                )
                self._optimistic_jobs.pop(job_id, None)
            elif current_time - timestamp > 60:
                logger.warning(
                    f"Задача {job_id[:8]}... в оптимистичном списке более минуты, "
                    "удаляем (таймаут)"
                )
                self._optimistic_jobs.pop(job_id, None)
            else:
                logger.debug(
                    f"Задача {job_id[:8]}... ещё не на сервере, добавляем оптимистично"
                )
                merged_jobs.insert(0, job_info)

        # Emit для UI
        self.jobs_updated.emit(merged_jobs)
        self.connection_status.emit("connected")
        self._consecutive_errors = 0

        # Сохраняем snapshot для мгновенного старта
        self._save_snapshot()

        # Auto-download
        self._check_auto_download(merged_jobs)

        # Adjust timer
        self._has_active_jobs = any(
            j.status in ("queued", "processing") for j in merged_jobs
        )
        self._adjust_poll_interval()

    def _on_jobs_error(self, error_msg: str) -> None:
        """Слот: ошибка загрузки списка."""
        self._is_fetching = False
        self.connection_status.emit("disconnected")
        self._consecutive_errors += 1

        backoff_interval = min(
            self.POLL_ERROR_BASE * (2 ** min(self._consecutive_errors - 1, 3)),
            self.POLL_ERROR_MAX,
        )
        if self._poll_timer.interval() != backoff_interval:
            self._poll_timer.setInterval(backoff_interval)
        if not self._poll_timer.isActive():
            self._poll_timer.start()

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Auto-download
    # ══════════════════════════════════════════════════════════════════

    def _check_auto_download(self, jobs: list) -> None:
        """Проверить и запустить авто-скачивание для текущего документа."""
        current_node_id = getattr(self.main_window, "_current_node_id", None)
        if not current_node_id:
            return

        # Не скачиваем если для этого node есть активный job
        has_active_for_node = any(
            j.status in ("queued", "processing")
            and getattr(j, "node_id", None) == current_node_id
            for j in jobs
        )
        if has_active_for_node:
            return

        # Ищем самый новый done-job, который ещё не скачан
        latest_done = None
        for job in reversed(jobs):
            if (
                job.status == "done"
                and getattr(job, "node_id", None) == current_node_id
                and job.id not in self._downloaded_jobs
                and job.id not in self._downloading_jobs
            ):
                latest_done = job
                break

        if latest_done is None:
            return

        # Пропускаем если все блоки уже имеют ocr_text
        # (результаты уже применены из локального result.json при открытии)
        current_doc = getattr(self.main_window, "annotation_document", None)
        if current_doc:
            all_blocks = [
                b for p in current_doc.pages for b in p.blocks
            ]
            if all_blocks and all(b.ocr_text for b in all_blocks):
                self._downloaded_jobs.add(latest_done.id)
                return

        # Показываем toast если панель скрыта
        if not self._panel_visible:
            from app.gui.toast import show_toast

            doc_name = latest_done.task_name or latest_done.document_name or ""
            show_toast(
                self.main_window,
                f"OCR завершён: {doc_name}",
                duration=5000,
            )
            logger.info(
                f"Задача {latest_done.id[:8]}... завершена "
                f"(панель скрыта), показано уведомление"
            )

        self.auto_download_result(latest_done.id)

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Job creation (background)
    # ══════════════════════════════════════════════════════════════════

    def _create_job_bg(
        self,
        client: RemoteOCRClient,
        pdf_path: str,
        blocks: list,
        task_name: str,
        engine: str,
        text_model: str | None,
        table_model: str | None,
        image_model: str | None,
        stamp_model: str | None,
        node_id: str | None = None,
        temp_job_id: str | None = None,
        is_correction_mode: bool = False,
        r2_key: str | None = None,
        cleanup_blocks: list | None = None,
        annotation_document: object | None = None,
    ) -> None:
        """Фоновое создание задачи.

        Порядок операций:
        1. Проверка r2.exists (если node_id + r2_key)
        2. Отправка job на сервер
        3. Cleanup старых результатов (только при успехе)
        """
        try:
            from app.ocr_client import (
                AuthenticationError,
                PayloadTooLargeError,
                ServerError,
                get_or_create_client_id,
            )

            # 1. Проверка наличия PDF в R2 (перенесено из GUI-потока)
            if node_id and r2_key:
                try:
                    from rd_core.r2_storage import R2Storage

                    r2 = R2Storage()
                    if not r2.exists(r2_key):
                        self._worker.job_create_error.emit(
                            "r2",
                            "PDF не загружен в облако.\n"
                            "Синхронизируйте документ или перезагрузите его "
                            "в дерево проектов.",
                        )
                        return
                except Exception as e:
                    logger.warning(f"Не удалось проверить R2: {e}")

            # 2. Отправка задачи на сервер
            client_id = get_or_create_client_id()
            logger.info(
                f"Начало создания задачи: engine={engine}, blocks={len(blocks)}"
            )
            job_info = client.create_job(
                pdf_path,
                blocks,
                client_id=client_id,
                task_name=task_name,
                engine=engine,
                text_model=text_model,
                table_model=table_model,
                image_model=image_model,
                stamp_model=stamp_model,
                node_id=node_id,
                is_correction_mode=is_correction_mode,
            )
            logger.info(f"Задача создана: id={job_info.id}, status={job_info.status}")

            # 3. Cleanup старых результатов — ТОЛЬКО после успешного создания job
            if node_id and r2_key:
                try:
                    self._clean_old_ocr_results_bg(
                        node_id,
                        r2_key,
                        blocks_to_reprocess=cleanup_blocks,
                        annotation_document=annotation_document,
                    )
                except Exception as e:
                    logger.warning(f"Post-create cleanup failed (non-fatal): {e}")

            job_info._temp_job_id = temp_job_id
            self._worker.job_created.emit(job_info)
        except AuthenticationError:
            logger.error("Ошибка авторизации при создании задачи")
            self._worker.job_create_error.emit("auth", "Неверный API ключ.")
        except PayloadTooLargeError:
            logger.error("PDF файл слишком большой")
            self._worker.job_create_error.emit(
                "size", "PDF файл превышает лимит сервера."
            )
        except ServerError as e:
            logger.error(f"Ошибка сервера: {e}")
            self._worker.job_create_error.emit("server", f"Сервер недоступен.\n{e}")
        except Exception as e:
            logger.error(f"Ошибка создания задачи: {e}", exc_info=True)
            self._worker.job_create_error.emit("generic", str(e))

    def _on_job_created(self, job_info: JobInfo) -> None:
        """Слот: задача создана на сервере."""
        logger.info(
            f"Обработка job_created: job_id={job_info.id}, status={job_info.status}"
        )

        temp_job_id = getattr(job_info, "_temp_job_id", None)

        if temp_job_id and temp_job_id in self._optimistic_jobs:
            self._optimistic_jobs.pop(temp_job_id, None)
            logger.info(
                f"Удалена временная задача из оптимистичного списка: {temp_job_id}"
            )

        self._optimistic_jobs[job_info.id] = (job_info, time.time())
        logger.info(
            f"Реальная задача добавлена в оптимистичный список: {job_info.id}"
        )

        # Одноразовый refresh через 5 секунд (вместо 2s burst)
        QTimer.singleShot(5000, self.refresh)

        # Пробрасываем сигнал наружу для UI
        self.job_created.emit(job_info)

    def _on_job_create_error(self, error_type: str, message: str) -> None:
        """Слот: ошибка создания задачи."""
        # Удаляем uploading-задачи из оптимистичного списка
        uploading_ids = [
            job_id
            for job_id, (job_info, _) in self._optimistic_jobs.items()
            if job_info.status == "uploading"
        ]
        for job_id in uploading_ids:
            self._optimistic_jobs.pop(job_id, None)

        self.job_create_error.emit(error_type, message)

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Download (background)
    # ══════════════════════════════════════════════════════════════════

    def _download_result_bg(
        self, job_id: str, r2_prefix: str, extract_dir: str
    ) -> None:
        """Фоновое скачивание результата в папку текущего документа."""
        try:
            from rd_core.r2_metadata_cache import get_metadata_cache
            from rd_core.r2_storage import R2Storage

            r2 = R2Storage()

            extract_path = Path(extract_dir)
            extract_path.mkdir(parents=True, exist_ok=True)

            # Получаем информацию о задаче
            client = self._get_client()
            job_details = client.get_job_details(job_id) if client else {}
            doc_name = job_details.get("document_name", "result.pdf")
            doc_stem = Path(doc_name).stem

            # Получаем имя PDF из main_window
            pdf_path = getattr(self.main_window, "_current_pdf_path", None)
            pdf_stem = Path(pdf_path).stem if pdf_path else doc_stem

            actual_prefix = job_details.get("result_prefix") or r2_prefix

            # Инвалидируем кэш метаданных для префикса перед скачиванием
            get_metadata_cache().invalidate_prefix(actual_prefix + "/")
            logger.debug(f"Invalidated metadata cache for prefix: {actual_prefix}/")

            # Скачиваем _annotation.json, _ocr.html, _result.json и _document.md
            files_to_download = [
                (f"{doc_stem}_annotation.json", f"{pdf_stem}_annotation.json"),
                (f"{doc_stem}_ocr.html", f"{pdf_stem}_ocr.html"),
                (f"{doc_stem}_result.json", f"{pdf_stem}_result.json"),
                (f"{doc_stem}_document.md", f"{pdf_stem}_document.md"),
            ]

            self._worker.download_started.emit(job_id, len(files_to_download))

            for idx, (remote_name, local_name) in enumerate(files_to_download, 1):
                self._worker.download_progress.emit(job_id, idx, local_name)
                remote_key = f"{actual_prefix}/{remote_name}"
                local_path = extract_path / local_name
                try:
                    if r2.exists(remote_key, use_cache=False):
                        r2.download_file(remote_key, str(local_path), use_cache=False)
                        logger.info(f"Скачан: {local_path}")
                    else:
                        logger.warning(f"Файл не найден: {remote_key}")
                except Exception as e:
                    logger.warning(f"Не удалось скачать {remote_key}: {e}")

            logger.info(f"Результат скачан: {extract_dir}")
            self._worker.download_finished.emit(job_id, extract_dir)

        except Exception as e:
            logger.error(f"Ошибка скачивания {job_id}: {e}")
            self._worker.download_error.emit(job_id, str(e))

    def _on_download_started(self, job_id: str, total_files: int) -> None:
        """Слот: начало скачивания — пробрасываем наружу."""
        self.download_started.emit(job_id, total_files)

    def _on_download_progress(self, job_id: str, current: int, filename: str) -> None:
        """Слот: прогресс скачивания — пробрасываем наружу."""
        self.download_progress.emit(job_id, current, filename)

    def _on_download_finished(self, job_id: str, extract_dir: str) -> None:
        """Слот: скачивание завершено — обновляем состояние и пробрасываем."""
        self._downloaded_jobs.add(job_id)
        self._downloading_jobs.discard(job_id)

        # Обновляем аннотации и дерево
        self._reload_annotation_from_result(extract_dir)
        self._refresh_document_in_tree()
        self.update_ocr_stats()

        self.download_finished.emit(job_id, extract_dir)

    def _on_download_error(self, job_id: str, error_msg: str) -> None:
        """Слот: ошибка скачивания — пробрасываем наружу."""
        self._downloading_jobs.discard(job_id)
        self.download_error.emit(job_id, error_msg)

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Result handling
    # ══════════════════════════════════════════════════════════════════

    def _refresh_document_in_tree(self) -> None:
        """Обновить узел документа в дереве проектов."""
        from PySide6.QtCore import Qt

        node_id = getattr(self.main_window, "_current_node_id", None)
        if not node_id:
            return

        if not hasattr(self.main_window, "project_tree_widget"):
            return

        tree = self.main_window.project_tree_widget
        item = tree._node_map.get(node_id)
        if not item:
            return

        node = item.data(0, Qt.UserRole)
        if not node:
            return

        # Инвалидируем кэш метаданных R2 для этого документа
        try:
            from rd_core.r2_metadata_cache import get_metadata_cache

            r2_key = getattr(node, "r2_key", None)
            if r2_key:
                from pathlib import PurePosixPath

                prefix = str(PurePosixPath(r2_key).parent) + "/"
                get_metadata_cache().invalidate_prefix(prefix)
                logger.debug(f"Invalidated R2 metadata cache for prefix: {prefix}")
        except Exception as e:
            logger.warning(f"Failed to invalidate R2 metadata cache: {e}")

        logger.info(f"Refreshed document in tree: {node_id}")

    def _reload_annotation_from_result(self, extract_dir: str) -> None:
        """Обновить ocr_text в блоках из результата OCR."""
        try:
            pdf_path = getattr(self.main_window, "_current_pdf_path", None)
            if not pdf_path:
                return

            pdf_stem = Path(pdf_path).stem
            ann_path = Path(extract_dir) / f"{pdf_stem}_annotation.json"

            if not ann_path.exists():
                logger.warning(f"Файл аннотации не найден: {ann_path}")
                return

            from rd_core.annotation_io import AnnotationIO

            loaded_doc, result = AnnotationIO.load_and_migrate(str(ann_path))

            if not result.success or not loaded_doc:
                logger.warning(f"Не удалось загрузить OCR результат: {result.errors}")
                return

            current_doc = self.main_window.annotation_document
            if not current_doc:
                return

            # Собираем ocr_text по ID блоков из результата OCR
            ocr_results = {}
            for page in loaded_doc.pages:
                for block in page.blocks:
                    if block.ocr_text:
                        ocr_results[block.id] = block.ocr_text

            # Обновляем только ocr_text в существующих блоках
            updated_count = 0
            for page in current_doc.pages:
                for block in page.blocks:
                    if block.id in ocr_results:
                        block.ocr_text = ocr_results[block.id]
                        if block.is_correction:
                            block.is_correction = False
                        updated_count += 1

            self.main_window._render_current_page()
            if (
                hasattr(self.main_window, "blocks_tree_manager")
                and self.main_window.blocks_tree_manager
            ):
                self.main_window.blocks_tree_manager.update_blocks_tree()

            # Триггерим авто-сохранение с обновлёнными ocr_text
            if updated_count > 0:
                self.main_window._auto_save_annotation()

            # Перезагружаем OCR result file для preview
            if hasattr(self.main_window, "_load_ocr_result_file"):
                self.main_window._load_ocr_result_file()

            # Обновляем OCR preview для текущего выбранного блока
            for preview_attr in ("ocr_preview", "ocr_preview_inline"):
                preview = getattr(self.main_window, preview_attr, None)
                if preview and getattr(preview, "_current_block_id", None):
                    preview.show_block(preview._current_block_id)

            logger.info(f"OCR результаты применены: {updated_count} блоков обновлено")
        except Exception as e:
            logger.error(f"Ошибка применения OCR результатов: {e}")

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL: Clean old OCR results
    # ══════════════════════════════════════════════════════════════════

    # ── Cleanup: разделён на in-memory (GUI) и background (R2/Supabase) ──

    def _clear_ocr_text_in_memory(
        self,
        blocks_to_reprocess: list | None = None,
    ) -> int:
        """Быстрая очистка ocr_text в памяти (вызывается из GUI-потока).

        Returns:
            Количество очищенных блоков.
        """
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

    def _clean_old_ocr_results_bg(
        self,
        node_id: str,
        r2_key: str,
        blocks_to_reprocess: list | None = None,
        annotation_document: object | None = None,
    ) -> None:
        """Очистить старые результаты OCR (вызывается из background-потока).

        Выполняет тяжёлые операции: R2 cleanup, Supabase node_files,
        сохранение аннотации. Вызывается ПОСЛЕ успешного создания job.

        Args:
            node_id: ID узла в дереве проектов.
            r2_key: R2 ключ PDF файла.
            blocks_to_reprocess: список ID блоков для smart mode.
            annotation_document: снимок annotation_document для save_to_db.
        """
        import shutil
        from pathlib import PurePosixPath

        from app.gui.folder_settings_dialog import get_projects_dir

        is_smart_mode = blocks_to_reprocess is not None

        try:
            from rd_core.r2_storage import R2Storage

            r2 = R2Storage()
            pdf_stem = Path(r2_key).stem
            r2_prefix = str(PurePosixPath(r2_key).parent)
            projects_dir = get_projects_dir()

            if not is_smart_mode:
                # 1. Удаляем кропы из R2 и локального кэша
                crops_prefix = f"{r2_prefix}/crops/{pdf_stem}/"
                crop_keys = r2.list_files(crops_prefix)

                if crop_keys:
                    deleted_keys, errors = r2.delete_objects_batch(crop_keys)
                    logger.debug(f"Deleted {len(deleted_keys)} crops from R2")
                    if errors:
                        logger.warning(
                            f"Failed to delete {len(errors)} crops from R2"
                        )

                    if projects_dir:
                        for crop_key in deleted_keys:
                            rel = (
                                crop_key[len("tree_docs/"):]
                                if crop_key.startswith("tree_docs/")
                                else crop_key
                            )
                            crop_local = Path(projects_dir) / "cache" / rel
                            if crop_local.exists():
                                crop_local.unlink()

                # Удаляем папку crops из кэша
                if projects_dir:
                    rel_prefix = (
                        r2_prefix[len("tree_docs/"):]
                        if r2_prefix.startswith("tree_docs/")
                        else r2_prefix
                    )
                    crops_folder = (
                        Path(projects_dir)
                        / "cache"
                        / rel_prefix
                        / "crops"
                        / pdf_stem
                    )
                    if crops_folder.exists():
                        shutil.rmtree(crops_folder, ignore_errors=True)

                # 2. Удаляем записи из node_files (CROP)
                from app.tree_client import FileType, TreeClient

                client = TreeClient()
                node_files = client.get_node_files(node_id)
                for nf in node_files:
                    if nf.file_type == FileType.CROP:
                        client.delete_node_file(nf.id)

            # 3. Сохраняем аннотацию с очищенными ocr_text в Supabase
            if annotation_document:
                from app.annotation_db import AnnotationDBIO

                success = AnnotationDBIO.save_to_db(annotation_document, node_id)
                if success:
                    logger.debug(
                        f"Saved cleared annotation to Supabase: {node_id}"
                    )
                else:
                    logger.warning(
                        f"Failed to save annotation to Supabase: {node_id}"
                    )

            mode_str = (
                f"smart ({len(blocks_to_reprocess)} blocks)"
                if is_smart_mode
                else "full"
            )
            logger.info(
                f"Cleaned old OCR results for node: {node_id} (mode={mode_str})"
            )

            # Сбрасываем только джобы текущего node
            jobs_to_remove = set()
            for jid in self._downloaded_jobs:
                cached = self._jobs_cache.get(jid)
                if cached and getattr(cached, "node_id", None) == node_id:
                    jobs_to_remove.add(jid)
            self._downloaded_jobs -= jobs_to_remove

        except Exception as e:
            logger.warning(f"Failed to clean old OCR results: {e}")
