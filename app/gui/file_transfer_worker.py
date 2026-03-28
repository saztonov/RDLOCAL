"""
Асинхронные операции загрузки/скачивания файлов
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class TransferType(Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"


@dataclass
class TransferTask:
    """Задача на передачу файла"""

    transfer_type: TransferType
    local_path: str
    r2_key: str
    node_id: str = ""
    file_size: int = 0
    filename: str = ""
    parent_node_id: str = ""  # Для upload - ID родительской папки
    timeout: int = 60  # Таймаут скачивания в секундах
    use_cache: bool = True  # Использовать R2DiskCache при скачивании


class FileTransferWorker(QThread):
    """Worker для параллельной загрузки/скачивания файлов"""

    # Сигналы
    progress = Signal(str, int, int)  # message, current, total
    finished_task = Signal(TransferTask, bool, str)  # task, success, error_message
    all_finished = Signal()

    def __init__(self, parent=None, max_workers: int = 8):
        super().__init__(parent)
        self._tasks: list[TransferTask] = []
        self._running = True
        self.max_workers = max_workers
        self._completed = 0
        self._lock = threading.Lock()

    def add_task(self, task: TransferTask):
        """Добавить задачу в очередь"""
        self._tasks.append(task)

    def _process_single_task(
        self, task: TransferTask, r2: "R2Storage"
    ) -> tuple[bool, str]:
        """Обработать одну задачу (вызывается в отдельном потоке)"""
        if not self._running:
            return False, "Отменено"

        try:
            if task.transfer_type == TransferType.UPLOAD:
                display_name = task.filename or Path(task.local_path).name
                success = r2.upload_file(task.local_path, task.r2_key)
                error = "" if success else "Ошибка загрузки в R2"
            else:  # DOWNLOAD
                display_name = Path(task.r2_key).name
                if task.timeout < 60:
                    # Короткий таймаут для вспомогательных файлов (OCR результаты)
                    from concurrent.futures import ThreadPoolExecutor as _TPE
                    from concurrent.futures import TimeoutError as _TE

                    with _TPE(max_workers=1) as mini:
                        fut = mini.submit(
                            r2.download_file,
                            task.r2_key,
                            task.local_path,
                            use_cache=task.use_cache,
                        )
                        try:
                            success = fut.result(timeout=task.timeout)
                            error = "" if success else "Ошибка скачивания из R2"
                        except _TE:
                            success = False
                            error = f"Таймаут скачивания ({task.timeout}с)"
                            logger.warning(f"Download timeout ({task.timeout}s): {task.r2_key}")
                else:
                    success = r2.download_file(
                        task.r2_key, task.local_path, use_cache=task.use_cache
                    )
                    error = "" if success else "Ошибка скачивания из R2"

            # Обновляем счётчик завершённых задач
            with self._lock:
                self._completed += 1
                current = self._completed

            # Отправляем прогресс
            total = len(self._tasks)
            action = (
                "Загрузка"
                if task.transfer_type == TransferType.UPLOAD
                else "Скачивание"
            )
            self.progress.emit(f"{action}: {display_name}", current, total)

            return success, error

        except Exception as e:
            logger.exception(f"Transfer error: {e}")
            return False, str(e)

    def run(self):
        """Выполнить все задачи параллельно"""
        from rd_core.r2_storage import R2Storage

        try:
            r2 = R2Storage()
        except Exception as e:
            for task in self._tasks:
                self.finished_task.emit(task, False, f"R2 ошибка: {e}")
            self.all_finished.emit()
            return

        self._completed = 0

        # Параллельная обработка задач
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Отправляем все задачи
            futures = {
                executor.submit(self._process_single_task, task, r2): task
                for task in self._tasks
            }

            # Ждём завершения
            for future in as_completed(futures):
                if not self._running:
                    # Отменяем оставшиеся
                    for f in futures:
                        f.cancel()
                    break

                task = futures[future]
                try:
                    success, error = future.result()
                    self.finished_task.emit(task, success, error)
                except Exception as e:
                    logger.exception(f"Task execution failed: {e}")
                    self.finished_task.emit(task, False, str(e))

        self.all_finished.emit()

    def stop(self):
        """Остановить обработку"""
        self._running = False
