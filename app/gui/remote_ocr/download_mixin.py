"""Методы скачивания для Remote OCR панели"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class DownloadMixin:
    """Миксин для скачивания результатов"""

    def _auto_download_result(self, job_id: str):
        """Запустить скачивание результата из R2 в папку текущего документа"""
        client = self._get_client()
        if client is None:
            return

        try:
            job_details = client.get_job_details(job_id)
            r2_prefix = job_details.get("r2_prefix")

            if not r2_prefix:
                logger.warning(f"Задача {job_id} не имеет r2_prefix")
                return

            # Получаем путь к текущему PDF из main_window
            pdf_path = getattr(self.main_window, "_current_pdf_path", None)
            if not pdf_path:
                logger.warning(
                    f"Нет открытого документа для сохранения результатов job {job_id}"
                )
                return

            pdf_path = Path(pdf_path)
            extract_dir = pdf_path.parent

            # Всегда скачиваем новые результаты (перезапуск OCR очищает старые)
            self._executor.submit(
                self._download_result_bg, job_id, r2_prefix, str(extract_dir)
            )

        except Exception as e:
            logger.error(f"Ошибка подготовки скачивания {job_id}: {e}")

    def _download_result_bg(self, job_id: str, r2_prefix: str, extract_dir: str):
        """Фоновое скачивание результата в папку текущего документа."""
        try:
            from rd_core.r2_metadata_cache import get_metadata_cache
            from rd_core.r2_storage import R2Storage

            r2 = R2Storage()

            extract_path = Path(extract_dir)
            extract_path.mkdir(parents=True, exist_ok=True)

            # Получаем информацию о задаче
            client = self._get_client()
            job_details = client.get_job_details(job_id) if client else {}
            doc_name = job_details.get("document_name", "result.pdf")
            doc_stem = Path(doc_name).stem

            # Получаем имя PDF из main_window
            pdf_path = getattr(self.main_window, "_current_pdf_path", None)
            pdf_stem = Path(pdf_path).stem if pdf_path else doc_stem

            actual_prefix = job_details.get("result_prefix") or r2_prefix

            # Инвалидируем кэш метаданных для префикса перед скачиванием
            get_metadata_cache().invalidate_prefix(actual_prefix + "/")
            logger.debug(f"Invalidated metadata cache for prefix: {actual_prefix}/")

            # Скачиваем _annotation.json, _ocr.html, _result.json и _document.md
            files_to_download = [
                (f"{doc_stem}_annotation.json", f"{pdf_stem}_annotation.json"),
                (f"{doc_stem}_ocr.html", f"{pdf_stem}_ocr.html"),
                (f"{doc_stem}_result.json", f"{pdf_stem}_result.json"),
                (f"{doc_stem}_document.md", f"{pdf_stem}_document.md"),
            ]

            self._signals.download_started.emit(job_id, len(files_to_download))

            for idx, (remote_name, local_name) in enumerate(files_to_download, 1):
                self._signals.download_progress.emit(job_id, idx, local_name)
                remote_key = f"{actual_prefix}/{remote_name}"
                local_path = extract_path / local_name
                try:
                    # Проверяем без кэша (свежие данные с сервера)
                    if r2.exists(remote_key, use_cache=False):
                        # Скачиваем без дискового кэша (сервер мог обновить файл)
                        r2.download_file(remote_key, str(local_path))
                        logger.info(f"Скачан: {local_path}")
                    else:
                        logger.warning(f"Файл не найден: {remote_key}")
                except Exception as e:
                    logger.warning(f"Не удалось скачать {remote_key}: {e}")

            logger.info(f"✅ Результат скачан: {extract_dir}")
            self._signals.download_finished.emit(job_id, extract_dir)

        except Exception as e:
            logger.error(f"Ошибка скачивания {job_id}: {e}")
            self._signals.download_error.emit(job_id, str(e))
