"""Статистика экспорта OCR-результатов в итоговые документы."""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class ExportStats:
    """Счётчики блоков при генерации HTML/MD документов.

    Позволяет отследить, какие блоки были исключены и почему:
    - stamp: блоки с category_code="stamp" (штампы)
    - linked_text: TEXT блоки, встроенные в IMAGE через linked_block_id (только MD)
    """

    total_blocks: int = 0
    excluded_stamp_blocks: int = 0
    excluded_linked_text_blocks: int = 0
    exported_blocks: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def log_summary(self, format_name: str = "") -> str:
        """Человекочитаемая строка для логирования."""
        parts = [f"{self.total_blocks} total"]
        if self.excluded_stamp_blocks:
            parts.append(f"{self.excluded_stamp_blocks} stamp excluded")
        if self.excluded_linked_text_blocks:
            parts.append(f"{self.excluded_linked_text_blocks} linked-text excluded")
        parts.append(f"{self.exported_blocks} exported")
        prefix = f"{format_name}: " if format_name else ""
        return f"{prefix}{', '.join(parts)}"
