"""
PASS 1: Подготовка кропов и сохранение на диск.

Каждый блок сохраняется как отдельный crop (без группировки в strips).
"""
from __future__ import annotations

import gc
import os
from typing import Callable, List, Optional

from ..logging_config import get_logger
from ..manifest_models import CropManifestEntry, TwoPassManifest
from ..ocr_constants import make_non_retriable
from ..memory_utils import force_gc, log_memory, log_memory_delta
from ..pdf_streaming_core import StreamingPDFProcessor
from ..settings import settings

logger = get_logger(__name__)


def pass1_prepare_crops(
    pdf_path: str,
    blocks: List,
    crops_dir: str,
    padding: int = 5,
    save_image_crops_as_pdf: bool = True,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> TwoPassManifest:
    """
    PASS 1: Вырезать все кропы и сохранить на диск.

    Каждый блок (TEXT, IMAGE, STAMP) сохраняется как отдельный crop.
    Память освобождается после каждой страницы.
    """
    from rd_core.models import BlockType

    os.makedirs(crops_dir, exist_ok=True)
    images_dir = os.path.join(crops_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    start_mem = log_memory(
        f"PASS1 start (PDF: {os.path.getsize(pdf_path) / 1024 / 1024:.1f} MB)"
    )

    # Группируем блоки по страницам
    blocks_by_page: dict[int, List] = {}
    for block in blocks:
        blocks_by_page.setdefault(block.page_index, []).append(block)

    all_entries: List[CropManifestEntry] = []

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

                    # Все блоки сохраняются в images_dir с PNG и PDF кропами
                    crop_filename = f"{block.id}_p0.png"
                    crop_path = os.path.join(images_dir, crop_filename)

                    crop.save(crop_path, "PNG", compress_level=compress_level)

                    entry = CropManifestEntry(
                        block_id=block.id,
                        crop_path=crop_path,
                        block_type=block.block_type.value,
                        page_index=block.page_index,
                        part_idx=0,
                        total_parts=1,
                        width=crop.width,
                        height=crop.height,
                    )

                    logger.debug(
                        f"Crop saved: {block.id} ({crop.width}x{crop.height})",
                        extra={
                            "event": "crop_saved",
                            "block_id": block.id,
                            "page_index": block.page_index,
                            "block_type": block.block_type.value,
                            "crop_width": crop.width,
                            "crop_height": crop.height,
                        },
                    )

                    crop.close()

                    # PDF кроп для всех типов блоков (text, image, stamp)
                    if save_image_crops_as_pdf:
                        pdf_crop_path = os.path.join(images_dir, f"{block.id}.pdf")
                        result = processor.crop_block_to_pdf(
                            block, pdf_crop_path, padding_pt=2
                        )
                        if result:
                            block.image_file = result
                            entry.pdf_crop_path = result

                    all_entries.append(entry)

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
                return TwoPassManifest(pdf_path=pdf_path, crops_dir=crops_dir)

            gc.collect()

        log_memory_delta("PASS1 после кропов", start_mem)

    manifest = TwoPassManifest(
        pdf_path=pdf_path,
        crops_dir=crops_dir,
        blocks=all_entries,
        total_blocks=len(blocks),
    )

    # Сохраняем manifest
    manifest_path = os.path.join(crops_dir, "manifest.json")
    manifest.save(manifest_path)

    force_gc("PASS1 завершён")
    log_memory_delta("PASS1 end", start_mem)

    logger.info(
        f"PASS1 завершён: {len(all_entries)} block crops"
    )

    return manifest
