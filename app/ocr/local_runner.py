"""
LocalOcrRunner — multiprocessing обёртка для OCR pipeline.

Каждая OCR задача выполняется в отдельном Process для изоляции памяти
(аналог Celery prefork). Process умирает после завершения → чистая память.

Интеграция с Qt GUI через multiprocessing.Queue → QTimer polling.
"""
from __future__ import annotations

import hashlib
import logging
import multiprocessing
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from multiprocessing import Process, Queue
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"done", "partial", "error", "cancelled"})


def _parse_iso_to_timestamp(iso_str: str) -> float:
    """Конвертировать ISO datetime строку в Unix timestamp."""
    if not iso_str:
        return time.time()
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return time.time()


@dataclass
class LocalJob:
    """Описание локальной OCR задачи."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    pdf_path: str = ""
    document_name: str = ""
    node_id: Optional[str] = None
    status: str = "queued"  # queued | processing | done | partial | error | cancelled
    progress: float = 0.0
    status_message: str = ""
    error_message: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    recognized: int = 0
    total_blocks: int = 0
    output_dir: str = ""
    result_files: dict[str, str] = field(default_factory=dict)


class LocalOcrRunner(QObject):
    """
    Менеджер локальных OCR задач.

    Запускает каждую задачу в отдельном multiprocessing.Process.
    Обменивается с процессом через Queue (progress updates).
    Qt signals обновляют UI.
    """

    # Signals для GUI
    job_created = Signal(object)        # LocalJob
    job_updated = Signal(object)        # LocalJob (status/progress changed)
    job_finished = Signal(object)       # LocalJob (done/partial/error)
    job_error = Signal(str, str)        # (job_id, error_message)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: dict[str, LocalJob] = {}
        self._processes: dict[str, Process] = {}
        self._queues: dict[str, Queue] = {}
        self._cancel_flags: dict[str, multiprocessing.Value] = {}

        # Timer для polling multiprocessing Queue
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_queues)
        self._poll_timer.setInterval(200)  # 5 Hz polling

        # Загрузить историю завершённых задач из Supabase
        self._client_id = self._get_client_id()
        self._load_history_from_supabase()

    @property
    def jobs(self) -> dict[str, LocalJob]:
        return dict(self._jobs)

    @property
    def has_active_jobs(self) -> bool:
        return any(
            j.status in ("queued", "processing")
            for j in self._jobs.values()
        )

    # --- Persistence (Supabase) ---

    @staticmethod
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

    def _get_supabase_headers(self) -> dict | None:
        """Получить заголовки для Supabase REST API."""
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
        if not url or not key:
            return None
        return {
            "_url": url,
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def _load_history_from_supabase(self) -> None:
        """Загрузить завершённые задачи из Supabase."""
        try:
            headers = self._get_supabase_headers()
            if not headers:
                return
            import httpx
            url = headers.pop("_url")
            resp = httpx.get(
                f"{url}/rest/v1/jobs"
                f"?select=id,document_name,status,progress,created_at,error_message,"
                f"status_message,node_id"
                f"&status=in.(done,partial,error,cancelled)"
                f"&order=created_at.desc"
                f"&limit=100",
                headers=headers,
                timeout=5.0,
            )
            if resp.status_code != 200:
                logger.warning(f"Jobs history: HTTP {resp.status_code}")
                return
            rows = resp.json()
            for row in rows:
                job_id = row["id"]
                if job_id in self._jobs:
                    continue
                self._jobs[job_id] = LocalJob(
                    id=job_id,
                    document_name=row.get("document_name", ""),
                    node_id=row.get("node_id"),
                    status=row.get("status", "done"),
                    progress=row.get("progress", 1.0),
                    status_message=row.get("status_message", ""),
                    error_message=row.get("error_message"),
                    created_at=_parse_iso_to_timestamp(row.get("created_at", "")),
                )
            if rows:
                logger.info(f"Jobs history: загружено {len(rows)} задач из Supabase")
        except Exception:
            logger.debug("Jobs history: не удалось загрузить", exc_info=True)

    def _save_job_to_supabase(self, job: LocalJob) -> None:
        """Upsert задачу в Supabase (jobs + job_settings)."""
        try:
            headers = self._get_supabase_headers()
            if not headers:
                return
            import httpx
            url = headers.pop("_url")
            now = datetime.now(timezone.utc).isoformat()
            doc_id = hashlib.md5(job.pdf_path.encode()).hexdigest() if job.pdf_path else ""
            payload = {
                "id": job.id,
                "document_id": doc_id,
                "document_name": job.document_name,
                "task_name": job.document_name,
                "status": job.status,
                "progress": job.progress,
                "error_message": job.error_message,
                "status_message": job.status_message,
                "client_id": self._client_id,
                "node_id": job.node_id,
                "updated_at": now,
            }
            if job.status in _TERMINAL_STATUSES:
                payload["completed_at"] = now
            # Upsert by id
            resp = httpx.post(
                f"{url}/rest/v1/jobs",
                headers={**headers, "Prefer": "return=minimal,resolution=merge-duplicates"},
                json=payload,
                timeout=5.0,
            )
            if resp.status_code not in (200, 201):
                logger.debug(f"Jobs save: HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception:
            logger.debug("Jobs save: ошибка", exc_info=True)

    def submit_job(
        self,
        pdf_path: str,
        blocks_data: list[dict],
        output_dir: str,
        *,
        engine: str = "lmstudio",
        chandra_base_url: str = "",
        qwen_base_url: str = "",
        chandra_http_timeout: int = 300,
        qwen_http_timeout: int = 300,
        max_concurrent: int = 2,
        timeout_seconds: int = 3600,
        is_correction_mode: bool = False,
        node_id: str | None = None,
        task_name: str = "",
    ) -> LocalJob:
        """Создать и запустить OCR задачу в отдельном процессе."""
        job = LocalJob(
            pdf_path=pdf_path,
            document_name=Path(pdf_path).name,
            node_id=node_id,
            status="processing",
            total_blocks=len(blocks_data),
            output_dir=output_dir,
            status_message="Запуск OCR...",
        )

        self._jobs[job.id] = job

        # IPC: Queue для progress updates, Value для отмены
        progress_queue: Queue = Queue()
        cancel_flag = multiprocessing.Value("b", 0)  # shared bool

        self._queues[job.id] = progress_queue
        self._cancel_flags[job.id] = cancel_flag

        # Запуск в отдельном процессе
        proc = Process(
            target=_run_ocr_process,
            args=(
                job.id,
                pdf_path,
                blocks_data,
                output_dir,
                progress_queue,
                cancel_flag,
            ),
            kwargs={
                "engine": engine,
                "chandra_base_url": chandra_base_url,
                "qwen_base_url": qwen_base_url,
                "chandra_http_timeout": chandra_http_timeout,
                "qwen_http_timeout": qwen_http_timeout,
                "max_concurrent": max_concurrent,
                "timeout_seconds": timeout_seconds,
                "is_correction_mode": is_correction_mode,
                "node_id": node_id,
            },
            daemon=True,
        )
        self._processes[job.id] = proc
        proc.start()

        # Start polling
        if not self._poll_timer.isActive():
            self._poll_timer.start()

        self._save_job_to_supabase(job)
        self.job_created.emit(job)
        return job

    def cancel_job(self, job_id: str) -> bool:
        """Отменить задачу."""
        if job_id not in self._cancel_flags:
            return False

        self._cancel_flags[job_id].value = 1  # Signal cancellation

        job = self._jobs.get(job_id)
        if job and job.status in ("queued", "processing"):
            job.status = "cancelled"
            job.status_message = "Отменено"
            self._save_job_to_supabase(job)
            self.job_updated.emit(job)

        return True

    def remove_job(self, job_id: str):
        """Удалить завершённую задачу из списка."""
        self._jobs.pop(job_id, None)
        self._cleanup_process(job_id)

    def _poll_queues(self):
        """Опрос multiprocessing Queue для получения progress updates."""
        finished_ids = []

        for job_id, queue in list(self._queues.items()):
            job = self._jobs.get(job_id)
            if not job:
                finished_ids.append(job_id)
                continue

            # Drain all available messages
            while not queue.empty():
                try:
                    msg = queue.get_nowait()
                except Exception:
                    break

                msg_type = msg.get("type", "")

                if msg_type == "progress":
                    job.progress = msg.get("progress", job.progress)
                    job.status_message = msg.get("message", job.status_message)
                    job.status = "processing"
                    self.job_updated.emit(job)

                elif msg_type == "result":
                    job.status = msg.get("status", "done")
                    job.progress = 1.0
                    job.recognized = msg.get("recognized", 0)
                    job.total_blocks = msg.get("total_blocks", job.total_blocks)
                    job.error_count = msg.get("error_count", 0)
                    job.error_message = msg.get("error_message")
                    job.result_files = msg.get("result_files", {})
                    job.status_message = msg.get("status_message", "")
                    self._save_job_to_supabase(job)
                    self.job_finished.emit(job)
                    finished_ids.append(job_id)

            # Check if process died unexpectedly
            proc = self._processes.get(job_id)
            if proc and not proc.is_alive() and job_id not in finished_ids:
                if job.status in ("queued", "processing"):
                    job.status = "error"
                    job.error_message = f"Процесс завершился с кодом {proc.exitcode}"
                    job.status_message = "Ошибка процесса"
                    self._save_job_to_supabase(job)
                    self.job_finished.emit(job)
                finished_ids.append(job_id)

        # Cleanup finished
        for job_id in finished_ids:
            self._cleanup_process(job_id)

        # Stop timer if no active jobs
        if not any(
            j.status in ("queued", "processing")
            for j in self._jobs.values()
        ):
            self._poll_timer.stop()

    def _cleanup_process(self, job_id: str):
        """Очистка ресурсов процесса."""
        proc = self._processes.pop(job_id, None)
        if proc and proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)

        self._queues.pop(job_id, None)
        self._cancel_flags.pop(job_id, None)


def _run_ocr_process(
    job_id: str,
    pdf_path: str,
    blocks_data: list[dict],
    output_dir: str,
    progress_queue: Queue,
    cancel_flag: multiprocessing.Value,
    **kwargs,
):
    """
    Entrypoint для multiprocessing.Process.

    Выполняет OCR pipeline и отправляет результаты через Queue.
    Этот код работает в ОТДЕЛЬНОМ процессе — изолирует утечки памяти.
    """
    import sys

    # Ensure project root in path for imports
    project_root = str(Path(__file__).parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from dotenv import load_dotenv
    load_dotenv()

    # Setup basic logging in subprocess
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    def on_progress(progress: float, message: str):
        try:
            progress_queue.put_nowait({
                "type": "progress",
                "progress": progress,
                "message": message,
            })
        except Exception:
            pass

    def check_cancelled() -> bool:
        try:
            return bool(cancel_flag.value)
        except Exception:
            return False

    try:
        from app.ocr.local_pipeline import run_local_ocr

        result = run_local_ocr(
            pdf_path=pdf_path,
            blocks_data=blocks_data,
            output_dir=output_dir,
            on_progress=on_progress,
            check_cancelled=check_cancelled,
            **kwargs,
        )

        # Status message
        if result.status == "done":
            status_msg = f"Готово: {result.recognized}/{result.total_blocks}"
        elif result.status == "partial":
            status_msg = f"Частично: {result.recognized}/{result.total_blocks}"
        else:
            status_msg = result.error_message or "Ошибка"

        progress_queue.put({
            "type": "result",
            "status": result.status,
            "recognized": result.recognized,
            "total_blocks": result.total_blocks,
            "error_count": result.error_count,
            "error_message": result.error_message,
            "result_files": result.result_files,
            "status_message": status_msg,
            "duration_seconds": result.duration_seconds,
        })

    except Exception as e:
        progress_queue.put({
            "type": "result",
            "status": "error",
            "error_message": str(e),
            "status_message": f"Ошибка: {e}",
            "recognized": 0,
            "total_blocks": len(blocks_data),
            "error_count": 0,
            "result_files": {},
            "duration_seconds": time.time(),
        })
