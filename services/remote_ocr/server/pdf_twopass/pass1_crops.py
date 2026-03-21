"""
PASS 1: Подготовка кропов и сохранение на диск.

Группирует TEXT/TABLE блоки в strips, IMAGE блоки сохраняет отдельно.
"""
from __future__ import annotations

import gc
import os
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image

from ..logging_config import get_logger
from ..manifest_models import CropManifestEntry, StripManifestEntry, TwoPassManifest
from ..ocr_constants import make_non_retriable
from ..memory_utils import force_gc, log_memory, log_memory_delta
from ..pdf_streaming_core import (
    StreamingPDFProcessor,
    merge_crops_vertically,
    split_large_crop,
)
from ..settings import settings

MAX_STRIP_HEIGHT = settings.max_strip_height
MAX_SINGLE_BLOCK_HEIGHT = settings.max_strip_height

logger = get_logger(__name__)


def pass1_prepare_crops(
    pdf_path: str,
    blocks: List,
    crops_dir: str,
    padding: int = 5,
    save_image_crops_as_pdf: bool = True,
    on_progress: Optional[Callable[[int, int], None]] = None,
    engine: str = "",
    should_stop: Optional[Callable[[], bool]] = None,
) -> TwoPassManifest:
    """
    PASS 1: Вырезать все кропы и сохранить на диск.

    Группирует TEXT/TABLE блоки в strips, IMAGE блоки сохраняет отдельно.
    Память освобождается после каждой страницы.
    """
    from rd_core.models import BlockType

    os.makedirs(crops_dir, exist_ok=True)
    strips_dir = os.path.join(crops_dir, "strips")
    images_dir = os.path.join(crops_dir, "images")
    os.makedirs(strips_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)

    start_mem = log_memory(
        f"PASS1 start (PDF: {os.path.getsize(pdf_path) / 1024 / 1024:.1f} MB)"
    )

    # Группируем блоки по страницам
    blocks_by_page: Dict[int, List] = {}
    for block in blocks:
        blocks_by_page.setdefault(block.page_index, []).append(block)

    # Временное хранение путей к кропам
    block_crop_paths: Dict[str, List[Tuple[str, int, int]]] = {}
    image_block_entries: List[CropManifestEntry] = []
    image_pdf_paths: Dict[str, str] = {}

    processed_pages = 0
    total_pages = len(blocks_by_page)

    compress_level = settings.crop_png_compress

    with StreamingPDFProcessor(pdf_path) as processor:
        logger.info(f"PASS1: {processor.page_count} страниц, {len(blocks)} блоков")

        for page_idx in sorted(blocks_by_page.keys()):
            page_blocks = blocks_by_page[page_idx]

            for block in page_blocks:
                try:
                    crop = processor.crop_block_image(block, padding)
                    if not crop:
                        block.ocr_text = make_non_retriable("не удалось вырезать блок — невалидные координаты")
                        logger.warning(
                            f"PASS1: блок {block.id} пропущен (crop=None), "
                            f"координаты невалидны"
                        )
                        continue

                    # Разделяем большие кропы
                    crop_parts = split_large_crop(crop, MAX_SINGLE_BLOCK_HEIGHT)
                    total_parts = len(crop_parts)

                    block_crop_paths[block.id] = []

                    for part_idx, crop_part in enumerate(crop_parts):
                        if block.block_type == BlockType.IMAGE:
                            crop_filename = f"{block.id}_p{part_idx}.png"
                            crop_path = os.path.join(images_dir, crop_filename)
                        else:
                            crop_filename = f"{block.id}_p{part_idx}.png"
                            crop_path = os.path.join(crops_dir, crop_filename)

                        crop_part.save(crop_path, "PNG", compress_level=compress_level)

                        block_crop_paths[block.id].append(
                            (crop_path, part_idx, total_parts)
                        )

                        if block.block_type == BlockType.IMAGE:
                            image_block_entries.append(
                                CropManifestEntry(
                                    block_id=block.id,
                                    crop_path=crop_path,
                                    block_type=block.block_type.value,
                                    page_index=block.page_index,
                                    part_idx=part_idx,
                                    total_parts=total_parts,
                                    width=crop_part.width,
                                    height=crop_part.height,
                                )
                            )
                            logger.debug(
                                f"Image crop: {block.id} ({crop_part.width}x{crop_part.height})",
                                extra={
                                    "event": "image_crop_saved",
                                    "block_id": block.id,
                                    "page_index": block.page_index,
                                    "crop_width": crop_part.width,
                                    "crop_height": crop_part.height,
                                },
                            )

                        crop_part.close()

                    if total_parts > 1:
                        crop.close()

                    # PDF кроп для IMAGE блоков
                    if save_image_crops_as_pdf and block.block_type == BlockType.IMAGE:
                        pdf_crop_path = os.path.join(images_dir, f"{block.id}.pdf")
                        result = processor.crop_block_to_pdf(
                            block, pdf_crop_path, padding_pt=2
                        )
                        if result:
                            image_pdf_paths[block.id] = result
                            block.image_file = result
                            if total_parts == 1:
                                for entry in image_block_entries:
                                    if entry.block_id == block.id:
                                        entry.pdf_crop_path = result
                                        break

                except Exception as e:
                    block.ocr_text = make_non_retriable(f"ошибка crop — {e}")
                    logger.error(
                        f"PASS1: crop error for block {block.id}",
                        extra={
                            "event": "pass1_crop_error",
                            "block_id": block.id,
                            "page_index": block.page_index,
                            "block_type": block.block_type.value,
                        },
                        exc_info=True,
                    )

            processed_pages += 1
            if on_progress:
                on_progress(processed_pages, total_pages)

            if should_stop and should_stop():
                logger.info("PASS1 прерван: задача отменена/приостановлена")
                return TwoPassManifest(strips=[], image_blocks=[])

            gc.collect()

        log_memory_delta("PASS1 после кропов", start_mem)

    # Группируем TEXT/TABLE в strips и сохраняем merged images
    strips = _group_and_merge_strips(
        blocks, block_crop_paths, strips_dir, compress_level, engine=engine
    )

    # Удаляем промежуточные кропы TEXT/TABLE
    from rd_core.models import BlockType as BT
    for block in blocks:
        if block.block_type != BT.IMAGE and block.id in block_crop_paths:
            for crop_path, _, _ in block_crop_paths[block.id]:
                try:
                    if os.path.exists(crop_path):
                        os.remove(crop_path)
                except Exception:
                    pass

    manifest = TwoPassManifest(
        pdf_path=pdf_path,
        crops_dir=crops_dir,
        strips=strips,
        image_blocks=image_block_entries,
        total_blocks=len(blocks),
    )

    # Сохраняем manifest
    manifest_path = os.path.join(crops_dir, "manifest.json")
    manifest.save(manifest_path)

    force_gc("PASS1 завершён")
    log_memory_delta("PASS1 end", start_mem)

    logger.info(
        f"PASS1 завершён: {len(strips)} strips, {len(image_block_entries)} image crops"
    )

    return manifest


