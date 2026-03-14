"""Кеш аннотаций с асинхронной синхронизацией в Supabase"""
import copy
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from rd_core.models import Document

logger = logging.getLogger(__name__)


class AnnotationCache(QObject):
    """Кеш аннотаций с отложенной синхронизацией в Supabase"""

    # Сигналы
    synced = Signal(str)  # Когда аннотация синхронизирована с Supabase
    sync_failed = Signal(str, str)  # node_id, error

    def __init__(self):
        super().__init__()
        self._cache: Dict[str, Document] = {}  # node_id -> Document
        self._dirty: Dict[str, float] = {}  # node_id -> last_modified_time
        self._metadata: Dict[str, dict] = {}  # node_id -> {pdf_path}

        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(False)
        self._sync_timer.timeout.connect(self._check_sync)
        self._sync_timer.start(1000)  # Проверка каждую секунду

        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ann_sync")
        self._sync_delay = 3.0  # Синхронизация через 3 секунды после последнего изменения

    def set(self, node_id: str, document: Document, pdf_path: str = ""):
        """Сохранить аннотацию в кеш"""
        self._cache[node_id] = document
        self._metadata[node_id] = {"pdf_path": pdf_path}

    def get(self, node_id: str) -> Optional[Document]:
        """Получить аннотацию из кеша"""
        return self._cache.get(node_id)

    def mark_dirty(self, node_id: str):
        """Пометить аннотацию как измененную"""
        if node_id not in self._cache:
            return
        self._dirty[node_id] = time.time()

    def _check_sync(self):
        """Проверить, какие аннотации нужно синхронизировать с Supabase"""
        current_time = time.time()
        to_sync = []

        for node_id, modified_time in list(self._dirty.items()):
            if current_time - modified_time >= self._sync_delay:
                to_sync.append(node_id)

        for node_id in to_sync:
            self._sync_to_db(node_id)

    def _sync_to_db(self, node_id: str):
        """Синхронизировать с Supabase (асинхронно)"""
        if node_id not in self._cache:
            return

        del self._dirty[node_id]

        document = self._cache[node_id]

        # Проверяем офлайн статус
        if self._is_offline():
            self._add_to_sync_queue(node_id, document)
            logger.debug(f"Офлайн: добавлена в очередь синхронизация {node_id}")
            return

        # Копируем для фонового потока
        doc_copy = copy.deepcopy(document)
        self._executor.submit(self._background_sync_db, node_id, doc_copy)

    def _is_offline(self) -> bool:
        """Проверить, работаем ли мы в офлайн режиме"""
        try:
            from app.gui.main_window import MainWindow
            from PySide6.QtWidgets import QApplication
            from app.gui.connection_manager import ConnectionStatus

            app = QApplication.instance()
            if app:
                for widget in app.topLevelWidgets():
                    if isinstance(widget, MainWindow):
                        if hasattr(widget, 'connection_manager'):
                            status = widget.connection_manager.get_status()
                            return status != ConnectionStatus.CONNECTED
            return False
        except Exception:
            return False

    def _background_sync_db(self, node_id: str, doc: Document):
        """Фоновая синхронизация с Supabase"""
        try:
            from app.annotation_db import AnnotationDBIO

            success = AnnotationDBIO.save_to_db(doc, node_id)
            if success:
                logger.info(f"Annotation synced to Supabase: {node_id}")
                self.synced.emit(node_id)
                self._update_has_annotation_flag(node_id)
            else:
                self._add_to_sync_queue(node_id, doc)
                self.sync_failed.emit(node_id, "Не удалось сохранить в Supabase")

        except Exception as e:
            logger.error(f"DB sync failed for {node_id}: {e}")
            self._add_to_sync_queue(node_id, doc)
            self.sync_failed.emit(node_id, str(e))

    def _add_to_sync_queue(self, node_id: str, document: Document):
        """Добавить операцию в очередь отложенной синхронизации"""
        try:
            from uuid import uuid4
            from datetime import datetime
            from rd_core.annotation_io import ANNOTATION_FORMAT_VERSION
            from app.gui.sync_queue import SyncOperation, SyncOperationType, get_sync_queue

            queue = get_sync_queue()

            # Проверяем, нет ли уже такой операции в очереди
            for op in queue.get_pending_operations():
                if (op.type == SyncOperationType.SAVE_ANNOTATION
                        and op.node_id == node_id):
                    # Обновляем данные в существующей операции
                    op.data = {
                        "annotation_data": document.to_dict(),
                        "format_version": ANNOTATION_FORMAT_VERSION,
                    }
                    logger.debug(f"Обновлена операция в очереди: {node_id}")
                    return

            data = document.to_dict()
            data["format_version"] = ANNOTATION_FORMAT_VERSION

            operation = SyncOperation(
                id=str(uuid4()),
                type=SyncOperationType.SAVE_ANNOTATION,
                timestamp=datetime.now().isoformat(),
                local_path="",
                r2_key="",
                node_id=node_id,
                data={
                    "annotation_data": data,
                    "format_version": ANNOTATION_FORMAT_VERSION,
                },
            )
            queue.add_operation(operation)
            logger.info(f"Добавлена операция в очередь: annotation для {node_id}")

        except Exception as e:
            logger.error(f"Ошибка добавления в очередь: {e}")

    def _update_has_annotation_flag(self, node_id: str):
        """Обновить флаг has_annotation в tree_nodes"""
        try:
            from app.tree_client import TreeClient

            client = TreeClient()
            node = client.get_node(node_id)
            if node and not node.attributes.get("has_annotation"):
                attrs = node.attributes.copy()
                attrs["has_annotation"] = True
                client.update_node(node_id, attributes=attrs)

        except Exception as e:
            logger.debug(f"Update has_annotation failed: {e}")

    def force_sync(self, node_id: str):
        """Принудительно синхронизировать с Supabase"""
        if node_id in self._dirty:
            self._sync_to_db(node_id)

    def force_sync_all(self):
        """Синхронизировать все несохраненные изменения"""
        for node_id in list(self._dirty.keys()):
            self._sync_to_db(node_id)

    def clear(self, node_id: str):
        """Очистить кеш для узла"""
        self._cache.pop(node_id, None)
        self._dirty.pop(node_id, None)
        self._metadata.pop(node_id, None)


# Глобальный экземпляр
_annotation_cache: Optional[AnnotationCache] = None


def get_annotation_cache() -> AnnotationCache:
    """Получить глобальный кеш аннотаций"""
    global _annotation_cache
    if _annotation_cache is None:
        _annotation_cache = AnnotationCache()
    return _annotation_cache
