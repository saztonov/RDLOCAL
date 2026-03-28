"""
LocalOcrRunner — multiprocessing обёртка для OCR pipeline.

Каждая OCR задача выполняется в отдельном Process для изоляции памяти
(аналог Celery prefork). Process умирает после завершения → чистая память.

Интеграция с Qt GUI через multiprocessing.Queue → QTimer polling.
"""
from __future__ import annotations

import logging
import multiprocessing
import time
import uuid
from dataclasses import dataclass, field
from multiprocessing import Process, Queue
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)


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

    @property
    def jobs(self) -> dict[str, LocalJob]:
        return dict(self._jobs)

    @property
    def has_active_jobs(self) -> bool:
        return any(
            j.status in ("queued", "processing")
            for j in self._jobs.values()
        )

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
                    self.job_finished.emit(job)
                    finished_ids.append(job_id)

            # Check if process died unexpectedly
            proc = self._processes.get(job_id)
            if proc and not proc.is_alive() and job_id not in finished_ids:
                if job.status in ("queued", "processing"):
                    job.status = "error"
                    job.error_message = f"Процесс завершился с кодом {proc.exitcode}"
                    job.status_message = "Ошибка процесса"
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
