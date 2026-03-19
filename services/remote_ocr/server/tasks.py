"""Celery задачи для OCR обработки.

Тонкий entrypoint — вся логика в job_stages.py.
"""
from __future__ import annotations

import time

from celery.exceptions import SoftTimeLimitExceeded

from .celery_app import celery_app
from .job_context import JobBootstrapError, JobSkipped, JobValidationError
from .job_stages import (
    bootstrap_job,
    cleanup,
    finalize,
    generate_and_upload,
    handle_error,
    register_results,
    run_ocr,
    validate_job,
)
from .logging_config import get_logger
from .memory_utils import log_memory
from .storage import get_job, update_job_status

logger = get_logger(__name__)


@celery_app.task(bind=True, name="run_ocr_task", max_retries=3, rate_limit="4/m")
def run_ocr_task(self, job_id: str) -> dict:
    """Celery задача для обработки OCR"""
    start_mem = log_memory(f"[START] Задача {job_id}")
    start_time = time.time()

    ctx = None
    engine = "openrouter"
    lmstudio_acquired = False
    try:
        job = validate_job(job_id, self.request.id)
        ctx = bootstrap_job(job, start_mem)
        engine = ctx.engine
        lmstudio_acquired = ctx.lmstudio_acquired

        run_ocr(ctx)
        generate_and_upload(ctx)
        register_results(ctx)
        return finalize(ctx)

    except JobSkipped as e:
        logger.info(f"Job {job_id}: {e.status} — {e}")
        return {"status": e.status, "message": str(e)}

    except JobValidationError as e:
        logger.error(f"Job {job_id}: validation error — {e}")
        return {"status": "error", "message": str(e)}

    except JobBootstrapError as e:
        logger.error(f"Job {job_id}: bootstrap error — {e}")
        return handle_error(job_id, e, ctx, start_time, engine)

    except SoftTimeLimitExceeded:
        duration = int(time.time() - start_time)
        logger.warning(
            f"Job {job_id}: soft timeout exceeded ({duration}s)",
            extra={"event": "task_soft_timeout", "job_id": job_id, "duration_ms": duration * 1000},
        )
        # Если задача уже cancelled (revoke от cancel_handler) — не менять статус
        job_now = get_job(job_id)
        if job_now and job_now.status == "cancelled":
            return {"status": "cancelled", "message": "Отменено пользователем"}
        update_job_status(
            job_id, "error",
            error_message=f"Превышен таймаут обработки ({duration}s)",
            status_message="❌ Таймаут обработки",
        )
        return {"status": "error", "message": f"SoftTimeLimitExceeded ({duration}s)"}

    except Exception as e:
        return handle_error(job_id, e, ctx, start_time, engine)

    finally:
        cleanup(job_id, ctx, engine, lmstudio_acquired, celery_task_id=self.request.id)
