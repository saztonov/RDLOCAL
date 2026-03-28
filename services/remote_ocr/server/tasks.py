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
    engine = "lmstudio"
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

        # Попытка сохранить частичные результаты
        partial_saved = False
        recognized_count = 0
        if ctx and ctx.blocks:
            from .ocr_constants import is_success
            recognized_count = sum(1 for b in ctx.blocks if is_success(b.ocr_text))
            if recognized_count > 0:
                try:
                    logger.info(
                        f"Job {job_id}: сохранение {recognized_count}/{ctx.total_blocks} частичных результатов...",
                        extra={"event": "partial_save_attempt", "job_id": job_id,
                               "recognized": recognized_count, "total": ctx.total_blocks},
                    )
                    generate_and_upload(ctx)
                    partial_saved = True
                    logger.info(f"Job {job_id}: частичные результаты сохранены")
                except Exception as save_exc:
                    logger.warning(f"Job {job_id}: не удалось сохранить частичные результаты: {save_exc}")

        error_msg = f"Превышен таймаут обработки ({duration}s)"
        if partial_saved:
            error_msg += f", сохранено {recognized_count}/{ctx.total_blocks} блоков"
            status_msg = f"⚠️ Таймаут ({duration}s), сохранено {recognized_count}/{ctx.total_blocks}"
        else:
            status_msg = "❌ Таймаут обработки"

        update_job_status(
            job_id, "error",
            error_message=error_msg,
            status_message=status_msg,
        )
        return {"status": "error", "message": f"SoftTimeLimitExceeded ({duration}s)"}

    except Exception as e:
        return handle_error(job_id, e, ctx, start_time, engine)

    finally:
        cleanup(job_id, ctx, engine, lmstudio_acquired, celery_task_id=self.request.id)
