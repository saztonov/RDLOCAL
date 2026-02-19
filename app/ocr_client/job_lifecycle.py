"""Миксин управления жизненным циклом OCR задач."""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from app.ocr_client.http_pool import get_remote_ocr_client
from rd_core.models import Block

logger = logging.getLogger(__name__)


class JobLifecycleMixin:
    """Запуск, пауза, возобновление, отмена, перезапуск, переименование задач."""

    def start_job(
        self,
        job_id: str,
        engine: str = "openrouter",
        text_model: Optional[str] = None,
        table_model: Optional[str] = None,
        image_model: Optional[str] = None,
        stamp_model: Optional[str] = None,
    ) -> bool:
        """Запустить черновик на распознавание."""
        data = {
            "engine": engine,
            "text_model": text_model or "",
            "table_model": table_model or "",
            "image_model": image_model or "",
            "stamp_model": stamp_model or "",
        }
        resp = self._request_with_retry("post", f"/jobs/{job_id}/start", data=data)
        return resp.json().get("ok", False)

    def pause_job(self, job_id: str) -> bool:
        """Поставить задачу на паузу."""
        resp = self._request_with_retry("post", f"/jobs/{job_id}/pause")
        return resp.json().get("ok", False)

    def resume_job(self, job_id: str) -> bool:
        """Возобновить задачу с паузы."""
        resp = self._request_with_retry("post", f"/jobs/{job_id}/resume")
        return resp.json().get("ok", False)

    def cancel_job(self, job_id: str) -> bool:
        """Отменить задачу."""
        resp = self._request_with_retry("post", f"/jobs/{job_id}/cancel")
        return resp.json().get("ok", False)

    def restart_job(
        self, job_id: str, updated_blocks: Optional[List[Block]] = None
    ) -> bool:
        """Перезапустить задачу (сбросить результаты и поставить в очередь)."""
        if updated_blocks:
            blocks_data = [block.to_dict() for block in updated_blocks]
            blocks_json = json.dumps(blocks_data, ensure_ascii=False)
            blocks_bytes = blocks_json.encode("utf-8")

            client = get_remote_ocr_client(self.base_url, self.timeout)
            resp = client.post(
                f"/jobs/{job_id}/restart",
                headers=self._headers(),
                timeout=self.timeout,
                files={
                    "blocks_file": ("blocks.json", blocks_bytes, "application/json")
                },
            )
            self._handle_response_error(resp)
            return resp.json().get("ok", False)

        resp = self._request_with_retry("post", f"/jobs/{job_id}/restart")
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
