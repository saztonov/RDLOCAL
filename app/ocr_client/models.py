"""Модели данных для Remote OCR клиента."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class JobInfo:
    """Информация о задаче OCR — совместим с _LocalJobInfo и JobsTableModel."""

    id: str = ""
    status: str = ""
    progress: float = 0.0
    document_id: str = ""
    document_name: str = ""
    task_name: str = ""
    created_at: str = ""
    updated_at: str = ""
    error_message: str | None = None
    node_id: str | None = None
    status_message: str | None = None
    priority: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> JobInfo:
        return cls(
            id=d.get("id", ""),
            status=d.get("status", ""),
            progress=d.get("progress", 0.0),
            document_id=d.get("document_id", ""),
            document_name=d.get("document_name", ""),
            task_name=d.get("task_name", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            error_message=d.get("error_message"),
            node_id=d.get("node_id"),
            status_message=d.get("status_message"),
            priority=d.get("priority", 0),
        )
