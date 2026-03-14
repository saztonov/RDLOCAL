"""
Storage API Routes - прокси для операций с R2 Storage
Все запросы требуют X-API-Key аутентификацию
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..async_r2_storage import AsyncR2StorageSync
from ..routes.common import check_api_key

router = APIRouter(
    prefix="/api/storage", tags=["storage"], dependencies=[Depends(check_api_key)]
)


# === Request/Response Models ===


class ExistsResponse(BaseModel):
    exists: bool


class UploadTextRequest(BaseModel):
    content: str
    r2_key: str
    content_type: Optional[str] = None


class DeleteBatchRequest(BaseModel):
    keys: List[str]


class DeleteBatchResponse(BaseModel):
    deleted: List[str]
    errors: List[str]


class R2ObjectMetadata(BaseModel):
    key: str
    size: int
    last_modified: str
    content_type: Optional[str]


# === Endpoints ===


@router.get("/exists/{r2_key:path}", response_model=ExistsResponse)
def exists_endpoint(r2_key: str):
    """Проверить существование объекта"""
    try:
        r2 = AsyncR2StorageSync()
        exists = r2.exists(r2_key)
        return {"exists": exists}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download/{r2_key:path}")
def download_file_endpoint(r2_key: str):
    """Скачать файл (бинарный stream)"""
    try:
        r2 = AsyncR2StorageSync()

        # Для бинарных файлов используем presigned URL (эффективнее)
        presigned_url = r2.generate_presigned_url(r2_key, expiration=300)  # 5 минут
        if not presigned_url:
            raise HTTPException(status_code=404, detail="File not found")

        # Редирект на presigned URL
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url=presigned_url)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download-text/{r2_key:path}")
def download_text_endpoint(r2_key: str):
    """Скачать текстовый файл"""
    try:
        r2 = AsyncR2StorageSync()
        content = r2.download_text(r2_key)
        if content is None:
            raise HTTPException(status_code=404, detail="File not found")
        return {"content": content}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload/{r2_key:path}")
async def upload_file_endpoint(r2_key: str, file: UploadFile = File(...)):
    """Загрузить файл"""
    try:
        import os
        import tempfile

        # Сохраняем во временный файл
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        try:
            r2 = AsyncR2StorageSync()
            success = r2.upload_file(tmp_path, r2_key, content_type=file.content_type)

            if not success:
                raise HTTPException(status_code=500, detail="Upload failed")

            return {"ok": True, "r2_key": r2_key}
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload-text")
def upload_text_endpoint(req: UploadTextRequest):
    """Загрузить текстовый контент"""
    try:
        r2 = AsyncR2StorageSync()
        success = r2.upload_text(req.content, req.r2_key, req.content_type)

        if not success:
            raise HTTPException(status_code=500, detail="Upload failed")

        return {"ok": True, "r2_key": req.r2_key}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/delete-batch", response_model=DeleteBatchResponse)
def delete_batch_endpoint(req: DeleteBatchRequest):
    """Удалить несколько объектов батчем"""
    try:
        r2 = AsyncR2StorageSync()
        deleted = []
        errors = []

        for key in req.keys:
            success = r2.delete_object(key)
            if success:
                deleted.append(key)
            else:
                errors.append(key)

        return {"deleted": deleted, "errors": errors}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/{r2_key:path}")
def delete_object_endpoint(r2_key: str):
    """Удалить объект"""
    try:
        r2 = AsyncR2StorageSync()
        success = r2.delete_object(r2_key)

        if not success:
            raise HTTPException(status_code=404, detail="Object not found")

        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete-prefix/{prefix:path}")
def delete_by_prefix_endpoint(prefix: str):
    """Удалить все объекты с префиксом"""
    try:
        r2 = AsyncR2StorageSync()

        # Получаем список объектов
        objects = r2.list_objects(prefix)

        # Удаляем батчем
        deleted = []
        errors = []

        for key in objects:
            success = r2.delete_object(key)
            if success:
                deleted.append(key)
            else:
                errors.append(key)

        return {
            "deleted_count": len(deleted),
            "error_count": len(errors),
            "deleted": deleted,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list/{prefix:path}", response_model=List[str])
def list_files_endpoint(prefix: str):
    """Список файлов по префиксу (только ключи)"""
    try:
        r2 = AsyncR2StorageSync()
        keys = r2.list_objects(prefix)
        return keys
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list-metadata/{prefix:path}", response_model=List[R2ObjectMetadata])
def list_with_metadata_endpoint(prefix: str):
    """Список файлов с метаданными"""
    try:
        import os

        import boto3
        from botocore.config import Config

        # Для list с метаданными используем синхронный boto3
        account_id = os.getenv("R2_ACCOUNT_ID")
        endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

        bucket = os.getenv("R2_BUCKET_NAME", "rd1")
        response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)

        if "Contents" not in response:
            return []

        result = []
        for obj in response["Contents"]:
            result.append(
                {
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"].isoformat(),
                    "content_type": None,  # R2 не возвращает content-type в list
                }
            )

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
