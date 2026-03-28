"""
Модели для checkpoint/resume системы OCR.

Позволяет сохранять прогресс обработки и восстанавливать его после паузы.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class OCRCheckpoint:
    """
    Checkpoint для сохранения прогресса OCR обработки.

    Позволяет:
    - Сохранять состояние между PASS1 и PASS2
    - Восстанавливать обработку после паузы
    - Пропускать уже обработанные элементы
    """

    job_id: str
    phase: str  # "pass1", "pass2", "verification", "completed"

    # Обработанные блоки (block_id)
    processed_blocks: Set[str] = field(default_factory=set)

    # Результаты: block_id -> ocr_text
    partial_results: Dict[str, str] = field(default_factory=dict)

    # Метаданные
    manifest_path: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Статистика
    total_blocks: int = 0

    def is_block_processed(self, block_id: str) -> bool:
        """Проверить, обработан ли блок"""
        return block_id in self.processed_blocks

    def mark_block_processed(self, block_id: str, text: str):
        """Отметить блок как обработанный"""
        self.partial_results[block_id] = text
        self.processed_blocks.add(block_id)
        self.updated_at = datetime.utcnow().isoformat()

    def get_pending_blocks(self, all_block_ids: List[str]) -> List[str]:
        """Получить список необработанных блоков"""
        return [b for b in all_block_ids if b not in self.processed_blocks]

    def get_progress(self) -> Dict[str, float]:
        """Получить прогресс обработки"""
        if self.total_blocks == 0:
            return {"total": 0.0}
        return {"total": len(self.processed_blocks) / self.total_blocks}

    def save(self, path: Path) -> bool:
        """
        Сохранить checkpoint в файл.

        Использует атомарную запись (write to tmp, then rename).
        """
        try:
            data = {
                "job_id": self.job_id,
                "phase": self.phase,
                "processed_blocks": list(self.processed_blocks),
                "partial_results": self.partial_results,
                "manifest_path": self.manifest_path,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "total_blocks": self.total_blocks,
            }

            # Атомарная запись
            tmp_path = path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # Rename (атомарно на большинстве FS)
            os.replace(tmp_path, path)

            logger.debug(
                f"Checkpoint saved: {path}",
                extra={
                    "job_id": self.job_id,
                    "phase": self.phase,
                    "processed_blocks": len(self.processed_blocks),
                },
            )
            return True

        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}", exc_info=True)
            return False

    @classmethod
    def load(cls, path: Path) -> Optional["OCRCheckpoint"]:
        """Загрузить checkpoint из файла"""
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Backward-compatible: объединяем старые processed_strips + processed_images
            processed_blocks = set(data.get("processed_blocks", []))
            processed_blocks.update(data.get("processed_strips", []))
            processed_blocks.update(data.get("processed_images", []))

            checkpoint = cls(
                job_id=data["job_id"],
                phase=data["phase"],
                processed_blocks=processed_blocks,
                partial_results=data.get("partial_results", {}),
                manifest_path=data.get("manifest_path"),
                created_at=data.get("created_at", datetime.utcnow().isoformat()),
                updated_at=data.get("updated_at", datetime.utcnow().isoformat()),
                total_blocks=data.get(
                    "total_blocks",
                    data.get("total_strips", 0) + data.get("total_images", 0),
                ),
            )

            logger.info(
                f"Checkpoint loaded: {path}",
                extra={
                    "job_id": checkpoint.job_id,
                    "phase": checkpoint.phase,
                    "processed_blocks": len(checkpoint.processed_blocks),
                },
            )
            return checkpoint

        except Exception as e:
            logger.error(f"Failed to load checkpoint: {e}", exc_info=True)
            return None

    @classmethod
    def create_new(
        cls,
        job_id: str,
        total_blocks: int = 0,
        manifest_path: str = None,
    ) -> "OCRCheckpoint":
        """Создать новый checkpoint"""
        return cls(
            job_id=job_id,
            phase="pass1",
            total_blocks=total_blocks,
            manifest_path=manifest_path,
        )

    def apply_to_blocks(self, blocks: List) -> int:
        """
        Применить сохранённые результаты к блокам.

        Returns:
            Количество блоков с восстановленными результатами
        """
        applied = 0
        blocks_by_id = {b.id: b for b in blocks}

        for block_id, ocr_text in self.partial_results.items():
            if block_id in blocks_by_id:
                blocks_by_id[block_id].ocr_text = ocr_text
                applied += 1

        logger.info(
            f"Checkpoint applied: {applied} blocks restored",
            extra={"job_id": self.job_id},
        )
        return applied


def get_checkpoint_path(work_dir: Path) -> Path:
    """Получить путь к файлу checkpoint"""
    return work_dir / "ocr_checkpoint.json"
