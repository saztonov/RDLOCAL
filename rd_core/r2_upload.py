"""Операции загрузки в R2"""
import logging
from pathlib import Path
from typing import Optional

from rd_core.r2_disk_cache import get_disk_cache
from rd_core.r2_metadata_cache import get_metadata_cache
from rd_core.r2_errors import handle_r2_upload_error

logger = logging.getLogger(__name__)


class R2UploadMixin:
    """Миксин для операций загрузки в R2"""

    def upload_file(
        self, local_path: str, remote_key: str, content_type: Optional[str] = None
    ) -> bool:
        """
        Загрузить файл в R2

        Args:
            local_path: Локальный путь к файлу
            remote_key: Ключ объекта в R2 (путь в bucket)
            content_type: MIME тип (определяется автоматически если None)

        Returns:
            True если успешно, False при ошибке
        """
        try:
            local_file = Path(local_path)
            logger.debug(
                f"Попытка загрузки файла: {local_file} → {self.bucket_name}/{remote_key}"
            )

            if not local_file.exists():
                logger.error(f"❌ Файл не найден: {local_path}")
                return False

            file_size = local_file.stat().st_size
            logger.debug(f"Размер файла: {file_size} байт")

            # Определяем content_type если не указан
            if content_type is None:
                content_type = self._guess_content_type(local_file)

            logger.debug(f"Content-Type: {content_type}")

            # Загружаем файл
            extra_args = {}
            if content_type:
                extra_args["ContentType"] = content_type

            logger.debug(f"Начало загрузки в bucket '{self.bucket_name}'...")

            self.s3_client.upload_file(
                str(local_file),
                self.bucket_name,
                remote_key,
                ExtraArgs=extra_args,
                Config=self.transfer_config,
            )

            # Инвалидируем кэши после успешной загрузки
            get_metadata_cache().invalidate_key(remote_key)
            get_disk_cache().invalidate(remote_key)

            logger.info(f"✅ Файл загружен в R2: {remote_key} ({file_size} байт)")
            return True

        except Exception as e:
            handle_r2_upload_error(e, remote_key, local_path, content_type, "upload_file")
            return False

    def upload_directory(
        self, local_dir: str, remote_prefix: str = "", recursive: bool = True
    ) -> tuple[int, int]:
        """
        Загрузить директорию в R2

        Args:
            local_dir: Локальная директория
            remote_prefix: Префикс для объектов в R2
            recursive: Рекурсивная загрузка поддиректорий

        Returns:
            (успешно загружено, ошибок)
        """
        logger.info("=== Начало загрузки директории в R2 ===")
        logger.info(f"Локальная директория: {local_dir}")
        logger.info(f"Remote prefix: {remote_prefix}")
        logger.info(f"Recursive: {recursive}")

        local_path = Path(local_dir)
        if not local_path.is_dir():
            logger.error(f"❌ Директория не найдена: {local_dir}")
            return (0, 1)

        success_count = 0
        error_count = 0

        # Получаем список файлов
        if recursive:
            files = list(local_path.rglob("*"))
        else:
            files = list(local_path.glob("*"))

        files = [f for f in files if f.is_file()]

        logger.info(f"Найдено файлов для загрузки: {len(files)}")

        for idx, file_path in enumerate(files, 1):
            # Формируем remote_key с сохранением структуры
            relative_path = file_path.relative_to(local_path)
            remote_key = (
                f"{remote_prefix}/{relative_path.as_posix()}"
                if remote_prefix
                else relative_path.as_posix()
            )

            logger.info(f"[{idx}/{len(files)}] Загрузка: {relative_path.as_posix()}")

            if self.upload_file(str(file_path), remote_key):
                success_count += 1
            else:
                error_count += 1

        logger.info(
            f"=== Загрузка завершена: ✅ {success_count} успешно, ❌ {error_count} ошибок ==="
        )
        return (success_count, error_count)

    def upload_text(
        self, content: str, remote_key: str, content_type: str = None
    ) -> bool:
        """
        Загрузить текстовый контент в R2

        Args:
            content: Текстовое содержимое
            remote_key: Ключ объекта в R2
            content_type: MIME тип (auto для JSON по расширению)

        Returns:
            True если успешно
        """
        try:
            # Автоопределение content-type для JSON
            if content_type is None:
                if remote_key.endswith(".json"):
                    content_type = "application/json; charset=utf-8"
                else:
                    content_type = "text/plain; charset=utf-8"

            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=remote_key,
                Body=content.encode("utf-8"),
                ContentType=content_type,
            )

            # Инвалидируем кэши после успешной загрузки
            get_metadata_cache().invalidate_key(remote_key)
            get_disk_cache().invalidate(remote_key)

            logger.info(f"✅ Текст загружен в R2: {remote_key}")
            return True
        except Exception as e:
            handle_r2_upload_error(e, remote_key, None, content_type, "upload_text")
            return False
