"""Mixin для рендеринга блоков в PageViewer"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsTextItem

from rd_core.models import Block, BlockSource, BlockType, ShapeType


class BlockRenderingMixin:
    """Миксин для отрисовки блоков"""

    _redraw_pending: bool = False
    _last_redraw_time: float = 0
    _redraw_timer: QTimer = None

    def set_blocks(self, blocks: List[Block]):
        """Установить список блоков для отображения"""
        self.current_blocks = blocks
        self._clear_block_items()
        self._draw_all_blocks()

    def _clear_block_items(self):
        """Очистить все QGraphicsRectItem блоков"""
        for item in self.block_items.values():
            try:
                self.scene.removeItem(item)
            except RuntimeError:
                pass
        self.block_items.clear()
        for label in self.block_labels.values():
            try:
                self.scene.removeItem(label)
            except RuntimeError:
                pass
        self.block_labels.clear()
        self._clear_resize_handles()

    def _draw_all_blocks(self):
        """Отрисовать все блоки"""
        for idx, block in enumerate(self.current_blocks):
            self._draw_block(block, idx)

    def _draw_block(self, block: Block, idx: int):
        """Отрисовать один блок"""
        from PySide6.QtCore import Qt

        color = self._get_block_color(block)
        pen = QPen(color, 2)

        if block.source == BlockSource.AUTO:
            pen.setStyle(Qt.DashLine)
            pen.setWidth(3)

        # Корректировочные блоки - оранжевая пунктирная рамка
        if block.is_correction:
            pen.setColor(QColor(255, 165, 0))  # Orange
            pen.setStyle(Qt.DashDotLine)
            pen.setWidth(3)

        if idx in self.selected_block_indices:
            pen.setColor(QColor(0, 120, 255))
            pen.setWidth(4)

        if idx == self.selected_block_idx:
            pen.setWidth(4)

        # Заливка всегда по типу блока (текст/картинка)
        brush = QBrush(QColor(color.red(), color.green(), color.blue(), 30))

        if block.shape_type == ShapeType.POLYGON and block.polygon_points:
            polygon = QPolygonF([QPointF(x, y) for x, y in block.polygon_points])
            poly_item = QGraphicsPolygonItem(polygon)
            poly_item.setPen(pen)
            poly_item.setBrush(brush)
            poly_item.setData(0, block.id)
            poly_item.setData(1, idx)
            self.scene.addItem(poly_item)
            self.block_items[block.id] = poly_item
            x1, y1, x2, y2 = block.coords_px
        else:
            x1, y1, x2, y2 = block.coords_px
            rect = QRectF(x1, y1, x2 - x1, y2 - y1)
            rect_item = QGraphicsRectItem(rect)
            rect_item.setPen(pen)
            rect_item.setBrush(brush)
            rect_item.setData(0, block.id)
            rect_item.setData(1, idx)
            self.scene.addItem(rect_item)
            self.block_items[block.id] = rect_item

        x1, y1, x2, y2 = block.coords_px
        label = QGraphicsTextItem(str(idx + 1))
        font = QFont("Arial", 12, QFont.Bold)
        label.setFont(font)
        label.setDefaultTextColor(QColor(255, 0, 0))
        label.setFlag(label.GraphicsItemFlag.ItemIgnoresTransformations, True)
        # Позиционируем метку в правом верхнем углу блока с учётом ширины текста
        lx, ly = self._compute_label_anchor(block, label)
        label.setPos(lx, ly)
        self.scene.addItem(label)
        self.block_labels[block.id] = label

        if idx == self.selected_block_idx:
            if block.shape_type == ShapeType.RECTANGLE:
                rect = QRectF(x1, y1, x2 - x1, y2 - y1)
                self._draw_resize_handles(rect)
            elif block.shape_type == ShapeType.POLYGON and block.polygon_points:
                self._draw_polygon_handles(block.polygon_points)

    def _get_block_color(self, block: Block) -> QColor:
        """Получить цвет для блока по типу"""
        colors = {
            BlockType.TEXT: QColor(0, 255, 0),
            BlockType.IMAGE: QColor(255, 140, 0),
            BlockType.STAMP: QColor(30, 144, 255),  # Dodger Blue
        }
        return colors.get(block.block_type, QColor(128, 128, 128))

    def _compute_label_anchor(self, block: Block, label: QGraphicsTextItem) -> tuple:
        """Вычислить позицию метки номера блока (правый верхний угол, внутрь).

        Для прямоугольников: правый верхний угол с учётом ширины текста.
        Для полигонов: scanline у верхней границы контура → правое пересечение,
        сдвиг внутрь на ширину текста.
        """
        inset = 5 / self.zoom_factor
        # Ширина текста в scene-координатах (ItemIgnoresTransformations)
        text_w = label.boundingRect().width() / self.zoom_factor

        x1, y1, x2, y2 = block.coords_px

        if block.shape_type == ShapeType.POLYGON and block.polygon_points:
            return self._polygon_label_anchor(
                block.polygon_points, text_w, inset
            )

        # Прямоугольник: правый верхний угол внутрь
        return (x2 - inset - text_w, y1 + inset)

    @staticmethod
    def _polygon_label_anchor(
        points: list, text_w: float, inset: float
    ) -> tuple:
        """Anchor для метки полигона по scanline у верхнего контура.

        1. Scanline на y = y_min + inset → пересечения с рёбрами → правое.
        2. Fallback: правейшая вершина в верхних 20% высоты.
        3. Fallback: bbox правый верхний угол.
        """
        ys = [p[1] for p in points]
        xs = [p[0] for p in points]
        y_min, y_max = min(ys), max(ys)
        height = y_max - y_min

        # Scanline чуть ниже верхней границы
        scan_y = y_min + max(inset, height * 0.05)

        # Пересечения scanline с рёбрами полигона
        intersections = []
        n = len(points)
        for i in range(n):
            ax, ay = points[i]
            bx, by = points[(i + 1) % n]
            # Ребро пересекает scanline?
            if (ay <= scan_y < by) or (by <= scan_y < ay):
                # Линейная интерполяция x на scanline
                t = (scan_y - ay) / (by - ay)
                ix = ax + t * (bx - ax)
                intersections.append(ix)

        if intersections:
            right_x = max(intersections)
            return (right_x - inset - text_w, scan_y)

        # Fallback 1: правейшая вершина в верхних 20% высоты
        top_band = y_min + height * 0.2
        top_vertices = [(px, py) for px, py in points if py <= top_band]
        if top_vertices:
            rightmost = max(top_vertices, key=lambda p: p[0])
            return (rightmost[0] - inset - text_w, rightmost[1] + inset)

        # Fallback 2: bbox
        return (max(xs) - inset - text_w, y_min + inset)

    def _redraw_blocks(self):
        """Перерисовать все блоки"""
        self._clear_block_items()
        self._draw_all_blocks()

    def _redraw_blocks_throttled(self, delay_ms: int = 16):
        """Перерисовать блоки с throttle (для анимаций)"""
        if self._redraw_pending:
            return
        self._redraw_pending = True

        if self._redraw_timer is None:
            self._redraw_timer = QTimer()
            self._redraw_timer.setSingleShot(True)
            self._redraw_timer.timeout.connect(self._do_throttled_redraw)

        self._redraw_timer.start(delay_ms)

    def _do_throttled_redraw(self):
        """Выполнить отложенную перерисовку"""
        self._redraw_pending = False
        self._redraw_blocks()

    def _update_single_block_visual(self, block_idx: int):
        """Обновить визуал только одного блока (для перетаскивания)"""
        if block_idx >= len(self.current_blocks):
            return

        block = self.current_blocks[block_idx]

        # Удаляем старый item этого блока
        if block.id in self.block_items:
            old_item = self.block_items[block.id]
            self.scene.removeItem(old_item)
            del self.block_items[block.id]

        # Удаляем старую метку
        if block.id in self.block_labels:
            old_label = self.block_labels[block.id]
            self.scene.removeItem(old_label)
            del self.block_labels[block.id]

        # Удаляем метку галочки если была
        check_key = block.id + "_check"
        if check_key in self.block_labels:
            self.scene.removeItem(self.block_labels[check_key])
            del self.block_labels[check_key]

        # Очищаем и перерисовываем resize handles для выбранного блока
        self._clear_resize_handles()

        # Рисуем блок заново
        self._draw_block(block, block_idx)

    def _find_block_at_position(self, scene_pos: QPointF) -> Optional[int]:
        """Найти блок в заданной позиции"""
        item = self.scene.itemAt(scene_pos, self.transform())

        if item and item != self.rubber_band_item:
            if isinstance(item, QGraphicsRectItem):
                idx = item.data(1)
                if idx is not None:
                    return idx
            elif isinstance(item, QGraphicsPolygonItem):
                idx = item.data(1)
                if idx is not None:
                    return idx
        return None

    def _find_blocks_in_rect(self, rect: QRectF) -> List[int]:
        """Найти все блоки, попадающие в прямоугольник"""
        selected_indices = []
        for idx, block in enumerate(self.current_blocks):
            x1, y1, x2, y2 = block.coords_px
            block_rect = QRectF(x1, y1, x2 - x1, y2 - y1)
            if rect.intersects(block_rect):
                selected_indices.append(idx)
        return selected_indices

    def get_selected_blocks(self) -> List[Block]:
        """Получить список выбранных блоков"""
        selected = []
        if self.selected_block_indices:
            for idx in self.selected_block_indices:
                if 0 <= idx < len(self.current_blocks):
                    selected.append(self.current_blocks[idx])
        elif self.selected_block_idx is not None:
            if 0 <= self.selected_block_idx < len(self.current_blocks):
                selected.append(self.current_blocks[self.selected_block_idx])
        return selected
