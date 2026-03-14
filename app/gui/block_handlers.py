"""
Миксин для обработки блоков и событий
Комбинированный миксин из модулей: block_crud, block_events
"""

from app.gui.blocks import BlockCRUDMixin
from app.gui.block_events import BlockEventsMixin


class BlockHandlersMixin(BlockCRUDMixin, BlockEventsMixin):
    """Комбинированный миксин для обработки блоков"""

    pass
