"""State machine для PageViewer.

Заменяет 7+ boolean флагов (drawing, selecting, moving_block, resizing_block,
panning, drawing_polygon, right_button_pressed) на единый enum состояния.

Гарантирует, что невозможные комбинации (drawing + moving_block) исключены.
"""
from __future__ import annotations

from enum import Enum, auto


class ViewerState(Enum):
    """Состояние PageViewer."""

    IDLE = auto()                    # Нет активной операции
    DRAWING_RECT = auto()            # Рисование прямоугольника (LMB на пустом месте)
    DRAWING_POLYGON = auto()         # Рисование полигона (LMB + polygon mode)
    SELECTING = auto()               # Выделение области (RMB drag)
    MOVING_BLOCK = auto()            # Перемещение блока (LMB на выбранном)
    RESIZING_BLOCK = auto()          # Изменение размера (LMB на resize handle)
    PANNING = auto()                 # Панорамирование (MMB)
    DRAGGING_POLYGON_VERTEX = auto() # Перетаскивание вершины полигона
    DRAGGING_POLYGON_EDGE = auto()   # Перетаскивание ребра полигона
    RIGHT_BUTTON_DOWN = auto()       # Правая кнопка зажата (до selecting/context menu)


# Состояния, в которых мышь "захвачена" (drag операция)
DRAG_STATES = frozenset({
    ViewerState.DRAWING_RECT,
    ViewerState.SELECTING,
    ViewerState.MOVING_BLOCK,
    ViewerState.RESIZING_BLOCK,
    ViewerState.PANNING,
    ViewerState.DRAGGING_POLYGON_VERTEX,
    ViewerState.DRAGGING_POLYGON_EDGE,
})

# Состояния, в которых запрещено editing (block create/modify)
EDITING_STATES = frozenset({
    ViewerState.DRAWING_RECT,
    ViewerState.DRAWING_POLYGON,
    ViewerState.MOVING_BLOCK,
    ViewerState.RESIZING_BLOCK,
    ViewerState.DRAGGING_POLYGON_VERTEX,
    ViewerState.DRAGGING_POLYGON_EDGE,
})
