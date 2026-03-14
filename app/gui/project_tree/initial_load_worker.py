"""
Воркер для асинхронной загрузки дерева при старте.

Перемещает сетевые операции из UI-потока в фоновый поток,
предотвращая блокировку интерфейса при медленной сети.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    from app.tree_client import TreeClient

logger = logging.getLogger(__name__)

__all__ = ["InitialLoadWorker"]


class InitialLoadWorker(QThread):
    """
    Воркер для начальной загрузки дерева проектов.

    Выполняет последовательно:
    1. Загрузку корневых узлов
    2. Загрузку статистики дерева
    3. Загрузку PDF статусов для документов
    """

    # Сигналы для поэтапного обновления UI
    roots_loaded = Signal(list)  # корневые узлы
    stats_loaded = Signal(dict)  # статистика дерева
    statuses_loaded = Signal(dict)  # PDF статусы {node_id: (status, message)}
    error = Signal(str)  # сообщение об ошибке
    finished_all = Signal()  # загрузка завершена

    def __init__(self, client: "TreeClient", parent=None):
        super().__init__(parent)
        self.client = client
        self._doc_ids: List[str] = []
        self._running = True

    def set_doc_ids(self, doc_ids: List[str]) -> None:
        """
        Установить список ID документов для загрузки PDF статусов.

        Вызывается из UI потока после получения корневых узлов.
        """
        self._doc_ids = doc_ids

    def stop(self) -> None:
        """Остановить воркер."""
        self._running = False
        self.wait()

    def run(self) -> None:
        """Выполнить загрузку в фоновом потоке."""
        try:
            # 1. Загружаем корневые узлы
            if not self._running:
                return
            logger.debug("InitialLoadWorker: loading root nodes...")
            roots = self.client.get_root_nodes()
            self.roots_loaded.emit(roots)

            # 2. Загружаем статистику
            if not self._running:
                return
            logger.debug("InitialLoadWorker: loading stats...")
            stats = self.client.get_tree_stats()
            self.stats_loaded.emit(stats)

            # 3. Загружаем PDF статусы (если есть документы)
            if not self._running:
                return
            if self._doc_ids:
                logger.debug(f"InitialLoadWorker: loading PDF statuses for {len(self._doc_ids)} docs...")
                statuses = self.client.get_pdf_statuses_batch(self._doc_ids)
                self.statuses_loaded.emit(statuses)

            logger.debug("InitialLoadWorker: all loading complete")

        except Exception as e:
            logger.error(f"InitialLoadWorker error: {e}")
            self.error.emit(str(e))
        finally:
            self.finished_all.emit()
