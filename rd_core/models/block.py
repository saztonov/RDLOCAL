"""
Модель блока разметки на странице PDF.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

from rd_core.models.armor_id import (
    generate_armor_id,
    get_moscow_time_str,
    migrate_block_id,
)
from rd_core.models.enums import BlockSource, BlockType, ShapeType


@dataclass
class Block:
    """
    Блок разметки на странице PDF (обновлённая версия)

    Attributes:
        id: уникальный идентификатор блока (UUID)
        page_index: индекс страницы (начиная с 0)
        coords_px: координаты в пикселях (x1, y1, x2, y2) на отрендеренном изображении
        coords_norm: нормализованные координаты (0..1) относительно ширины/высоты
        block_type: тип блока (TEXT/IMAGE)
        source: источник создания (USER/AUTO)
        shape_type: тип формы (RECTANGLE/POLYGON)
        polygon_points: координаты вершин полигона [(x1,y1), (x2,y2), ...] для POLYGON
        image_file: путь к сохранённому кропу блока
        ocr_text: результат OCR распознавания
        prompt: промпт для OCR (dict с ключами system/user)
        hint: подсказка пользователя для IMAGE блока (описание содержимого)
        pdfplumber_text: сырой текст извлечённый pdfplumber для блока
        linked_block_id: ID связанного блока (для IMAGE+TEXT)
        category_id: ID категории изображения (для IMAGE блоков)
        category_code: код категории изображения (для IMAGE блоков)
        created_at: дата и время создания блока (ISO формат)
    """

    id: str
    page_index: int
    coords_px: Tuple[int, int, int, int]  # (x1, y1, x2, y2)
    coords_norm: Tuple[float, float, float, float]  # (x1, y1, x2, y2) в диапазоне 0..1
    block_type: BlockType
    source: BlockSource
    shape_type: ShapeType = ShapeType.RECTANGLE
    polygon_points: Optional[List[Tuple[int, int]]] = None  # Для полигонов
    image_file: Optional[str] = None
    ocr_text: Optional[str] = None
    prompt: Optional[dict] = None  # {"system": "...", "user": "..."}
    hint: Optional[str] = None  # Подсказка пользователя для IMAGE блока
    pdfplumber_text: Optional[str] = None  # Сырой текст pdfplumber
    linked_block_id: Optional[str] = None  # ID связанного блока
    category_id: Optional[str] = None  # ID категории изображения
    category_code: Optional[str] = None  # Код категории изображения (для сериализации)
    created_at: Optional[str] = None  # Дата создания (ISO формат)
    is_correction: bool = False  # Флаг корректировочного блока (требует повторного OCR)

    @staticmethod
    def generate_id() -> str:
        """Генерировать уникальный ID для блока в формате XXXX-XXXX-XXX"""
        return generate_armor_id()

    @staticmethod
    def px_to_norm(
        coords_px: Tuple[int, int, int, int], page_width: int, page_height: int
    ) -> Tuple[float, float, float, float]:
        """
        Конвертировать координаты из пикселей в нормализованные (0..1)

        Args:
            coords_px: координаты в пикселях (x1, y1, x2, y2)
            page_width: ширина страницы в пикселях
            page_height: высота страницы в пикселях

        Returns:
            Нормализованные координаты (x1, y1, x2, y2)
        """
        x1, y1, x2, y2 = coords_px
        return (x1 / page_width, y1 / page_height, x2 / page_width, y2 / page_height)

    @staticmethod
    def norm_to_px(
        coords_norm: Tuple[float, float, float, float],
        page_width: int,
        page_height: int,
    ) -> Tuple[int, int, int, int]:
        """
        Конвертировать нормализованные координаты (0..1) в пиксели

        Args:
            coords_norm: нормализованные координаты (x1, y1, x2, y2)
            page_width: ширина страницы в пикселях
            page_height: высота страницы в пикселях

        Returns:
            Координаты в пикселях (x1, y1, x2, y2)
        """
        x1, y1, x2, y2 = coords_norm
        return (
            int(x1 * page_width),
            int(y1 * page_height),
            int(x2 * page_width),
            int(y2 * page_height),
        )

    @classmethod
    def create(
        cls,
        page_index: int,
        coords_px: Tuple[int, int, int, int],
        page_width: int,
        page_height: int,
        block_type: BlockType,
        source: BlockSource,
        shape_type: ShapeType = ShapeType.RECTANGLE,
        polygon_points: Optional[List[Tuple[int, int]]] = None,
        image_file: Optional[str] = None,
        ocr_text: Optional[str] = None,
        block_id: Optional[str] = None,
        prompt: Optional[dict] = None,
        hint: Optional[str] = None,
        pdfplumber_text: Optional[str] = None,
        linked_block_id: Optional[str] = None,
    ) -> "Block":
        """
        Создать блок с автоматическим вычислением нормализованных координат

        Args:
            page_index: индекс страницы
            coords_px: координаты в пикселях (x1, y1, x2, y2)
            page_width: ширина страницы в пикселях
            page_height: высота страницы в пикселях
            block_type: тип блока
            source: источник создания
            shape_type: тип формы (прямоугольник/полигон)
            polygon_points: вершины полигона
            image_file: путь к кропу
            ocr_text: результат OCR
            block_id: ID блока (если None, генерируется автоматически)
            prompt: промпт для OCR
            hint: подсказка пользователя для IMAGE блока
            pdfplumber_text: сырой текст pdfplumber
            linked_block_id: ID связанного блока

        Returns:
            Новый экземпляр Block
        """
        coords_norm = cls.px_to_norm(coords_px, page_width, page_height)

        return cls(
            id=block_id or cls.generate_id(),
            page_index=page_index,
            coords_px=coords_px,
            coords_norm=coords_norm,
            block_type=block_type,
            source=source,
            shape_type=shape_type,
            polygon_points=polygon_points,
            image_file=image_file,
            ocr_text=ocr_text,
            prompt=prompt,
            hint=hint,
            pdfplumber_text=pdfplumber_text,
            linked_block_id=linked_block_id,
            created_at=get_moscow_time_str(),
        )

    def get_width_height_px(self) -> Tuple[int, int]:
        """Получить ширину и высоту блока в пикселях"""
        x1, y1, x2, y2 = self.coords_px
        return (x2 - x1, y2 - y1)

    def get_width_height_norm(self) -> Tuple[float, float]:
        """Получить ширину и высоту блока в нормализованных координатах"""
        x1, y1, x2, y2 = self.coords_norm
        return (x2 - x1, y2 - y1)

    def update_coords_px(
        self,
        new_coords_px: Tuple[int, int, int, int],
        page_width: int,
        page_height: int,
    ):
        """
        Обновить координаты в пикселях и пересчитать нормализованные

        Args:
            new_coords_px: новые координаты в пикселях
            page_width: ширина страницы
            page_height: высота страницы
        """
        self.coords_px = new_coords_px
        self.coords_norm = self.px_to_norm(new_coords_px, page_width, page_height)

    def to_dict(self) -> dict:
        """Сериализация в словарь для JSON"""
        result = {
            "id": self.id,
            "page_index": self.page_index,
            "coords_px": list(self.coords_px),
            "coords_norm": list(self.coords_norm),
            "block_type": self.block_type.value,
            "source": self.source.value,
            "shape_type": self.shape_type.value,
            "image_file": self.image_file,
            "ocr_text": self.ocr_text,
        }
        if self.polygon_points:
            result["polygon_points"] = [list(p) for p in self.polygon_points]
        if self.prompt:
            result["prompt"] = self.prompt
        if self.hint:
            result["hint"] = self.hint
        if self.pdfplumber_text:
            result["pdfplumber_text"] = self.pdfplumber_text
        if self.linked_block_id:
            result["linked_block_id"] = self.linked_block_id
        if self.category_id:
            result["category_id"] = self.category_id
        if self.category_code:
            result["category_code"] = self.category_code
        if self.created_at:
            result["created_at"] = self.created_at
        if self.is_correction:
            result["is_correction"] = self.is_correction
        return result

    @classmethod
    def from_dict(cls, data: dict, migrate_ids: bool = True) -> tuple["Block", bool]:
        """
        Десериализация из словаря.

        Args:
            data: словарь с данными блока
            migrate_ids: мигрировать UUID в armor ID формат

        Returns:
            (Block, was_migrated) - блок и флаг миграции
        """
        # Безопасное получение block_type с fallback на TEXT
        # TABLE конвертируется в TEXT для обратной совместимости
        raw_type = data["block_type"]
        if raw_type == "table":
            block_type = BlockType.TEXT
        else:
            try:
                block_type = BlockType(raw_type)
            except ValueError:
                block_type = BlockType.TEXT

        # Безопасное получение shape_type с fallback на RECTANGLE
        try:
            shape_type = ShapeType(data.get("shape_type", "rectangle"))
        except ValueError:
            shape_type = ShapeType.RECTANGLE

        # Получение polygon_points если есть
        polygon_points = None
        if "polygon_points" in data and data["polygon_points"]:
            polygon_points = [tuple(p) for p in data["polygon_points"]]

        # Миграция ID
        was_migrated = False
        block_id = data["id"]
        linked_block_id = data.get("linked_block_id")

        if migrate_ids:
            block_id, m1 = migrate_block_id(block_id)
            was_migrated = m1

            if linked_block_id:
                linked_block_id, m2 = migrate_block_id(linked_block_id)
                was_migrated = was_migrated or m2

        block = cls(
            id=block_id,
            page_index=data["page_index"],
            coords_px=tuple(data["coords_px"]),
            coords_norm=tuple(data["coords_norm"]),
            block_type=block_type,
            source=BlockSource(data["source"]),
            shape_type=shape_type,
            polygon_points=polygon_points,
            image_file=data.get("image_file"),
            ocr_text=data.get("ocr_text"),
            prompt=data.get("prompt"),
            hint=data.get("hint"),
            pdfplumber_text=data.get("pdfplumber_text"),
            linked_block_id=linked_block_id,
            category_id=data.get("category_id"),
            category_code=data.get("category_code"),
            created_at=data.get("created_at") or get_moscow_time_str(),
            is_correction=data.get("is_correction", False),
        )
        return block, was_migrated
