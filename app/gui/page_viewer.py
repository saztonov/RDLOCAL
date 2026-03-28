"""
Виджет просмотра страницы PDF
Отображение страницы с возможностью рисовать прямоугольники для разметки
"""

from typing import Dict, List, Optional, Union

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
)

from app.gui.page_viewer_blocks import BlockRenderingMixin
from app.gui.page_viewer_menus.context_menu import ContextMenuMixin
from app.gui.page_viewer_mouse import MouseEventsMixin
from app.gui.page_viewer_polygon import PolygonMixin
from app.gui.page_viewer_resize import ResizeHandlesMixin
from app.gui.page_viewer_state import ViewerState
from rd_core.models import Block, ShapeType


class PageViewer(
    ContextMenuMixin,
    MouseEventsMixin,
    BlockRenderingMixin,
    PolygonMixin,
    ResizeHandlesMixin,
    QGraphicsView,
):
    """
    Виджет для отображения страницы PDF и рисования блоков разметки
    """

    blockDrawn = Signal(int, int, int, int)
    polygonDrawn = Signal(list)
    block_selected = Signal(int)
    blocks_selected = Signal(list)
    blockDeleted = Signal(int)
    blocks_deleted = Signal(list)
    blockMoved = Signal(int, int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        
        # Отключаем индексирование BSP для быстрой отрисовки при перемещении
        self.scene.setItemIndexMethod(QGraphicsScene.NoIndex)

        self.page_image: Optional[QPixmap] = None
        self.image_item: Optional[QGraphicsPixmapItem] = None
        self.current_blocks: List[Block] = []
        self.block_items: Dict[str, Union[QGraphicsRectItem, QGraphicsPolygonItem]] = {}
        self.block_labels: Dict[str, QGraphicsTextItem] = {}
        self.resize_handles: List[QGraphicsRectItem] = []
        self.current_page: int = 0

        self.read_only = False  # Режим "только чтение" для заблокированных документов

        # State machine (заменяет 7+ boolean флагов)
        self.state: ViewerState = ViewerState.IDLE

        # Polygon drawing data
        self.polygon_points: List[QPointF] = []
        self.polygon_preview_items: List[QGraphicsEllipseItem] = []
        self.polygon_line_items: List[QGraphicsLineItem] = []
        self.polygon_temp_line: Optional[QGraphicsLineItem] = None

        # Common interaction data
        self.start_point: Optional[QPointF] = None
        self.rubber_band_item: Optional[QGraphicsRectItem] = None
        self.selected_block_idx: Optional[int] = None
        self.selected_block_indices: List[int] = []

        # Block manipulation data
        self.resize_handle = None
        self.move_start_pos: Optional[QPointF] = None
        self.original_block_rect: Optional[QRectF] = None

        # Polygon manipulation data
        self._dragging_polygon_vertex_idx: Optional[int] = None
        self._dragging_polygon_edge_idx: Optional[int] = None
        self.original_polygon_points: Optional[List[tuple]] = None

        # Panning data
        self.pan_start_pos: Optional[QPointF] = None

        self.zoom_factor = 1.0
        
        # Кеш для main_window (оптимизация доступа)
        self._main_window_cache = None
        
        # Throttling для mouseMoveEvent при рисовании
        self._last_mouse_move_time = 0
        self._mouse_move_throttle_ms = 8  # ~120 FPS максимум
        
        self._setup_ui()

    # ── State property aliases (обратная совместимость с mixins) ─────

    @property
    def drawing(self) -> bool:
        return self.state == ViewerState.DRAWING_RECT

    @drawing.setter
    def drawing(self, value: bool):
        self.state = ViewerState.DRAWING_RECT if value else ViewerState.IDLE

    @property
    def drawing_polygon(self) -> bool:
        return self.state == ViewerState.DRAWING_POLYGON

    @drawing_polygon.setter
    def drawing_polygon(self, value: bool):
        self.state = ViewerState.DRAWING_POLYGON if value else ViewerState.IDLE

    @property
    def selecting(self) -> bool:
        return self.state == ViewerState.SELECTING

    @selecting.setter
    def selecting(self, value: bool):
        self.state = ViewerState.SELECTING if value else ViewerState.IDLE

    @property
    def moving_block(self) -> bool:
        return self.state == ViewerState.MOVING_BLOCK

    @moving_block.setter
    def moving_block(self, value: bool):
        self.state = ViewerState.MOVING_BLOCK if value else ViewerState.IDLE

    @property
    def resizing_block(self) -> bool:
        return self.state == ViewerState.RESIZING_BLOCK

    @resizing_block.setter
    def resizing_block(self, value: bool):
        self.state = ViewerState.RESIZING_BLOCK if value else ViewerState.IDLE

    @property
    def panning(self) -> bool:
        return self.state == ViewerState.PANNING

    @panning.setter
    def panning(self, value: bool):
        self.state = ViewerState.PANNING if value else ViewerState.IDLE

    @property
    def right_button_pressed(self) -> bool:
        return self.state == ViewerState.RIGHT_BUTTON_DOWN

    @right_button_pressed.setter
    def right_button_pressed(self, value: bool):
        if value:
            self.state = ViewerState.RIGHT_BUTTON_DOWN
        elif self.state == ViewerState.RIGHT_BUTTON_DOWN:
            self.state = ViewerState.IDLE

    @property
    def dragging_polygon_vertex(self) -> Optional[int]:
        if self.state == ViewerState.DRAGGING_POLYGON_VERTEX:
            return self._dragging_polygon_vertex_idx
        return None

    @dragging_polygon_vertex.setter
    def dragging_polygon_vertex(self, value: Optional[int]):
        if value is not None:
            self._dragging_polygon_vertex_idx = value
            self.state = ViewerState.DRAGGING_POLYGON_VERTEX
        else:
            self._dragging_polygon_vertex_idx = None
            if self.state == ViewerState.DRAGGING_POLYGON_VERTEX:
                self.state = ViewerState.IDLE

    @property
    def dragging_polygon_edge(self) -> Optional[int]:
        if self.state == ViewerState.DRAGGING_POLYGON_EDGE:
            return self._dragging_polygon_edge_idx
        return None

    @dragging_polygon_edge.setter
    def dragging_polygon_edge(self, value: Optional[int]):
        if value is not None:
            self._dragging_polygon_edge_idx = value
            self.state = ViewerState.DRAGGING_POLYGON_EDGE
        else:
            self._dragging_polygon_edge_idx = None
            if self.state == ViewerState.DRAGGING_POLYGON_EDGE:
                self.state = ViewerState.IDLE

    def _setup_ui(self):
        """Настройка интерфейса"""
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMinimumSize(800, 600)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.context_menu_pos: Optional[QPointF] = None
        
        # Оптимизация рендеринга для интерактивных операций
        self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.DontAdjustForAntialiasing, True)

    def get_current_shape_type(self) -> ShapeType:
        """Получить текущий выбранный тип формы из главного окна"""
        # Используем кеш для оптимизации
        if self._main_window_cache is None:
            self._main_window_cache = self.parent().window()
        
        if hasattr(self._main_window_cache, "selected_shape_type"):
            return self._main_window_cache.selected_shape_type
        return ShapeType.RECTANGLE

    def _clamp_to_page(self, point: QPointF) -> QPointF:
        """Ограничить точку границами страницы"""
        if not self.page_image:
            return point
        page_rect = self.scene.sceneRect()
        x = max(page_rect.left(), min(point.x(), page_rect.right()))
        y = max(page_rect.top(), min(point.y(), page_rect.bottom()))
        return QPointF(x, y)

    def _clamp_rect_to_page(self, rect: QRectF) -> QRectF:
        """Ограничить прямоугольник границами страницы"""
        if not self.page_image:
            return rect
        page_rect = self.scene.sceneRect()
        x1 = max(page_rect.left(), min(rect.left(), page_rect.right()))
        y1 = max(page_rect.top(), min(rect.top(), page_rect.bottom()))
        x2 = max(page_rect.left(), min(rect.right(), page_rect.right()))
        y2 = max(page_rect.top(), min(rect.bottom(), page_rect.bottom()))
        if x2 - x1 < 10:
            x2 = min(x1 + 10, page_rect.right())
        if y2 - y1 < 10:
            y2 = min(y1 + 10, page_rect.bottom())
        return QRectF(QPointF(x1, y1), QPointF(x2, y2))

    def set_page_image(
        self, pil_image: Image.Image, page_number: int = 0, reset_zoom: bool = True
    ):
        """Установить изображение страницы"""
        if pil_image is None:
            self.scene.clear()
            self.page_image = None
            self.image_item = None
            self.current_page = page_number
            self.selected_block_idx = None
            self.block_items.clear()
            self._set_close_button_visible(False)
            return

        # Конвертируем в RGB если нужно (RGBA -> RGB)
        if pil_image.mode == "RGBA":
            rgb_image = Image.new("RGB", pil_image.size, (255, 255, 255))
            rgb_image.paste(pil_image, mask=pil_image.split()[3])
            pil_image = rgb_image
        elif pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")

        # Прямая конвертация PIL → QPixmap без промежуточного копирования
        img_data = pil_image.tobytes("raw", "RGB")
        qimage = QImage(
            img_data,
            pil_image.width,
            pil_image.height,
            pil_image.width * 3,
            QImage.Format_RGB888,
        )
        # Важно: делаем copy() чтобы QImage владел данными
        self.page_image = QPixmap.fromImage(qimage.copy())
        self.current_page = page_number

        self.scene.clear()
        self.image_item = self.scene.addPixmap(self.page_image)
        self.scene.setSceneRect(QRectF(self.page_image.rect()))

        self.selected_block_idx = None
        self.selected_block_indices = []
        self.block_items.clear()
        self.block_labels.clear()

        if reset_zoom:
            self.fit_to_view()

        self._set_close_button_visible(True)

    def _set_close_button_visible(self, visible: bool):
        """Показать/скрыть кнопку закрытия"""
        main_window = self.window()
        if hasattr(main_window, "close_pdf_btn"):
            if visible:
                main_window.close_pdf_btn.show()
                self._update_close_button_position()
            else:
                main_window.close_pdf_btn.hide()

    def reset_zoom(self):
        """Сбросить масштаб к 100%"""
        self.resetTransform()
        self.zoom_factor = 1.0

    def fit_to_view(self):
        """Подогнать страницу под размер view"""
        if self.page_image:
            self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
            self.zoom_factor = self.transform().m11()

    def resizeEvent(self, event):
        """Позиционирование кнопки закрытия при изменении размера"""
        super().resizeEvent(event)
        self._update_close_button_position()

    def _update_close_button_position(self):
        """Обновить позицию кнопки закрытия"""
        main_window = self.window()
        if hasattr(main_window, "close_pdf_btn"):
            btn = main_window.close_pdf_btn
            btn.move(self.width() - btn.width() - 10, 10)
