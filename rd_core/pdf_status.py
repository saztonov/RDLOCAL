"""Утилиты для работы со статусами PDF документов"""
import json
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class PDFStatus(str, Enum):
    """Статус PDF документа"""

    COMPLETE = "complete"  # Все файлы есть, блоки размечены
    MISSING_FILES = "missing_files"  # Не хватает файлов
    MISSING_BLOCKS = "missing_blocks"  # Нет annotation или есть страницы без блоков
    UNKNOWN = "unknown"  # Статус неизвестен


def calculate_pdf_status(
    r2_storage, node_id: str, r2_key: str, check_blocks: bool = True
) -> tuple[PDFStatus, str]:
    """
    Вычислить статус PDF документа

    Args:
        r2_storage: Экземпляр R2Storage
        node_id: ID узла документа
        r2_key: R2 ключ PDF файла
        check_blocks: Проверять ли наличие блоков в аннотации

    Returns:
        Кортеж (статус, сообщение)
    """
    from pathlib import PurePosixPath

    from app.tree_client import FileType, TreeClient

    if not r2_key:
        return PDFStatus.UNKNOWN, "Нет R2 ключа"

    try:
        client = TreeClient()

        # Формируем ключи для связанных файлов
        pdf_path = PurePosixPath(r2_key)
        pdf_stem = pdf_path.stem
        pdf_parent = str(pdf_path.parent)

        ocr_r2_key = f"{pdf_parent}/{pdf_stem}_ocr.html"
        res_r2_key = f"{pdf_parent}/{pdf_stem}_result.json"

        # Проверяем наличие файлов на R2 одним запросом list_objects
        r2_objects = r2_storage.list_objects_with_metadata(f"{pdf_parent}/")
        r2_keys = {obj["Key"] for obj in r2_objects}

        has_ocr_html_r2 = ocr_r2_key in r2_keys
        has_result_json_r2 = res_r2_key in r2_keys

        # Проверяем наличие аннотации в таблице annotations (Supabase)
        has_annotation = client.has_annotation_in_db(node_id)

        # Проверяем наличие файлов в node_files (Supabase)
        try:
            node_files = client.get_node_files(node_id)
            file_types_in_db = {nf.file_type for nf in node_files}
        except Exception as e:
            logger.error(f"Failed to get node files for {node_id}: {e}", exc_info=True)
            raise

        has_ocr_html_db = FileType.OCR_HTML in file_types_in_db
        has_result_json_db = FileType.RESULT_JSON in file_types_in_db

        # Проверяем блоки если требуется — загружаем из Supabase
        pages_without_blocks = []
        if check_blocks and has_annotation:
            try:
                ann_data = client.get_annotation_data_for_status(node_id)
                if ann_data:
                    # Поддержка двух форматов: {"pages": [...]} или просто [...]
                    if isinstance(ann_data, dict):
                        pages = ann_data.get("pages", [])
                    elif isinstance(ann_data, list):
                        pages = ann_data
                    else:
                        pages = []

                    for page in pages:
                        if isinstance(page, dict):
                            page_num = page.get("page_number", -1)
                            blocks = page.get("blocks", [])
                            if not blocks:
                                pages_without_blocks.append(page_num)
            except Exception as e:
                logger.error(f"Failed to check annotation blocks: {e}")

        # Определяем статус и сообщение
        missing_r2 = []
        missing_db = []

        if not has_ocr_html_r2:
            missing_r2.append("ocr.html")
        if not has_ocr_html_db:
            missing_db.append("ocr.html")
        if not has_result_json_r2:
            missing_r2.append("result.json")
        if not has_result_json_db:
            missing_db.append("result.json")

        # Приоритет 3: Нет аннотации или есть страницы без блоков
        if not has_annotation:
            return PDFStatus.MISSING_BLOCKS, "Нет аннотации в базе данных"
        elif pages_without_blocks:
            pages_str = ", ".join(str(p) for p in sorted(pages_without_blocks))
            return PDFStatus.MISSING_BLOCKS, f"Страницы без блоков: {pages_str}"
        # Приоритет 2: Не хватает файлов
        elif missing_r2 or missing_db:
            parts = []
            if missing_r2:
                parts.append(f"R2: {', '.join(missing_r2)}")
            if missing_db:
                parts.append(f"БД: {', '.join(missing_db)}")
            message = "Отсутствует:\n" + "\n".join(parts)
            return PDFStatus.MISSING_FILES, message
        # Приоритет 1: Всё в порядке
        else:
            return PDFStatus.COMPLETE, "Все файлы на месте, блоки размечены"

    except Exception as e:
        logger.error(f"Failed to calculate PDF status: {e}", exc_info=True)
        return PDFStatus.UNKNOWN, f"Ошибка проверки: {e}"


def update_pdf_status_in_db(
    client, node_id: str, status: PDFStatus, message: str = None
):
    """
    Обновить статус PDF в БД

    Args:
        client: TreeClient
        node_id: ID узла документа
        status: Статус
        message: Сообщение (опционально)
    """
    try:
        # Используем RPC функцию для обновления
        response = client._request(
            "post",
            "/rpc/update_pdf_status",
            json={"p_node_id": node_id, "p_status": status.value, "p_message": message},
        )
        logger.debug(f"Updated PDF status for {node_id}: {status.value}")
    except Exception as e:
        logger.error(f"Failed to update PDF status in DB: {e}")