def _group_and_merge_strips(
    blocks: List,
    block_crop_paths: Dict[str, List[Tuple[str, int, int]]],
    strips_dir: str,
    compress_level: int,
    engine: str = "",
) -> List[StripManifestEntry]:
    """Группировка TEXT/TABLE блоков в strips и сохранение merged images"""
    from rd_core.models import BlockType

    strips: List[StripManifestEntry] = []
    current_strip_blocks: List[Tuple[str, str, int, int]] = []
    current_strip_height = 0
    strip_counter = 0
    gap = 20

    def _save_current_strip():
        nonlocal strip_counter, current_strip_blocks, current_strip_height

        if not current_strip_blocks:
            return

        strip_counter += 1
        strip_id = f"strip_{strip_counter:04d}"
        strip_path = os.path.join(strips_dir, f"{strip_id}.png")

        crops = []
        for block_id, crop_path, part_idx, total_parts in current_strip_blocks:
            try:
                crop = Image.open(crop_path)
                crops.append(crop)
            except Exception as e:
                logger.error(
                    f"PASS1: crop load error {crop_path}",
                    extra={
                        "event": "pass1_crop_load_error",
                        "block_id": block_id,
                        "crop_path": crop_path,
                        "part_idx": part_idx,
                        "total_parts": total_parts,
                    },
                    exc_info=True,
                )

        if crops:
            try:
                block_ids = [b[0] for b in current_strip_blocks]
                merged = merge_crops_vertically(crops, gap, block_ids=block_ids)
                merged.save(strip_path, "PNG", compress_level=compress_level)
                merged.close()
            except Exception as e:
                logger.error(
                    f"PASS1: strip merge error {strip_id}",
                    extra={
                        "event": "pass1_strip_merge_error",
                        "strip_id": strip_id,
                        "block_count": len(current_strip_blocks),
                        "block_ids": [b[0] for b in current_strip_blocks],
                    },
                    exc_info=True,
                )
                strip_path = ""
            finally:
                for c in crops:
                    try:
                        c.close()
                    except Exception:
                        pass

        block_ids_list = [b[0] for b in current_strip_blocks]
        strips.append(
            StripManifestEntry(
                strip_id=strip_id,
                strip_path=strip_path,
                block_ids=block_ids_list,
                block_parts=[
                    {"block_id": b[0], "part_idx": b[2], "total_parts": b[3]}
                    for b in current_strip_blocks
                ],
            )
        )

        logger.debug(
            f"Strip {strip_id}: {len(block_ids_list)} блоков, высота={current_strip_height}px",
            extra={
                "event": "strip_created",
                "strip_id": strip_id,
                "block_count": len(block_ids_list),
                "block_ids": block_ids_list,
                "crop_height": current_strip_height,
            },
        )

        current_strip_blocks = []
        current_strip_height = 0

    for block in blocks:
        if block.block_type == BlockType.IMAGE:
            continue

        if block.id not in block_crop_paths:
            continue

        for crop_path, part_idx, total_parts in block_crop_paths[block.id]:
            try:
                with Image.open(crop_path) as img:
                    crop_height = img.height
            except Exception:
                crop_height = 500

            new_height = crop_height + (gap if current_strip_blocks else 0)

            if (
                current_strip_height + new_height > MAX_STRIP_HEIGHT
                and current_strip_blocks
            ):
                _save_current_strip()
                new_height = crop_height

            current_strip_blocks.append((block.id, crop_path, part_idx, total_parts))
            current_strip_height += new_height

        # LM Studio бэкенды: каждый блок отдельно (не объединять в общие strips)
        if engine == "chandra" and current_strip_blocks:
            _save_current_strip()

    _save_current_strip()

    return strips
