"""Перечисления для моделей данных"""
from enum import Enum


class BlockType(Enum):
    """Типы блоков разметки (3 типа: текст, картинка, штамп)"""

    TEXT = "text"
    IMAGE = "image"
    STAMP = "stamp"


class BlockSource(Enum):
    """Источник создания блока"""

    USER = "user"  # Создан пользователем вручную
    AUTO = "auto"  # Создан автоматической сегментацией


class ShapeType(Enum):
    """Тип формы блока"""

    RECTANGLE = "rectangle"  # Прямоугольник
    POLYGON = "polygon"  # Многоугольник
