"""Единая точка запуска OCR-задач в Celery."""

from services.remote_ocr.server.logging_config import get_logger
from services.remote_ocr.server.storage_jobs import save_celery_task_id
from services.remote_ocr.server.tasks import run_ocr_task
from services.remote_ocr.server.timeout_utils import calculate_dynamic_timeout

_logger = get_logger(__name__)


def dispatch_ocr_task(job_id: str, block_count: int, priority: int = 5) -> str:
    """Рассчитать таймаут, отправить задачу в Celery и сохранить task_id.

    Args:
        job_id: ID задачи OCR
        block_count: Количество блоков (для расчёта таймаута)
        priority: Приоритет Celery задачи (0-10, меньше = выше)

    Returns:
        celery_task_id
    """
    soft_timeout, hard_timeout = calculate_dynamic_timeout(block_count)

    celery_result = run_ocr_task.apply_async(
        args=[job_id],
        priority=max(0, min(10, priority)),
        soft_time_limit=soft_timeout,
        time_limit=hard_timeout,
    )
    save_celery_task_id(job_id, celery_result.id)

    return celery_result.id
