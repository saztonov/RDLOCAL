"""Модели данных Remote OCR клиента"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class JobInfo:
    """Информация о задаче"""

    id: str
    status: str
    progress: float
    document_id: str
    document_name: str
    task_name: str = ""
    created_at: str = ""
    updated_at: str = ""
    error_message: Optional[str] = None
    node_id: Optional[str] = None
    status_message: Optional[str] = None
    priority: int = 0
