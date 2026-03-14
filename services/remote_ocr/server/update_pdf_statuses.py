"""
Скрипт для обновления статусов PDF документов
Запускается по cron ежедневно в 02:00
"""
import os
import sys
from pathlib import Path

# Standalone cron-скрипт: rd_core не установлен как пакет,
# поэтому добавляем корневую директорию проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from rd_core.pdf_status import calculate_pdf_status
from rd_core.r2_storage import R2Storage

from .logging_config import get_logger

logger = get_logger(__name__)


def update_all_pdf_statuses():
    """Обновить статусы всех PDF документов"""
    import httpx

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL or SUPABASE_KEY not set")
        return

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
    }

    try:
        r2 = R2Storage()

        # Получаем все документы
        response = httpx.get(
            f"{supabase_url}/rest/v1/tree_nodes",
            params={"node_type": "eq.document", "select": "id,attributes"},
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        documents = response.json()

        logger.info(f"Found {len(documents)} documents to check")

        updated_count = 0
        error_count = 0

        for doc in documents:
            node_id = doc["id"]
            r2_key = doc.get("attributes", {}).get("r2_key", "")

            if not r2_key:
                continue

            try:
                # Вычисляем статус
                status, message = calculate_pdf_status(
                    r2, node_id, r2_key, check_blocks=True
                )

                # Обновляем в БД через RPC функцию
                rpc_response = httpx.post(
                    f"{supabase_url}/rest/v1/rpc/update_pdf_status",
                    json={
                        "p_node_id": node_id,
                        "p_status": status.value,
                        "p_message": message,
                    },
                    headers=headers,
                    timeout=10.0,
                )
                rpc_response.raise_for_status()

                updated_count += 1
                logger.debug(f"Updated {node_id}: {status.value}")

            except Exception as e:
                logger.error(f"Failed to update status for {node_id}: {e}")
                error_count += 1

        logger.info(f"Update complete: {updated_count} updated, {error_count} errors")

    except Exception as e:
        logger.error(f"Failed to update PDF statuses: {e}")
        raise


if __name__ == "__main__":
    update_all_pdf_statuses()
