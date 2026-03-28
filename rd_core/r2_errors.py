"""Централизованная обработка ошибок R2 Storage"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class R2ErrorCode(Enum):
    """Известные коды ошибок R2"""
    NOT_FOUND = "NoSuchKey"
    NOT_FOUND_404 = "404"
    TIMEOUT = "RequestTimeout"
    SERVICE_UNAVAILABLE = "ServiceUnavailable"
    UNKNOWN = "Unknown"


@dataclass
class R2ErrorResult:
    """Результат классификации ошибки R2"""
    error_code: str
    error_message: str
    is_retryable: bool
    should_queue: bool


def classify_client_error(e: ClientError) -> R2ErrorResult:
    """
    Классифицировать ClientError и определить стратегию обработки.

    Args:
        e: ClientError от botocore

    Returns:
        R2ErrorResult с информацией об ошибке
    """
    error_code = e.response.get("Error", {}).get("Code", "Unknown")
    error_message = e.response.get("Error", {}).get("Message", str(e))

    # Ошибки "не найдено"
    if error_code in (R2ErrorCode.NOT_FOUND.value, R2ErrorCode.NOT_FOUND_404.value):
        return R2ErrorResult(
            error_code=error_code,
            error_message=error_message,
            is_retryable=False,
            should_queue=False
        )

    # Сетевые ошибки - можно повторить
    if error_code in (R2ErrorCode.TIMEOUT.value, R2ErrorCode.SERVICE_UNAVAILABLE.value):
        return R2ErrorResult(
            error_code=error_code,
            error_message=error_message,
            is_retryable=True,
            should_queue=True
        )

    # Прочие ошибки
    return R2ErrorResult(
        error_code=error_code,
        error_message=error_message,
        is_retryable=False,
        should_queue=False
    )


def handle_r2_download_error(
    e: Exception,
    remote_key: str,
    operation: str = "download"
) -> bool:
    """
    Обработать ошибку R2 при скачивании.

    Args:
        e: Исключение
        remote_key: R2 ключ объекта
        operation: Название операции для логирования

    Returns:
        True если ошибка обработана (не критическая), False если критическая
    """
    if isinstance(e, ClientError):
        result = classify_client_error(e)

        if result.error_code in (R2ErrorCode.NOT_FOUND.value, R2ErrorCode.NOT_FOUND_404.value):
            logger.warning(f"⚠️ Файл не найден в R2: {remote_key}")
            return True

        if result.is_retryable:
            logger.warning(f"⚠️ Сетевая ошибка при {operation} из R2: {result.error_code}")
            return True

        logger.error(f"❌ Ошибка {operation} из R2: {result.error_code} - {result.error_message}")
        return False

    if isinstance(e, (ConnectionError, TimeoutError)):
        logger.warning(f"⚠️ Сетевая ошибка при {operation} из R2: {e}")
        return True

    logger.error(
        f"❌ Неожиданная ошибка {operation} из R2: {type(e).__name__}: {e}",
        exc_info=True
    )
    return False


def handle_r2_upload_error(
    e: Exception,
    remote_key: str,
    local_path: Optional[str] = None,
    content_type: Optional[str] = None,
    operation: str = "upload"
) -> bool:
    """
    Обработать ошибку R2 при загрузке.

    Args:
        e: Исключение
        remote_key: R2 ключ объекта
        local_path: Локальный путь к файлу (для логирования)
        content_type: MIME тип
        operation: Название операции

    Returns:
        True если ошибка обработана (не критическая), False если критическая
    """
    if isinstance(e, ClientError):
        result = classify_client_error(e)

        if result.is_retryable:
            logger.warning(f"⚠️ Сетевая ошибка при {operation} в R2: {result.error_message}")
            return True

        logger.error(f"❌ ClientError при {operation} в R2: {result.error_code} - {result.error_message}")
        if local_path:
            logger.error(f"   Файл: {local_path}")
        logger.error(f"   Key: {remote_key}")
        return False

    if isinstance(e, (ConnectionError, TimeoutError)):
        logger.warning(f"⚠️ Сетевая ошибка при {operation} в R2: {e}")
        return True

    logger.error(
        f"❌ Неожиданная ошибка {operation} в R2: {type(e).__name__}: {e}",
        exc_info=True
    )
    if local_path:
        logger.error(f"   Файл: {local_path}")
    logger.error(f"   Key: {remote_key}")
    return False
