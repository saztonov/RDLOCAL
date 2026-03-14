"""Миксин управления жизненным циклом OCR задач."""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class JobLifecycleMixin:
    """Возобновление, отмена, переименование, переупорядочивание задач."""

    def resume_job(self, job_id: str) -> bool:
        """Возобновить задачу с паузы."""
        resp = self._request_with_retry("post", f"/jobs/{job_id}/resume")
        return resp.json().get("ok", False)

    def cancel_job(self, job_id: str) -> bool:
        """Отменить задачу."""
        resp = self._request_with_retry("post", f"/jobs/{job_id}/cancel")
        return resp.json().get("ok", False)

    def rename_job(self, job_id: str, task_name: str) -> bool:
        """Переименовать задачу."""
        resp = self._request_with_retry(
            "patch", f"/jobs/{job_id}", data={"task_name": task_name}
        )
        return resp.json().get("ok", False)

    def reorder_job(self, job_id: str, direction: str) -> bool:
        """Переместить задачу вверх/вниз в очереди обработки."""
        resp = self._request_with_retry(
            "post", f"/jobs/{job_id}/reorder", data={"direction": direction}
        )
        return resp.json().get("ok", False)
