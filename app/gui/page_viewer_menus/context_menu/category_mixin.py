"""Миксин для работы с категориями в контекстном меню"""

# Фиксированные категории изображений (default + stamp)
HARDCODED_CATEGORIES = [
    {"id": "default", "name": "По умолчанию", "code": "default", "is_default": True},
    {"id": "stamp", "name": "Штамп", "code": "stamp", "is_default": False},
]


class CategoryMixin:
    """Миксин для работы с категориями изображений (хардкод)"""

    def _get_image_categories(self):
        """Получить фиксированный список категорий"""
        return HARDCODED_CATEGORIES
