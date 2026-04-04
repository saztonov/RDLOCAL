"""Annotations API — загрузка и сохранение аннотаций документов."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import verify_token
from ..logging_config import get_logger
from ..node_storage.ocr_registry import _load_annotation_from_db, _save_annotation_to_db

logger = get_logger(__name__)

router = APIRouter(prefix="/api/annotations", tags=["annotations"])


class SaveAnnotationRequest(BaseModel):
    data: Dict[str, Any]


@router.get("/{node_id}")
def get_annotation(
    node_id: str,
    _user: str = Depends(verify_token),
) -> dict:
    """Получить аннотацию документа по node_id."""
    ann_data = _load_annotation_from_db(node_id)
    if ann_data is None:
        raise HTTPException(status_code=404, detail="Annotation not found")
    return {"node_id": node_id, "data": ann_data}


@router.put("/{node_id}")
def save_annotation(
    node_id: str,
    body: SaveAnnotationRequest,
    _user: str = Depends(verify_token),
) -> dict:
    """Сохранить аннотацию документа (upsert)."""
    success = _save_annotation_to_db(node_id, body.data)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save annotation")
    return {"ok": True, "node_id": node_id}
