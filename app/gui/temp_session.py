"""Менеджер временных сессий для tree-документов.

Создаёт изолированные рабочие каталоги в системном temp,
удаляет их при закрытии документа. Safety check: cleanup
работает только внутри managed TEMP_ROOT.
"""

import logging
import shutil
import tempfile
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Корневая директория для всех temp-сессий
TEMP_ROOT = Path(tempfile.gettempdir()) / "rdlocal_tree_docs"


class TempSessionManager:
    """Единая точка создания и удаления temp-каталогов для tree-документов."""

    def __init__(self):
        self._root = TEMP_ROOT
        self._root.mkdir(parents=True, exist_ok=True)

    def create_workspace(self, node_id: str) -> Path:
        """Создать temp/<node_id>-<uuid>/, вернуть путь."""
        short_uuid = uuid.uuid4().hex[:8]
        workspace = self._root / f"{node_id}-{short_uuid}"
        workspace.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Temp workspace created: {workspace}")
        return workspace

    def get_pdf_path(self, workspace: Path, r2_key: str) -> Path:
        """Путь к PDF внутри workspace (сохраняет оригинальное имя)."""
        return workspace / Path(r2_key).name

    def cleanup(self, workspace) -> None:
        """Удалить workspace, но только если она лежит под TEMP_ROOT."""
        if workspace is None:
            return

        workspace = Path(workspace)

        # Safety: проверяем что workspace внутри managed root
        try:
            workspace.resolve().relative_to(self._root.resolve())
        except ValueError:
            logger.warning(
                f"Refusing to delete workspace outside managed root: {workspace}"
            )
            return

        if workspace.exists():
            try:
                shutil.rmtree(workspace, ignore_errors=True)
                logger.info(f"Temp workspace removed: {workspace}")
            except Exception as e:
                logger.warning(f"Failed to remove temp workspace {workspace}: {e}")


# Синглтон
_instance: TempSessionManager | None = None


def get_temp_session_manager() -> TempSessionManager:
    """Получить глобальный экземпляр TempSessionManager."""
    global _instance
    if _instance is None:
        _instance = TempSessionManager()
    return _instance
