"""Общая логика для sync/async Datalab бэкендов."""
import json
import logging
import os
import tempfile
from typing import Optional, Tuple

from PIL import Image

from rd_core.ocr_result import make_error

logger = logging.getLogger(__name__)

API_URL = "https://www.datalab.to/api/v1/convert"
MAX_WIDTH = 4000
DEFAULT_POLL_INTERVAL = 3
DEFAULT_POLL_MAX_ATTEMPTS = 90
DEFAULT_MAX_RETRIES = 3
DEFAULT_QUALITY_THRESHOLD = 2.0


def init_params(
    api_key: str,
    poll_interval: Optional[int],
    poll_max_attempts: Optional[int],
    max_retries: Optional[int],
    extras: Optional[str],
    quality_threshold: Optional[float],
) -> Tuple[int, int, int, Optional[str], float]:
    """Нормализация параметров __init__."""
    if not api_key:
        raise ValueError("DATALAB_API_KEY не указан")
    return (
        poll_interval if poll_interval is not None else DEFAULT_POLL_INTERVAL,
        poll_max_attempts if poll_max_attempts is not None else DEFAULT_POLL_MAX_ATTEMPTS,
        max_retries if max_retries is not None else DEFAULT_MAX_RETRIES,
        extras or None,
        quality_threshold if quality_threshold is not None else DEFAULT_QUALITY_THRESHOLD,
    )


def prepare_source(
    image: Optional[Image.Image], pdf_file_path: Optional[str]
) -> Optional[Tuple[str, str, bool]]:
    """Подготовить источник. Возвращает (tmp_path, mime_type, need_cleanup) или None."""
    if pdf_file_path and os.path.exists(pdf_file_path):
        logger.info(f"Datalab: используем PDF ввод: {pdf_file_path}")
        return pdf_file_path, "application/pdf", False
    elif image is not None:
        # Resize если нужно
        if image.width > MAX_WIDTH:
            ratio = MAX_WIDTH / image.width
            new_width = MAX_WIDTH
            new_height = int(image.height * ratio)
            logger.info(f"Сжатие изображения {image.width}x{image.height} -> {new_width}x{new_height}")
            image = image.resize((new_width, new_height), Image.LANCZOS)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            image.save(tmp, format="PNG")
            return tmp.name, "image/png", True
    return None


def build_request_data(extras: Optional[str], skip_cache: bool = False) -> dict:
    """Собрать данные для POST запроса."""
    data = {
        "mode": "accurate",
        "paginate": "true",
        "output_format": "html",
        "disable_image_extraction": "true",
        "disable_image_captions": "true",
        "additional_config": json.dumps({"keep_pageheader_in_output": True}),
    }
    if extras:
        data["extras"] = extras
    if skip_cache:
        data["skip_cache"] = "true"
    return data


def handle_http_error(status_code: int, response_text: str) -> str:
    """Обработка HTTP ошибок."""
    logger.error(f"Datalab API error: {status_code} - {response_text}")
    if status_code == 401:
        return make_error("Datalab API 401: Неверный или просроченный DATALAB_API_KEY")
    elif status_code == 403:
        return make_error("Datalab API 403: Доступ запрещён")
    return make_error(f"Datalab API: {status_code}")


def handle_immediate_result(result: dict) -> Optional[str]:
    """Обработка немедленного результата (без polling). Возвращает None если нужен polling."""
    if not result.get("success"):
        error = result.get("error", "Unknown error")
        return make_error(f"Datalab: {error}")

    check_url = result.get("request_check_url")
    if not check_url:
        if "json" in result:
            json_result = result["json"]
            if isinstance(json_result, dict):
                return json.dumps(json_result, ensure_ascii=False)
            return json_result
        return make_error("нет request_check_url")
    return None  # нужен polling


def handle_poll_complete(poll_result: dict) -> Tuple[Optional[str], Optional[float]]:
    """Обработка complete статуса polling. Возвращает (html, quality_score)."""
    quality = poll_result.get("parse_quality_score")
    runtime = poll_result.get("runtime")
    logger.info(
        f"Datalab: задача успешно завершена"
        f"{f', quality={quality}' if quality is not None else ''}"
        f"{f', runtime={runtime}ms' if runtime is not None else ''}"
    )
    logger.debug(f"Datalab: ключи ответа: {list(poll_result.keys())}")
    html_result = poll_result.get("html", "")
    return (html_result if html_result else ""), quality
