"""Модели данных для верификации блоков"""

from dataclasses import dataclass, field
from typing import List, Optional, Set


@dataclass
class BlockInfo:
    """Информация о блоке"""

    id: str
    page_index: int
    block_type: str  # "text", "image", "stamp"
    category_code: Optional[str] = None  # legacy, сохранено для совместимости
    linked_block_id: Optional[str] = None  # ID связанного блока (для TEXT→IMAGE)

    @property
    def is_stamp(self) -> bool:
        return self.block_type == "stamp"


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

    # Блоки с ошибками в OCR контенте (проверка result.json)
    error_blocks: List[BlockInfo] = field(default_factory=list)       # [Ошибка: ...]
    suspicious_blocks: List[BlockInfo] = field(default_factory=list)  # JSON-dump и т.п.

    # Описания проблем (block_id → reason)
    error_reasons: dict = field(default_factory=dict)
    suspicious_reasons: dict = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """Верификация прошла успешно (структурная + контентная)?"""
        return (
            len(self.missing_in_ocr_html) == 0
            and len(self.missing_in_result) == 0
            and len(self.missing_in_document_md) == 0
            and len(self.error_blocks) == 0
            and len(self.suspicious_blocks) == 0
        )
