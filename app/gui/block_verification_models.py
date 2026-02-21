"""Модели данных для верификации блоков"""

from dataclasses import dataclass, field
from typing import List, Optional, Set


@dataclass
class BlockInfo:
    """Информация о блоке"""

    id: str
    page_index: int
    block_type: str  # "text", "image"
    category_code: Optional[str] = None  # "stamp" для штампов
    linked_block_id: Optional[str] = None  # ID связанного блока (для TEXT→IMAGE)

    @property
    def is_stamp(self) -> bool:
        return self.category_code == "stamp"


@dataclass
class VerificationResult:
    """Результат верификации"""

    # Блоки в annotation.json
    ann_total: int = 0
    ann_text: int = 0
    ann_image: int = 0
    ann_stamp: int = 0
    ann_blocks: List[BlockInfo] = field(default_factory=list)

    # Блоки в ocr.html (без штампов)
    ocr_html_blocks: Set[str] = field(default_factory=set)  # block IDs

    # Блоки в result.json
    result_blocks: Set[str] = field(default_factory=set)  # block IDs

    # Блоки в document.md (без штампов)
    document_md_blocks: Set[str] = field(default_factory=set)  # block IDs

    # Ожидаемые блоки (без штампов)
    expected_blocks: Set[str] = field(default_factory=set)

    # Embedded TEXT блоки (связаны с IMAGE через linked_block_id)
    embedded_text_ids: Set[str] = field(default_factory=set)

    # Отсутствующие блоки
    missing_in_ocr_html: List[BlockInfo] = field(default_factory=list)
    missing_in_result: List[BlockInfo] = field(default_factory=list)
    missing_in_document_md: List[BlockInfo] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        """Верификация прошла успешно?"""
        return (
            len(self.missing_in_ocr_html) == 0
            and len(self.missing_in_result) == 0
            and len(self.missing_in_document_md) == 0
        )
