"""
Tree API Routes - прокси для операций с деревом проектов
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..node_storage import (
    add_node_file,
    create_node,
    delete_node,
    delete_node_file,
    get_children,
    get_node,
    get_node_files,
    get_root_nodes,
    update_node,
    update_pdf_status,
)

router = APIRouter(
    prefix="/api/tree", tags=["tree"]
)


# === Request/Response Models ===


class NodeResponse(BaseModel):
    id: str
    parent_id: Optional[str]
    node_type: str
    name: str
    code: Optional[str]
    status: str
    attributes: Dict[str, Any]
    sort_order: int
    version: int
    created_at: str
    updated_at: str


class CreateNodeRequest(BaseModel):
    node_type: str
    name: str
    parent_id: Optional[str] = None
    code: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None


class UpdateNodeRequest(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    status: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    sort_order: Optional[int] = None
    version: Optional[int] = None


class UpdatePDFStatusRequest(BaseModel):
    status: str
    message: Optional[str] = None


class AddNodeFileRequest(BaseModel):
    file_type: str
    r2_key: str
    file_name: str
    file_size: int = 0
    mime_type: str = "application/octet-stream"
    metadata: Optional[Dict[str, Any]] = None


class NodeFileResponse(BaseModel):
    id: str
    node_id: str
    file_type: str
    r2_key: str
    file_name: str
    file_size: int
    mime_type: str
    metadata: Dict[str, Any]
    created_at: str


# === Endpoints ===


@router.get("/nodes/root", response_model=List[NodeResponse])
def get_root_nodes_endpoint():
    """Получить корневые узлы (проекты)"""
    try:
        nodes = get_root_nodes()
        return [_node_to_dict(n) for n in nodes]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes/{node_id}", response_model=NodeResponse)
def get_node_endpoint(node_id: str):
    """Получить узел по ID"""
    try:
        node = get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")
        return _node_to_dict(node)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes/{node_id}/children", response_model=List[NodeResponse])
def get_children_endpoint(node_id: str):
    """Получить дочерние узлы"""
    try:
        children = get_children(node_id)
        return [_node_to_dict(n) for n in children]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nodes", response_model=NodeResponse)
def create_node_endpoint(req: CreateNodeRequest):
    """Создать новый узел"""
    try:
        node = create_node(
            node_type=req.node_type,
            name=req.name,
            parent_id=req.parent_id,
            code=req.code,
            attributes=req.attributes or {},
        )
        return _node_to_dict(node)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/nodes/{node_id}", response_model=NodeResponse)
def update_node_endpoint(node_id: str, req: UpdateNodeRequest):
    """Обновить узел"""
    try:
        fields = req.dict(exclude_unset=True)
        node = update_node(node_id, **fields)
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")
        return _node_to_dict(node)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/nodes/{node_id}")
def delete_node_endpoint(node_id: str):
    """Удалить узел"""
    try:
        success = delete_node(node_id)
        if not success:
            raise HTTPException(status_code=404, detail="Node not found")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nodes/{node_id}/pdf-status")
def update_pdf_status_endpoint(node_id: str, req: UpdatePDFStatusRequest):
    """Обновить статус PDF документа"""
    try:
        update_pdf_status(node_id, req.status, req.message)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes/{node_id}/files", response_model=List[NodeFileResponse])
def get_node_files_endpoint(node_id: str, file_type: Optional[str] = None):
    """Получить файлы узла"""
    try:
        files = get_node_files(node_id, file_type)
        return files
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nodes/{node_id}/files", response_model=NodeFileResponse)
def add_node_file_endpoint(node_id: str, req: AddNodeFileRequest):
    """Добавить файл к узлу"""
    try:
        file = add_node_file(
            node_id=node_id,
            file_type=req.file_type,
            r2_key=req.r2_key,
            file_name=req.file_name,
            file_size=req.file_size,
            mime_type=req.mime_type,
            metadata=req.metadata or {},
        )
        return file
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/files/{file_id}")
def delete_node_file_endpoint(file_id: str):
    """Удалить файл узла"""
    try:
        success = delete_node_file(file_id)
        if not success:
            raise HTTPException(status_code=404, detail="File not found")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# === Helper Functions ===


def _node_to_dict(node) -> Dict[str, Any]:
    """Конвертировать TreeNode в словарь"""
    return {
        "id": node.id,
        "parent_id": node.parent_id,
        "node_type": node.node_type.value
        if hasattr(node.node_type, "value")
        else node.node_type,
        "name": node.name,
        "code": node.code,
        "status": node.status.value if hasattr(node.status, "value") else node.status,
        "attributes": node.attributes,
        "sort_order": node.sort_order,
        "version": node.version,
        "created_at": node.created_at,
        "updated_at": node.updated_at,
    }
