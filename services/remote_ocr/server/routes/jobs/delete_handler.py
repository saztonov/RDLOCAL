"""Обработчик удаления задачи OCR"""
from typing import Optional

from fastapi import Header, HTTPException

from services.remote_ocr.server.logging_config import get_logger
from services.remote_ocr.server.routes.common import (
    check_api_key,
    get_r2_sync_client,
    require_job,
)
from services.remote_ocr.server.storage import (
    delete_job,
)

_logger = get_logger(__name__)


def delete_job_handler(
    job_id: str,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """Удалить задачу и все связанные файлы.

    ВАЖНО: Если у job есть node_id, файлы НЕ удаляются из R2,
    т.к. они зарегистрированы в node_files и принадлежат документу в дереве.
    """
    check_api_key(x_api_key)

    job = require_job(job_id)

    _logger.info(
        f"Удаление задачи: {job_id}",
        extra={
            "event": "job_lifecycle",
            "action": "delete",
            "job_id": job_id,
            "node_id": job.node_id,
            "status": job.status,
        },
    )

    # Если job привязан к node, файлы принадлежат node_files - НЕ удаляем из R2
    if job.node_id:
        _logger.info(
            f"Job {job_id} linked to node {job.node_id}, skipping R2 file deletion"
        )
    elif job.r2_prefix:
        # Legacy: job без node_id - удаляем файлы из R2
        try:
            s3_client, bucket_name = get_r2_sync_client()
            r2_prefix = (
                job.r2_prefix if job.r2_prefix.endswith("/") else f"{job.r2_prefix}/"
            )

            files_to_delete = []
            paginator = s3_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket_name, Prefix=r2_prefix):
                if "Contents" in page:
                    for obj in page["Contents"]:
                        files_to_delete.append({"Key": obj["Key"]})

            if files_to_delete:
                for i in range(0, len(files_to_delete), 1000):
                    batch = files_to_delete[i : i + 1000]
                    s3_client.delete_objects(
                        Bucket=bucket_name, Delete={"Objects": batch}
                    )
                _logger.info(
                    f"Deleted {len(files_to_delete)} files from R2 for job {job_id}"
                )
        except Exception as e:
            _logger.warning(f"Failed to delete files from R2: {e}")

    if not delete_job(job_id):
        raise HTTPException(
            status_code=500, detail="Failed to delete job from database"
        )

    return {"ok": True, "deleted_job_id": job_id}
