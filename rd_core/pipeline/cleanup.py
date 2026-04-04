"""Очистка временных файлов после обработки."""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from .manifest_models import TwoPassManifest

logger = logging.getLogger(__name__)


def cleanup_manifest_files(manifest: TwoPassManifest) -> None:
    """Удалить все временные файлы после обработки"""
    try:
        crops_dir = manifest.crops_dir
        if os.path.exists(crops_dir):
            shutil.rmtree(crops_dir)
            logger.info(f"Удалена директория кропов: {crops_dir}")
    except Exception as e:
        logger.warning(f"Ошибка удаления кропов: {e}")


def copy_crops_to_final(work_dir: Path, blocks) -> None:
    """Копировать PDF кропы в crops_final/ (исключая штампы)."""
    crops_dir = work_dir / "crops"
    images_subdir = crops_dir / "images"
    crops_final = work_dir / "crops_final"

    if not images_subdir.exists():
        return

    crops_final.mkdir(exist_ok=True)
    blocks_by_id = {b.id: b for b in blocks}

    from rd_core.ocr.generator_common import is_stamp_block
    stamp_ids = {b.id for b in blocks if is_stamp_block(b)}

    for pdf_file in images_subdir.glob("*.pdf"):
        try:
            block_id = pdf_file.stem

            if block_id in stamp_ids:
                logger.debug(f"Пропущен кроп штампа: {pdf_file.name}")
                continue

            target = crops_final / pdf_file.name
            shutil.copy2(pdf_file, target)

            if block_id in blocks_by_id:
                blocks_by_id[block_id].image_file = str(target)

            logger.debug(f"PDF кроп скопирован: {pdf_file.name}")
        except Exception as e:
            logger.warning(f"Ошибка копирования PDF кропа {pdf_file}: {e}")
