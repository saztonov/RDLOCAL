"""Централизованный state container для MainWindow.

Заменяет 20+ разрозненных атрибутов (`_current_pdf_path`, `current_page`,
`undo_stack`, `page_images` и т.д.) одним объектом с явной структурой.

Mixins и виджеты обращаются к `self.state` вместо прямых атрибутов MainWindow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from rd_core.models import Document
from rd_core.pdf_utils import PDFDocument


@dataclass
class MainWindowState:
    """Состояние главного окна — единый источник истины."""

    # ── Document ─────────────────────────────────────────────────────
    pdf_document: Optional[PDFDocument] = None
    annotation_document: Optional[Document] = None
    current_page: int = 0

    # ── File paths ───────────────────────────────────────────────────
    current_pdf_path: Optional[str] = None
    current_node_id: Optional[str] = None
    current_node_locked: bool = False

    # ── Page cache (LRU) ─────────────────────────────────────────────
    page_images: dict = field(default_factory=dict)
    _page_images_order: list = field(default_factory=list)
    _page_images_max: int = 5
    page_zoom_states: dict = field(default_factory=dict)

    # ── Undo/Redo ────────────────────────────────────────────────────
    undo_stack: list = field(default_factory=list)
    redo_stack: list = field(default_factory=list)

    # ── Clipboard ────────────────────────────────────────────────────
    blocks_clipboard: list = field(default_factory=list)

    # ── Helpers ──────────────────────────────────────────────────────

    @property
    def has_document(self) -> bool:
        """Есть ли открытый PDF документ."""
        return self.pdf_document is not None

    @property
    def has_annotation(self) -> bool:
        """Есть ли аннотация."""
        return self.annotation_document is not None

    @property
    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def reset(self) -> None:
        """Сбросить состояние (при закрытии документа)."""
        self.pdf_document = None
        self.annotation_document = None
        self.current_page = 0
        self.current_pdf_path = None
        self.current_node_id = None
        self.current_node_locked = False
        self.page_images.clear()
        self._page_images_order.clear()
        self.page_zoom_states.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.blocks_clipboard.clear()
