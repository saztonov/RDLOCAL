"""HTTP-клиент для Remote OCR сервера."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from app.ocr_client.models import JobInfo

logger = logging.getLogger(__name__)

_RETRY_DELAYS = (1.0, 2.0, 4.0)
_RETRYABLE_STATUS = {502, 503, 504}


class RemoteOCRError(Exception):
    """Ошибка взаимодействия с Remote OCR сервером."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RemoteOCRClient:
    """Клиент для Remote OCR сервера (FastAPI + Celery).

    Используется GUI-приложением для отправки OCR-задач на удалённый
    сервер, доступный по сети (в т.ч. через ngrok).
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        auth: tuple[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=10.0, read=timeout, write=timeout, pool=10.0),
            auth=auth,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    # ── Health ────────────────────────────────────────────────────────

    def health(self) -> bool:
        """Проверка доступности сервера."""
        try:
            resp = self._request_with_retry("GET", "/health", max_retries=1)
            return resp.status_code == 200
        except Exception:
            return False

    # ── Jobs CRUD ─────────────────────────────────────────────────────

    def list_jobs(
        self,
        *,
        document_id: str | None = None,
        since: str | None = None,
    ) -> tuple[list[JobInfo], str]:
        """Получить список задач. Возвращает (jobs, server_time)."""
        params: dict = {}
        if document_id:
            params["document_id"] = document_id
        if since:
            params["since"] = since

        resp = self._request_with_retry("GET", "/jobs", params=params)
        data = resp.json()
        jobs = [JobInfo.from_dict(j) for j in data.get("jobs", [])]
        server_time = data.get("server_time", "")
        return jobs, server_time

    def create_job(
        self,
        *,
        document_id: str,
        document_name: str,
        client_id: str,
        task_name: str = "",
        engine: str = "lmstudio",
        node_id: str,
        is_correction_mode: bool = False,
        pdf_path: str,
        blocks_data: list[dict] | dict,
    ) -> dict:
        """Создать OCR-задачу (multipart upload PDF + blocks). Legacy метод."""
        form_data = {
            "document_id": document_id,
            "document_name": document_name,
            "client_id": client_id,
            "task_name": task_name,
            "engine": engine,
            "node_id": node_id,
            "is_correction_mode": str(is_correction_mode).lower(),
        }

        blocks_bytes = json.dumps(blocks_data, ensure_ascii=False).encode("utf-8")

        pdf_file = Path(pdf_path)
        with open(pdf_file, "rb") as f:
            files = {
                "pdf": (pdf_file.name, f, "application/pdf"),
                "blocks_file": ("blocks.json", blocks_bytes, "application/json"),
            }
            # Длинный таймаут для upload больших PDF
            resp = self._request_with_retry(
                "POST", "/jobs",
                data=form_data,
                files=files,
                timeout=600.0,
                max_retries=2,
            )

        return resp.json()

    def create_node_job(
        self,
        *,
        node_id: str,
        document_id: str,
        document_name: str,
        client_id: str,
        task_name: str = "",
        is_correction_mode: bool = False,
    ) -> dict:
        """Создать OCR-задачу для node-backed документа (без upload PDF/blocks).

        Сервер сам берёт PDF из R2 и annotation из Supabase.
        Лёгкий POST — только метаданные.
        """
        form_data = {
            "document_id": document_id,
            "document_name": document_name,
            "client_id": client_id,
            "task_name": task_name,
            "engine": "lmstudio",
            "node_id": node_id,
            "is_correction_mode": str(is_correction_mode).lower(),
        }

        resp = self._request_with_retry(
            "POST", "/jobs/node",
            json=form_data,
            timeout=30.0,
            max_retries=2,
        )

        return resp.json()

    def get_job(self, job_id: str) -> dict:
        """Получить информацию о задаче."""
        resp = self._request_with_retry("GET", f"/jobs/{job_id}")
        return resp.json()

    def get_job_details(self, job_id: str) -> dict:
        """Получить детальную информацию о задаче."""
        resp = self._request_with_retry("GET", f"/jobs/{job_id}/details")
        return resp.json()

    def download_result(self, job_id: str) -> dict:
        """Получить ссылку на скачивание результата."""
        resp = self._request_with_retry("GET", f"/jobs/{job_id}/result")
        return resp.json()

    def cancel_job(self, job_id: str) -> dict:
        """Отменить задачу."""
        resp = self._request_with_retry("POST", f"/jobs/{job_id}/cancel")
        return resp.json()

    def pause_job(self, job_id: str) -> dict:
        resp = self._request_with_retry("POST", f"/jobs/{job_id}/pause")
        return resp.json()

    def resume_job(self, job_id: str) -> dict:
        resp = self._request_with_retry("POST", f"/jobs/{job_id}/resume")
        return resp.json()

    def restart_job(self, job_id: str) -> dict:
        resp = self._request_with_retry("POST", f"/jobs/{job_id}/restart")
        return resp.json()

    def reorder_job(self, job_id: str, direction: str) -> dict:
        resp = self._request_with_retry(
            "POST", f"/jobs/{job_id}/reorder",
            json={"direction": direction},
        )
        return resp.json()

    def delete_job(self, job_id: str) -> dict:
        resp = self._request_with_retry("DELETE", f"/jobs/{job_id}")
        return resp.json()

    # ── Internal ──────────────────────────────────────────────────────

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        max_retries: int = 3,
        timeout: float | None = None,
        **kwargs,
    ) -> httpx.Response:
        """HTTP-запрос с retry на transient ошибки."""
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                kw = dict(kwargs)
                if timeout is not None:
                    kw["timeout"] = httpx.Timeout(
                        connect=10.0, read=timeout, write=timeout, pool=10.0,
                    )

                resp = self._client.request(method, path, **kw)

                if resp.status_code < 400:
                    return resp

                if resp.status_code in _RETRYABLE_STATUS and attempt < max_retries:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning(
                        f"Server {resp.status_code} on {method} {path}, "
                        f"retry {attempt + 1}/{max_retries} in {delay}s"
                    )
                    time.sleep(delay)
                    continue

                # Не-retriable ошибка — формируем сообщение
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                raise RemoteOCRError(
                    f"HTTP {resp.status_code}: {detail}",
                    status_code=resp.status_code,
                )

            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                last_exc = e
                if attempt < max_retries:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning(
                        f"Connection error on {method} {path}: {e}, "
                        f"retry {attempt + 1}/{max_retries} in {delay}s"
                    )
                    time.sleep(delay)
                    continue
                raise RemoteOCRError(
                    f"Сервер недоступен: {e}",
                ) from e

        raise RemoteOCRError(f"Все {max_retries} попыток исчерпаны") from last_exc
