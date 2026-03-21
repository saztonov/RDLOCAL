"""Миксин создания OCR задач."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from app.ocr_client.http_pool import get_remote_ocr_client
from app.ocr_client.models import JobInfo
from rd_core.models import Block

logger = logging.getLogger(__name__)


class JobCreateMixin:
    """Создание OCR задач."""

    def create_job(
        self,
        pdf_path: str,
        selected_blocks: List[Block],
        client_id: str,
        task_name: str = "",
        engine: str = "datalab",
        text_model: Optional[str] = None,
        table_model: Optional[str] = None,
        image_model: Optional[str] = None,
        stamp_model: Optional[str] = None,
        reuse_existing: bool = True,
        node_id: Optional[str] = None,
        is_correction_mode: bool = False,
    ) -> JobInfo:
        """
        Создать задачу OCR

        Args:
            pdf_path: путь к PDF файлу
            selected_blocks: список выбранных блоков
            client_id: идентификатор клиента
            task_name: название задания
            engine: движок OCR
            text_model: модель для текста
            table_model: модель для таблиц
            image_model: модель для изображений
            stamp_model: модель для штампов
            reuse_existing: переиспользовать существующую задачу если есть
            node_id: ID узла дерева для связи результатов
            is_correction_mode: режим корректировки (обновить только эти блоки)

        Returns:
            JobInfo с информацией о созданной/существующей задаче
        """
        document_id = self.hash_pdf(pdf_path)
        document_name = Path(pdf_path).name

        # Проверяем существующую активную задачу
        if reuse_existing:
            existing = self.find_existing_job(document_id)
            if existing:
                logger.info(f"Подключаемся к существующей задаче {existing.id}")
                return existing

        # Сериализуем блоки
        blocks_data = [block.to_dict() for block in selected_blocks]
        blocks_json = json.dumps(blocks_data, ensure_ascii=False)
        blocks_bytes = blocks_json.encode("utf-8")

        # Используем увеличенный таймаут для загрузки
        client = get_remote_ocr_client(self.base_url, self.upload_timeout)
        with open(pdf_path, "rb") as pdf_file:
            form_data = {
                "document_id": document_id,
                "document_name": document_name,
                "client_id": client_id,
                "task_name": task_name,
                "engine": engine,
            }
            if text_model:
                form_data["text_model"] = text_model
            if table_model:
                form_data["table_model"] = table_model
            if image_model:
                form_data["image_model"] = image_model
            if stamp_model:
                form_data["stamp_model"] = stamp_model
            if node_id:
                form_data["node_id"] = node_id
            if is_correction_mode:
                form_data["is_correction_mode"] = "true"

            resp = client.post(
                "/jobs",
                headers=self._headers(),
                data=form_data,
                timeout=self.upload_timeout,
                files={
                    "pdf": (document_name, pdf_file, "application/pdf"),
                    "blocks_file": ("blocks.json", blocks_bytes, "application/json"),
                },
            )
        logger.info(f"POST /jobs response: {resp.status_code}")
        if resp.status_code >= 400:
            logger.error(f"POST /jobs error response: {resp.text[:1000]}")
        self._handle_response_error(resp)
        data = resp.json()

        return JobInfo(
            id=data["id"],
            status=data["status"],
            progress=data["progress"],
            document_id=data["document_id"],
            document_name=data["document_name"],
            task_name=data.get("task_name", ""),
        )
