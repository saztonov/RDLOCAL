"""Embedded OCR Job Manager — замена Celery/Redis.

Основан на services/local_ocr/task_runner.py (LocalTaskManager).
Расширения:
- Job persistence: reload queued из Supabase при старте
- Hard timeout: process.kill() при превышении max_runtime
- Progress relay через multiprocessing.Queue
- Graceful shutdown
"""
from __future__ import annotations

import logging
import multiprocessing
import time
from dataclasses import dataclass, field
from multiprocessing import Process, Queue
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Hard timeout: убить процесс через N секунд (4200с = 1ч 10мин, как в Celery)
DEFAULT_HARD_TIMEOUT = 4200


@dataclass
class _ActiveJob:
    __slots__ = ("process", "queue", "cancel_flag", "started_at")
    process: Process
    queue: Queue
    cancel_flag: multiprocessing.Value
    started_at: float


def _run_ocr_in_process(
    job_id: str,
    progress_queue: Queue,
    cancel_flag: multiprocessing.Value,
) -> None:
    """Entrypoint для multiprocessing.Process.

    Вызывает серверные job_stages напрямую (без Celery).
    Отправляет progress/result через Queue.
    """
    import sys
    from pathlib import Path

    project_root = str(Path(__file__).parent.parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    _log = logging.getLogger(f"ocr.worker.{job_id[:8]}")
    start_time = time.time()
    ctx = None
    engine = "lmstudio"
    lmstudio_acquired = False

    try:
        from services.remote_ocr.server.job_stages import (
            bootstrap_job,
            cleanup,
            finalize,
            generate_and_upload,
            handle_error,
            register_results,
            run_ocr,
            validate_job,
        )
        from services.remote_ocr.server.memory_utils import log_memory
        from services.remote_ocr.server.storage import get_job, update_job_status

        start_mem = log_memory(f"[START] Task {job_id[:8]}")

        job = validate_job(job_id, celery_task_id="embedded")

        ctx = bootstrap_job(job, start_mem)
        engine = ctx.engine
        lmstudio_acquired = ctx.lmstudio_acquired

        run_ocr(ctx)
        generate_and_upload(ctx)
        register_results(ctx)
        result = finalize(ctx)

        progress_queue.put({"type": "done", **result})

    except Exception as e:
        from services.remote_ocr.server.job_context import JobSkipped, JobValidationError

        if isinstance(e, JobSkipped):
            _log.info(f"Job {job_id[:8]} skipped: {e}")
            progress_queue.put({"type": "done", "status": e.status, "message": str(e)})
        elif isinstance(e, JobValidationError):
            _log.error(f"Job {job_id[:8]} validation error: {e}")
            progress_queue.put({"type": "error", "message": str(e)})
        else:
            _log.error(f"Job {job_id[:8]} error: {e}", exc_info=True)
            try:
                handle_error(job_id, e, ctx, start_time, engine)
            except Exception:
                pass
            progress_queue.put({"type": "error", "message": str(e)})

    finally:
        try:
            cleanup(job_id, ctx, engine, lmstudio_acquired, celery_task_id="embedded")
        except Exception as cleanup_err:
            _log.warning(f"Cleanup error: {cleanup_err}")


class EmbeddedJobManager:
    """Менеджер OCR-задач: запуск в Process, polling прогресса.

    Расширения над LocalTaskManager:
    - Hard timeout для процессов
    - Job persistence (reload из Supabase)
    - WebSocket notification callback
    """

    def __init__(
        self,
        max_workers: int = 1,
        hard_timeout: float = DEFAULT_HARD_TIMEOUT,
        on_job_event: Optional[Callable[[dict], None]] = None,
    ):
        self._max_workers = max_workers
        self._hard_timeout = hard_timeout
        self._on_job_event = on_job_event
        self._active: dict[str, _ActiveJob] = {}
        self._pending: list[str] = []

    def submit(self, job_id: str) -> None:
        """Поставить задачу на выполнение."""
        if job_id in self._active or job_id in self._pending:
            logger.warning(f"Job {job_id[:8]} already submitted, ignoring")
            return

        if len(self._active) < self._max_workers:
            self._start_process(job_id)
        else:
            self._pending.append(job_id)
            logger.info(f"Job {job_id[:8]} queued (active={len(self._active)}, pending={len(self._pending)})")

    def cancel(self, job_id: str) -> bool:
        """Отменить задачу."""
        active = self._active.get(job_id)
        if active:
            active.cancel_flag.value = 1
            return True
        if job_id in self._pending:
            self._pending.remove(job_id)
            return True
        return False

    def poll(self) -> list[dict]:
        """Drain progress messages, enforce timeouts, promote pending.

        Returns:
            Список сообщений [{type, job_id, ...}]
        """
        messages: list[dict] = []
        finished_ids: list[str] = []
        now = time.time()

        for job_id, active in list(self._active.items()):
            # Drain queue
            while True:
                try:
                    msg = active.queue.get_nowait()
                    msg["job_id"] = job_id
                    messages.append(msg)
                    if msg["type"] in ("done", "error"):
                        finished_ids.append(job_id)
                except Exception:
                    break

            # Hard timeout
            if job_id not in finished_ids:
                elapsed = now - active.started_at
                if elapsed > self._hard_timeout:
                    logger.error(
                        f"Job {job_id[:8]} exceeded hard timeout ({elapsed:.0f}s > {self._hard_timeout}s), killing"
                    )
                    active.process.kill()
                    messages.append({
                        "type": "error",
                        "job_id": job_id,
                        "message": f"Hard timeout exceeded ({elapsed:.0f}s)",
                    })
                    finished_ids.append(job_id)

            # Dead process detection
            if job_id not in finished_ids and not active.process.is_alive():
                exitcode = active.process.exitcode
                messages.append({
                    "type": "error",
                    "job_id": job_id,
                    "message": f"Process exited unexpectedly with code {exitcode}",
                })
                finished_ids.append(job_id)

        # Cleanup finished
        for job_id in finished_ids:
            active = self._active.pop(job_id, None)
            if active and active.process.is_alive():
                active.process.terminate()
                active.process.join(timeout=5)

        # Promote pending
        while self._pending and len(self._active) < self._max_workers:
            next_id = self._pending.pop(0)
            self._start_process(next_id)

        # Notify via callback
        if self._on_job_event and messages:
            for msg in messages:
                try:
                    self._on_job_event(msg)
                except Exception:
                    pass

        return messages

    def reload_queued_jobs(self) -> int:
        """Загрузить queued задачи из Supabase и поставить на выполнение.

        Вызывается при старте сервера для восстановления после рестарта.

        Returns:
            Количество восстановленных задач.
        """
        try:
            from services.remote_ocr.server.storage_client import get_client

            client = get_client()
            result = (
                client.table("jobs")
                .select("id")
                .in_("status", ["queued", "processing"])
                .order("created_at")
                .execute()
            )
            jobs = result.data or []
            count = 0
            for job_row in jobs:
                job_id = job_row["id"]
                if job_id not in self._active and job_id not in self._pending:
                    self.submit(job_id)
                    count += 1
            if count:
                logger.info(f"Reloaded {count} queued/processing jobs from Supabase")
            return count
        except Exception as e:
            logger.error(f"Failed to reload queued jobs: {e}")
            return 0

    def _start_process(self, job_id: str) -> None:
        queue: Queue = Queue()
        cancel_flag = multiprocessing.Value("b", 0)
        proc = Process(
            target=_run_ocr_in_process,
            args=(job_id, queue, cancel_flag),
            daemon=True,
        )
        self._active[job_id] = _ActiveJob(
            process=proc,
            queue=queue,
            cancel_flag=cancel_flag,
            started_at=time.time(),
        )
        proc.start()
        logger.info(f"Started OCR process for job {job_id[:8]} (pid={proc.pid})")

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def has_active(self) -> bool:
        return bool(self._active) or bool(self._pending)

    def get_status(self) -> dict:
        """Статус менеджера для /queue endpoint."""
        return {
            "active": len(self._active),
            "pending": len(self._pending),
            "max_workers": self._max_workers,
            "can_accept": len(self._active) + len(self._pending) < self._max_workers + 10,
        }

    def shutdown(self) -> None:
        """Остановить все процессы."""
        logger.info(f"Shutting down job manager (active={len(self._active)}, pending={len(self._pending)})")
        for job_id, active in self._active.items():
            active.cancel_flag.value = 1
            if active.process.is_alive():
                active.process.terminate()
                active.process.join(timeout=10)
                if active.process.is_alive():
                    active.process.kill()
        self._active.clear()
        self._pending.clear()
