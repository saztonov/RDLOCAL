"""Операции с аннотациями (таблица annotations)."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class TreeAnnotationsMixin:
    """Миксин для CRUD операций с аннотациями в Supabase"""

    def get_annotation(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Получить данные аннотации для узла.

        Returns:
            dict с полями {data, format_version, ...} или None
        """
        try:
            resp = self._request(
                "get",
                f"/annotations?node_id=eq.{node_id}&select=id,data,format_version",
            )
            rows = resp.json()
            if rows:
                return rows[0].get("data")
            return None
        except Exception as e:
            logger.error(f"get_annotation failed for {node_id}: {e}")
            return None

    def save_annotation(
        self, node_id: str, data: Dict[str, Any], format_version: int = 2
    ) -> bool:
        """Сохранить аннотацию (upsert по node_id).

        Args:
            node_id: ID узла документа
            data: Полная структура аннотации (pages → blocks)
            format_version: Версия формата

        Returns:
            True при успехе
        """
        try:
            payload = {
                "node_id": node_id,
                "data": data,
                "format_version": format_version,
                "updated_at": "now()",
            }

            # Проверяем существует ли запись
            resp = self._request(
                "get",
                f"/annotations?node_id=eq.{node_id}&select=id",
            )
            existing = resp.json()

            if existing:
                # UPDATE
                self._request(
                    "patch",
                    f"/annotations?node_id=eq.{node_id}",
                    json={"data": data, "format_version": format_version},
                )
            else:
                # INSERT
                payload["id"] = str(uuid.uuid4())
                self._request("post", "/annotations", json=payload)

            return True
        except Exception as e:
            logger.error(f"save_annotation failed for {node_id}: {e}")
            return False

    def delete_annotation(self, node_id: str) -> bool:
        """Удалить аннотацию узла."""
        try:
            self._request("delete", f"/annotations?node_id=eq.{node_id}")
            return True
        except Exception as e:
            logger.error(f"delete_annotation failed for {node_id}: {e}")
            return False

    def has_annotation_in_db(self, node_id: str) -> bool:
        """Проверить наличие аннотации в БД."""
        try:
            resp = self._request(
                "get",
                f"/annotations?node_id=eq.{node_id}&select=id&limit=1",
            )
            return bool(resp.json())
        except Exception as e:
            logger.debug(f"has_annotation_in_db check failed for {node_id}: {e}")
            return False

    def copy_annotation_between_nodes(
        self, source_node_id: str, target_node_id: str
    ) -> bool:
        """Скопировать аннотацию из одного узла в другой."""
        try:
            data = self.get_annotation(source_node_id)
            if data is None:
                return False
            return self.save_annotation(target_node_id, data)
        except Exception as e:
            logger.error(
                f"copy_annotation failed {source_node_id} -> {target_node_id}: {e}"
            )
            return False

    def get_annotation_data_for_status(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Получить данные аннотации для вычисления PDF статуса.

        Возвращает минимальные данные: только pages с количеством блоков.
        """
        try:
            resp = self._request(
                "get",
                f"/annotations?node_id=eq.{node_id}&select=data",
            )
            rows = resp.json()
            if rows:
                return rows[0].get("data")
            return None
        except Exception as e:
            logger.debug(f"get_annotation_data_for_status failed for {node_id}: {e}")
            return None
