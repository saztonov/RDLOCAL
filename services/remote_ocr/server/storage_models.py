"""Модели данных для хранилища задач OCR"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class JobFile:
    id: str
    job_id: str  # Ссылка на задачу (каскадное удаление при удалении job)
    file_type: str  # pdf|blocks|annotation|result_md|result_zip|crop|ocr_html|result
    r2_key: str
    file_name: str
    file_size: int
    created_at: str
    metadata: Optional[dict] = None  # Для кропов: block_id, page_index, coords_norm, block_type


@dataclass
class JobSettings:
    job_id: str
    text_model: str = ""
    image_model: str = ""
    stamp_model: str = ""
    is_correction_mode: bool = False


@dataclass
class Job:
    id: str
    document_id: str
    document_name: str
    task_name: str
    status: str  # draft|queued|processing|done|error|paused
    progress: float
    created_at: str
    updated_at: str
    error_message: Optional[str]
    engine: str
    r2_prefix: str
    client_id: str  # Идентификатор клиента (из ~/.config/CoreStructure/client_id.txt)
    node_id: Optional[str] = None  # ID узла дерева (для связи с деревом проектов)
    status_message: Optional[str] = None  # Детальное сообщение о прогрессе
    started_at: Optional[str] = None  # Время начала обработки
    completed_at: Optional[str] = None  # Время завершения обработки
    retry_count: int = 0  # Количество попыток выполнения (защита от зацикливания)
    block_stats: Optional[dict] = None  # Статистика блоков
    phase_data: Optional[dict] = None  # Данные о фазах OCR
    priority: int = 0  # Приоритет в очереди (меньше = раньше)
    celery_task_id: Optional[str] = None  # ID Celery задачи для revoke при reorder
    # Вложенные данные (опционально загружаются)
    settings: Optional[JobSettings] = None
