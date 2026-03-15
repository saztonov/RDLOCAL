"""Миксин чтения OCR задач."""
from __future__ import annotations

import logging
from typing import List, Optional

from app.ocr_client.models import JobInfo

logger = logging.getLogger(__name__)


def _parse_job(j: dict) -> JobInfo:
    """Парсинг JSON задачи в JobInfo."""
    return JobInfo(
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
    )


class JobReadMixin:
    """Чтение и поиск OCR задач."""

    def find_existing_job(self, document_id: str) -> Optional[JobInfo]:
        """Найти существующую активную задачу для документа."""
        try:
            jobs, _ = self.list_jobs(document_id=document_id)
            for job in jobs:
                if job.status in ("queued", "processing"):
                    logger.info(
                        f"Найдена существующая задача {job.id} в статусе {job.status}"
                    )
                    return job
        except Exception as e:
            logger.warning(f"Ошибка поиска существующей задачи: {e}")
        return None

    def list_jobs(
        self, document_id: Optional[str] = None, since: Optional[str] = None
    ) -> tuple[List[JobInfo], str]:
        """Получить список задач. При since — только изменённые."""
        params = {}
        if document_id:
            params["document_id"] = document_id
        if since:
            params["since"] = since

        logger.debug(f"list_jobs: GET {self.base_url}/jobs params={params}")
        resp = self._request_with_retry("get", "/jobs", params=params)
        logger.debug(f"list_jobs response: {resp.status_code}, len={len(resp.content)}")
        data = resp.json()

        jobs = [_parse_job(j) for j in data.get("jobs", [])]
        return jobs, data.get("server_time", "")

    def get_job(self, job_id: str) -> JobInfo:
        """Получить информацию о задаче."""
        resp = self._request_with_retry("get", f"/jobs/{job_id}")
        return _parse_job(resp.json())

    def get_job_details(self, job_id: str) -> dict:
        """Получить детальную информацию о задаче."""
        resp = self._request_with_retry("get", f"/jobs/{job_id}/details")
        return resp.json()
