"""Кеш аннотаций с асинхронной синхронизацией в Supabase"""
import copy
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from rd_core.models import Document

logger = logging.getLogger(__name__)

# Максимальное число retry для одного node_id перед отказом
_MAX_RETRY_COUNT = 10
# После этого числа retry увеличиваем sync_delay
_BACKOFF_THRESHOLD = 5
_BACKOFF_SYNC_DELAY = 30.0


class AnnotationCache(QObject):
    """Кеш аннотаций с отложенной синхронизацией в Supabase"""

    # Сигналы
    synced = Signal(str)  # Когда аннотация синхронизирована с Supabase
    sync_failed = Signal(str, str)  # node_id, error
    sync_permanently_failed = Signal(str)  # node_id — все retry исчерпаны

    def __init__(self):
        super().__init__()
        self._cache: Dict[str, Document] = {}  # node_id -> Document
        self._dirty: Dict[str, float] = {}  # node_id -> last_modified_time
        self._metadata: Dict[str, dict] = {}  # node_id -> {pdf_path}
        self._retry_count: Dict[str, int] = {}  # node_id -> retry count
        self._lock = threading.Lock()  # Защита _dirty и _retry_count

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
        with self._lock:
            self._dirty[node_id] = time.time()

    def _check_sync(self):
        """Проверить, какие аннотации нужно синхронизировать с Supabase"""
        current_time = time.time()
        to_sync = []

        with self._lock:
            for node_id, modified_time in list(self._dirty.items()):
                if current_time - modified_time >= self._sync_delay:
                    to_sync.append(node_id)

        for node_id in to_sync:
            self._sync_to_db(node_id)

    def _sync_to_db(self, node_id: str):
        """Синхронизировать с Supabase (асинхронно)"""
        if node_id not in self._cache:
            return

        with self._lock:
            original_ts = self._dirty.pop(node_id, None)
            if original_ts is None:
                return

        document = self._cache[node_id]

        # Копируем для фонового потока
        doc_copy = copy.deepcopy(document)
        self._executor.submit(self._background_sync_db, node_id, doc_copy, original_ts)

    def _background_sync_db(self, node_id: str, doc: Document, original_ts: float):
        """Фоновая синхронизация с Supabase"""
        try:
            from app.annotation_db import AnnotationDBIO

            success = AnnotationDBIO.save_to_db(doc, node_id)
            if success:
                logger.info(f"Annotation synced to Supabase: {node_id}")
                with self._lock:
                    self._retry_count.pop(node_id, None)
                # Восстанавливаем sync_delay если был backoff
                if self._sync_delay > 3.0:
                    self._sync_delay = 3.0
                self.synced.emit(node_id)
                self._update_has_annotation_flag(node_id)
            else:
                self._handle_sync_failure(node_id, "Не удалось сохранить в Supabase")

        except Exception as e:
            logger.error(f"DB sync failed for {node_id}: {e}")
            self._handle_sync_failure(node_id, str(e))

    def _handle_sync_failure(self, node_id: str, error_msg: str):
        """Обработать неудачную синхронизацию с retry-лимитом"""
        with self._lock:
            count = self._retry_count.get(node_id, 0) + 1
            self._retry_count[node_id] = count

            if count >= _MAX_RETRY_COUNT:
                # Прекращаем retry — все попытки исчерпаны
                logger.error(
                    f"DB sync permanently failed for {node_id} after {count} attempts"
                )
                self._retry_count.pop(node_id, None)
                self.sync_permanently_failed.emit(node_id)
                return

            if count >= _BACKOFF_THRESHOLD:
                self._sync_delay = _BACKOFF_SYNC_DELAY

            # Ставим обратно в dirty для retry
            self._dirty[node_id] = time.time()

        logger.warning(
            f"DB sync failed for {node_id} (attempt {count}/{_MAX_RETRY_COUNT}), will retry"
        )
        self.sync_failed.emit(node_id, error_msg)

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
        with self._lock:
            if node_id in self._dirty:
                pass  # _sync_to_db сам удалит из dirty
            else:
                return
        self._sync_to_db(node_id)

    def force_sync_all(self):
        """Синхронизировать все несохраненные изменения"""
        with self._lock:
            nodes = list(self._dirty.keys())
        for node_id in nodes:
            self._sync_to_db(node_id)

    def clear(self, node_id: str):
        """Очистить кеш для узла"""
        self._cache.pop(node_id, None)
        with self._lock:
            self._dirty.pop(node_id, None)
            self._retry_count.pop(node_id, None)
        self._metadata.pop(node_id, None)


# Глобальный экземпляр
_annotation_cache: Optional[AnnotationCache] = None


def get_annotation_cache() -> AnnotationCache:
    """Получить глобальный кеш аннотаций"""
    global _annotation_cache
    if _annotation_cache is None:
        _annotation_cache = AnnotationCache()
    return _annotation_cache
