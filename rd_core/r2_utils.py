"""Утилиты для работы с R2"""
import logging
from pathlib import Path
from typing import Optional

from botocore.exceptions import ClientError

from rd_core.r2_disk_cache import get_disk_cache
from rd_core.r2_metadata_cache import get_metadata_cache

logger = logging.getLogger(__name__)


def invalidate_r2_cache(key: str, *, prefix: bool = False) -> None:
    """Инвалидировать R2 кэши (metadata + disk) для ключа или префикса.

    Единая точка инвалидации вместо дублирования в 3+ местах.
    """
    if prefix:
        get_metadata_cache().invalidate_prefix(key)
        get_disk_cache().invalidate_prefix(key)
    else:
        get_metadata_cache().invalidate_key(key)
        get_disk_cache().invalidate(key)


class R2UtilsMixin:
    """Миксин для утилит R2"""

    def _guess_content_type(self, file_path: Path) -> str:
        """Определить MIME тип по расширению"""
        extension = file_path.suffix.lower()

        content_types = {
            ".pdf": "application/pdf",
            ".json": "application/json",
            ".md": "text/markdown",
            ".txt": "text/plain",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }

        return content_types.get(extension, "application/octet-stream")

    def delete_object(self, remote_key: str) -> bool:
        """
        Удалить объект из R2

        Args:
            remote_key: Ключ объекта

        Returns:
            True если успешно
        """
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=remote_key)
            invalidate_r2_cache(remote_key)
            logger.info(f"✅ Объект удален из R2: {remote_key}")
            return True

        except ClientError as e:
            logger.error(f"❌ Ошибка удаления объекта: {e}")
            return False

    def rename_object(self, old_key: str, new_key: str) -> bool:
        """
        Переименовать объект в R2 (копирование + удаление)

        Args:
            old_key: Старый ключ объекта
            new_key: Новый ключ объекта

        Returns:
            True если успешно
        """
        try:
            # Копируем объект с новым ключом
            copy_source = {"Bucket": self.bucket_name, "Key": old_key}
            self.s3_client.copy_object(
                Bucket=self.bucket_name, CopySource=copy_source, Key=new_key
            )
            logger.info(f"✅ Объект скопирован: {old_key} → {new_key}")

            # Удаляем старый объект
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=old_key)
            logger.info(f"✅ Старый объект удален: {old_key}")

            # Инвалидируем кэши для обоих ключей
            metadata_cache = get_metadata_cache()
            metadata_cache.invalidate_key(old_key)
            metadata_cache.invalidate_key(new_key)

            disk_cache = get_disk_cache()
            disk_cache.invalidate(old_key)
            disk_cache.invalidate(new_key)

            return True

        except ClientError as e:
            logger.error(f"❌ Ошибка переименования объекта: {e}")
            return False

    def generate_presigned_url(
        self, remote_key: str, expiration: int = 3600
    ) -> Optional[str]:
        """
        Создать временную ссылку на объект

        Args:
            remote_key: Ключ объекта
            expiration: Время жизни ссылки в секундах

        Returns:
            URL или None при ошибке
        """
        try:
            url = self.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": remote_key},
                ExpiresIn=expiration,
            )
            return url

        except ClientError as e:
            logger.error(f"Ошибка генерации presigned URL: {e}")
            return None

    def list_by_prefix(self, prefix: str) -> list[str]:
        """
        Получить список ключей с определенным префиксом

        Args:
            prefix: Префикс для поиска

        Returns:
            Список ключей
        """
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name, Prefix=prefix
            )

            if "Contents" not in response:
                return []

            return [obj["Key"] for obj in response["Contents"]]
        except ClientError as e:
            logger.error(f"❌ Ошибка получения списка из R2: {e}")
            return []

    # Алиас для совместимости
    list_files = list_by_prefix

    def delete_by_prefix(self, prefix: str) -> int:
        """
        Удалить все объекты с заданным префиксом (использует пакетное удаление)

        Args:
            prefix: Префикс для удаления

        Returns:
            Количество удалённых объектов
        """
        keys = self.list_by_prefix(prefix)
        if not keys:
            return 0

        # Используем пакетное удаление вместо цикла
        deleted_keys, errors = self.delete_objects_batch(keys)

        logger.info(
            f"✅ Удалено {len(deleted_keys)}/{len(keys)} объектов по префиксу: {prefix}"
        )
        if errors:
            logger.warning(f"⚠️ Ошибок при удалении по префиксу: {len(errors)}")

        return len(deleted_keys)

    def list_objects_with_metadata(
        self, prefix: str, use_cache: bool = True
    ) -> list[dict]:
        """
        Получить список объектов с метаданными (LastModified, Size, ETag)

        Args:
            prefix: Префикс для поиска
            use_cache: Использовать кэш (по умолчанию True)

        Returns:
            Список dict с ключами: Key, LastModified, Size, ETag
        """
        cache = get_metadata_cache()

        # Проверяем кэш
        if use_cache:
            cached = cache.get_list(prefix)
            if cached is not None:
                logger.debug(f"Cache hit for list_objects: {prefix}")
                return cached

        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name, Prefix=prefix
            )

            if "Contents" not in response:
                result = []
            else:
                result = [
                    {
                        "Key": obj["Key"],
                        "LastModified": obj.get("LastModified"),
                        "Size": obj.get("Size", 0),
                        "ETag": obj.get("ETag", "").strip('"'),
                    }
                    for obj in response["Contents"]
                ]

            # Сохраняем в кэш
            cache.set_list(prefix, result)

            # Также обновляем exists кэш для найденных ключей
            for obj in result:
                cache.set_exists(obj["Key"], True)

            return result

        except ClientError as e:
            logger.error(f"❌ Ошибка получения списка из R2: {e}")
            return []

    def exists(self, remote_key: str, use_cache: bool = True) -> bool:
        """
        Проверить существование объекта в R2

        Args:
            remote_key: Ключ объекта
            use_cache: Использовать кэш (по умолчанию True)

        Returns:
            True если объект существует
        """
        cache = get_metadata_cache()

        # Проверяем кэш
        if use_cache:
            cached = cache.get_exists(remote_key)
            if cached is not None:
                logger.debug(f"Cache hit for exists: {remote_key} = {cached}")
                return cached

        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=remote_key)
            cache.set_exists(remote_key, True)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                cache.set_exists(remote_key, False)
                return False
            logger.error(f"❌ Ошибка проверки существования объекта: {e}")
            return False

    def delete_objects_batch(self, keys: list[str]) -> tuple[list[str], list[dict]]:
        """
        Пакетное удаление объектов из R2 (до 1000 за раз)

        Args:
            keys: Список ключей для удаления

        Returns:
            Кортеж: (список успешно удаленных ключей, список ошибок)
            Ошибка - dict с полями: Key, Code, Message
        """
        if not keys:
            return [], []

        deleted = []
        errors = []

        # AWS S3 API позволяет удалить до 1000 объектов за раз
        batch_size = 1000

        for i in range(0, len(keys), batch_size):
            batch = keys[i : i + batch_size]

            try:
                delete_dict = {
                    "Objects": [{"Key": key} for key in batch],
                    "Quiet": False,  # Получать информацию об удалённых объектах
                }

                response = self.s3_client.delete_objects(
                    Bucket=self.bucket_name, Delete=delete_dict
                )

                # Обрабатываем успешно удалённые
                if "Deleted" in response:
                    for obj in response["Deleted"]:
                        deleted.append(obj["Key"])

                # Обрабатываем ошибки
                if "Errors" in response:
                    for error in response["Errors"]:
                        errors.append(
                            {
                                "Key": error.get("Key", ""),
                                "Code": error.get("Code", ""),
                                "Message": error.get("Message", ""),
                            }
                        )
                        logger.warning(
                            f"❌ Ошибка удаления {error.get('Key')}: "
                            f"{error.get('Code')} - {error.get('Message')}"
                        )

                logger.info(
                    f"✅ Пакет {i//batch_size + 1}: удалено {len(response.get('Deleted', []))} объектов"
                )

            except ClientError as e:
                logger.error(f"❌ Ошибка пакетного удаления: {e}")
                # Добавляем все ключи из этого батча в ошибки
                for key in batch:
                    errors.append(
                        {"Key": key, "Code": "ClientError", "Message": str(e)}
                    )

        logger.info(f"✅ Всего удалено {len(deleted)}/{len(keys)} объектов")
        if errors:
            logger.warning(f"⚠️ Ошибок при удалении: {len(errors)}")

        # Инвалидируем кэши для удалённых ключей
        if deleted:
            metadata_cache = get_metadata_cache()
            disk_cache = get_disk_cache()
            for key in deleted:
                metadata_cache.invalidate_key(key)
                disk_cache.invalidate(key)

        return deleted, errors
