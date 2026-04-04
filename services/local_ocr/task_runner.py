"""Process-based OCR worker — замена Celery.

Выполняется в multiprocessing.Process для изоляции памяти.
Вызывает те же job_stages, что и Celery worker на remote сервере.
"""
from __future__ import annotations

import logging
import multiprocessing
import signal
import time
from multiprocessing import Process, Queue
from typing import Optional

logger = logging.getLogger(__name__)


def run_ocr_in_process(
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

    # Ensure project root in path
    project_root = str(Path(__file__).parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    _log = logging.getLogger(f"local_ocr.worker.{job_id[:8]}")
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

        start_mem = log_memory(f"[START] Local task {job_id[:8]}")

        # validate_job ожидает celery_task_id — для local передаём "local"
        # execution_lock fail-open без Redis, celery_task_id check пропускается
        # если job.celery_task_id is None
        job = validate_job(job_id, celery_task_id="local")

        ctx = bootstrap_job(job, start_mem)
        engine = ctx.engine
        lmstudio_acquired = ctx.lmstudio_acquired

        run_ocr(ctx)
        generate_and_upload(ctx)
        register_results(ctx)
        result = finalize(ctx)

        progress_queue.put({"type": "done", **result})

    except Exception as e:
        # JobSkipped, JobValidationError, JobBootstrapError, и прочие
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
            cleanup(job_id, ctx, engine, lmstudio_acquired, celery_task_id="local")
        except Exception as cleanup_err:
            _log.warning(f"Cleanup error: {cleanup_err}")


class LocalTaskManager:
    """Менеджер OCR-задач: запуск в Process, polling прогресса.

    Один активный процесс за раз + очередь ожидания.
    """

    def __init__(self, max_workers: int = 1):
        self._max_workers = max_workers
        self._active: dict[str, _ActiveJob] = {}  # job_id -> _ActiveJob
        self._pending: list[str] = []  # job_ids ожидающие запуска

    def submit(self, job_id: str) -> None:
        """Поставить задачу на выполнение."""
        if len(self._active) < self._max_workers:
            self._start_process(job_id)
        else:
            self._pending.append(job_id)
            logger.info(f"Job {job_id[:8]} queued (active: {len(self._active)})")

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
        """Drain progress messages из всех активных задач.

        Returns:
            Список сообщений [{type, job_id, ...}]
        """
        messages = []
        finished_ids = []

        for job_id, active in list(self._active.items()):
            # Drain queue
            while not active.queue.empty():
                try:
                    msg = active.queue.get_nowait()
                    msg["job_id"] = job_id
                    messages.append(msg)
                    if msg["type"] in ("done", "error"):
                        finished_ids.append(job_id)
                except Exception:
                    break

            # Check if process died without sending result
            if not active.process.is_alive() and job_id not in finished_ids:
                exitcode = active.process.exitcode
                messages.append({
                    "type": "error",
                    "job_id": job_id,
                    "message": f"Process exited with code {exitcode}",
                })
                finished_ids.append(job_id)

        # Cleanup finished
        for job_id in finished_ids:
            active = self._active.pop(job_id, None)
            if active and active.process.is_alive():
                active.process.terminate()
                active.process.join(timeout=5)

        # Start pending
        while self._pending and len(self._active) < self._max_workers:
            next_id = self._pending.pop(0)
            self._start_process(next_id)

        return messages

    def _start_process(self, job_id: str) -> None:
        queue: Queue = Queue()
        cancel_flag = multiprocessing.Value("b", 0)
        proc = Process(
            target=run_ocr_in_process,
            args=(job_id, queue, cancel_flag),
            daemon=True,
        )
        self._active[job_id] = _ActiveJob(
            process=proc,
            queue=queue,
            cancel_flag=cancel_flag,
        )
        proc.start()
        logger.info(f"Started OCR process for job {job_id[:8]} (pid={proc.pid})")

    @property
    def has_active(self) -> bool:
        return bool(self._active) or bool(self._pending)

    def shutdown(self) -> None:
        """Остановить все процессы."""
        for job_id, active in self._active.items():
            active.cancel_flag.value = 1
            if active.process.is_alive():
                active.process.terminate()
                active.process.join(timeout=10)
        self._active.clear()
        self._pending.clear()


class _ActiveJob:
    __slots__ = ("process", "queue", "cancel_flag")

    def __init__(self, process: Process, queue: Queue, cancel_flag):
        self.process = process
        self.queue = queue
        self.cancel_flag = cancel_flag
