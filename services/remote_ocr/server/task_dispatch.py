"""Единая точка запуска OCR-задач — embedded job manager."""

from services.remote_ocr.server.logging_config import get_logger

_logger = get_logger(__name__)


def dispatch_ocr_task(job_id: str, block_count: int = 0, priority: int = 5) -> str:
    """Отправить задачу в embedded job manager.

    Args:
        job_id: ID задачи OCR
        block_count: Количество блоков (для информации)
        priority: Приоритет (не используется в embedded режиме)

    Returns:
        job_id (вместо celery_task_id)
    """
    from .embedded_job_manager_singleton import get_job_manager

    manager = get_job_manager()
    manager.submit(job_id)

    _logger.info(
        f"Job {job_id[:8]} dispatched to embedded manager (blocks={block_count})",
        extra={"event": "job_dispatched", "job_id": job_id},
    )

    return job_id
