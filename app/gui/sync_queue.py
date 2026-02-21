"""
Очередь отложенной синхронизации для работы офлайн
"""
import json
import logging
import threading
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class SyncOperationType(Enum):
    """Тип операции синхронизации"""
    UPLOAD_ANNOTATION = "upload_annotation"  # Legacy: загрузка annotation JSON в R2
    UPLOAD_FILE = "upload_file"
    UPDATE_NODE = "update_node"
    DELETE_FILE = "delete_file"
    SAVE_ANNOTATION = "save_annotation"  # Сохранение аннотации в Supabase


@dataclass
class SyncOperation:
    """Операция синхронизации"""
    id: str
    type: SyncOperationType
    timestamp: str
    local_path: Optional[str] = None
    r2_key: Optional[str] = None
    node_id: Optional[str] = None
    data: Optional[Dict] = None
    attempts: int = 0
    last_error: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Сериализовать в словарь"""
        result = asdict(self)
        result['type'] = self.type.value
        return result
    
    @staticmethod
    def from_dict(data: dict) -> 'SyncOperation':
        """Десериализовать из словаря"""
        data = data.copy()
        data['type'] = SyncOperationType(data['type'])
        return SyncOperation(**data)


class SyncQueue(QObject):
    """
    Очередь отложенной синхронизации
    
    Сигналы:
        - operation_added: операция добавлена в очередь (operation_id)
        - operation_synced: операция синхронизирована (operation_id)
        - operation_failed: операция не удалась (operation_id, error)
        - queue_empty: очередь опустела
    """
    
    operation_added = Signal(str)
    operation_synced = Signal(str)
    operation_failed = Signal(str, str)
    queue_empty = Signal()
    
    def __init__(self, queue_file: Path, parent=None):
        super().__init__(parent)
        self._queue_file = queue_file
        self._operations: List[SyncOperation] = []
        self._lock = threading.Lock()
        self._load_queue()
        
    def _load_queue(self):
        """Загрузить очередь из файла"""
        if not self._queue_file.exists():
            return
            
        try:
            with open(self._queue_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self._operations = [SyncOperation.from_dict(op) for op in data]
            logger.info(f"Загружена очередь синхронизации: {len(self._operations)} операций")
        except Exception as e:
            logger.error(f"Ошибка загрузки очереди синхронизации: {e}")
            
    def _save_queue(self):
        """Сохранить очередь в файл"""
        try:
            self._queue_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._queue_file, 'w', encoding='utf-8') as f:
                data = [op.to_dict() for op in self._operations]
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения очереди синхронизации: {e}")
            
    def add_operation(self, operation: SyncOperation):
        """
        Добавить операцию в очередь
        
        Args:
            operation: операция синхронизации
        """
        with self._lock:
            self._operations.append(operation)
            self._save_queue()
        
        logger.info(f"Добавлена операция в очередь: {operation.type.value} (id={operation.id})")
        self.operation_added.emit(operation.id)
        
    def get_pending_operations(self) -> List[SyncOperation]:
        """Получить список ожидающих операций"""
        with self._lock:
            return self._operations.copy()
            
    def remove_operation(self, operation_id: str):
        """
        Удалить операцию из очереди (после успешной синхронизации)
        
        Args:
            operation_id: ID операции
        """
        with self._lock:
            self._operations = [op for op in self._operations if op.id != operation_id]
            self._save_queue()
            
            if not self._operations:
                self.queue_empty.emit()
        
        logger.info(f"Операция удалена из очереди: {operation_id}")
        self.operation_synced.emit(operation_id)
        
    def mark_failed(self, operation_id: str, error_message: str):
        """
        Пометить операцию как неудавшуюся
        
        Args:
            operation_id: ID операции
            error_message: сообщение об ошибке
        """
        with self._lock:
            for op in self._operations:
                if op.id == operation_id:
                    op.attempts += 1
                    op.last_error = error_message
                    self._save_queue()
                    break
        
        logger.warning(f"Операция не удалась: {operation_id} - {error_message}")
        self.operation_failed.emit(operation_id, error_message)
        
    def get_operation(self, operation_id: str) -> Optional[SyncOperation]:
        """Получить операцию по ID"""
        with self._lock:
            for op in self._operations:
                if op.id == operation_id:
                    return op
        return None
        
    def clear(self):
        """Очистить очередь"""
        with self._lock:
            self._operations.clear()
            self._save_queue()
        logger.info("Очередь синхронизации очищена")
        self.queue_empty.emit()
        
    def size(self) -> int:
        """Получить размер очереди"""
        with self._lock:
            return len(self._operations)
            
    def is_empty(self) -> bool:
        """Проверить пуста ли очередь"""
        with self._lock:
            return len(self._operations) == 0


def get_sync_queue() -> SyncQueue:
    """Получить глобальный экземпляр очереди синхронизации"""
    global _sync_queue_instance
    if _sync_queue_instance is None:
        import platform
        import os
        
        system = platform.system()
        if system == "Windows":
            base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        else:
            base = Path.home() / ".config"
            
        queue_file = base / "RD" / "sync_queue.json"
        _sync_queue_instance = SyncQueue(queue_file)
    return _sync_queue_instance


_sync_queue_instance: Optional[SyncQueue] = None
