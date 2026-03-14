"""Утилита разделения аннотаций по диапазонам страниц."""
import copy
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from rd_core.models import Document, Page

logger = logging.getLogger(__name__)


@dataclass
class SplitAnnotationResult:
    """Результат разделения аннотации для одной части."""

    document: Document  # Document с перенумерованными страницами
    broken_links: List[str] = field(default_factory=list)  # ID блоков со сломанными linked_block_id


def split_annotation(
    source_doc: Document,
    page_ranges: List[Tuple[int, int]],
    part_pdf_paths: List[str],
) -> List[SplitAnnotationResult]:
    """
    Разделить аннотацию Document по диапазонам страниц.

    Для каждой части:
    1. Копирует Page объекты, попадающие в диапазон
    2. Перенумеровывает page_number и block.page_index (0-based от начала части)
    3. coords_px и coords_norm остаются без изменений (относительны к странице)
    4. Обрабатывает cross-part ссылки (linked_block_id)

    Args:
        source_doc: Исходный Document
        page_ranges: Список диапазонов (start, end) включительно, 0-based
        part_pdf_paths: Пути к PDF для каждой части (для pdf_path в Document)

    Returns:
        Список SplitAnnotationResult для каждой части
    """
    # Индекс: block_id → номер части (для обработки cross-part ссылок)
    block_to_part: Dict[str, int] = {}

    # Первый проход: построить индексы
    for part_idx, (start, end) in enumerate(page_ranges):
        for page in source_doc.pages:
            if start <= page.page_number <= end:
                for block in page.blocks:
                    block_to_part[block.id] = part_idx

    # Второй проход: создать части
    results: List[SplitAnnotationResult] = []

    for part_idx, (start, end) in enumerate(page_ranges):
        part_pages: List[Page] = []
        broken_links: List[str] = []

        # Собрать страницы для этой части
        new_page_number = 0
        for page in source_doc.pages:
            if start <= page.page_number <= end:
                # Глубокая копия страницы
                new_page = copy.deepcopy(page)
                new_page.page_number = new_page_number

                # Перенумеровать блоки
                for block in new_page.blocks:
                    block.page_index = new_page_number

                    # Проверить linked_block_id
                    if block.linked_block_id:
                        linked_part = block_to_part.get(block.linked_block_id)
                        if linked_part is not None and linked_part != part_idx:
                            broken_links.append(block.id)
                            block.linked_block_id = None

                part_pages.append(new_page)
                new_page_number += 1

        part_doc = Document(
            pdf_path=part_pdf_paths[part_idx],
            pages=part_pages,
        )

        results.append(
            SplitAnnotationResult(
                document=part_doc,
                broken_links=broken_links,
            )
        )

        logger.info(
            f"Часть {part_idx + 1}: {len(part_pages)} стр., "
            f"{sum(len(p.blocks) for p in part_pages)} блоков"
            + (f", {len(broken_links)} сломанных ссылок" if broken_links else "")
        )

    return results
