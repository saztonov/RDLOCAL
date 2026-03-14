"""
Сохранение и загрузка разметки
Работа с JSON-файлами для сохранения/загрузки annotations.json
"""

import json
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from rd_core.models import Document, get_moscow_time_str

logger = logging.getLogger(__name__)


# Текущая версия формата аннотаций
ANNOTATION_FORMAT_VERSION = 2

# Обязательные поля для блока
REQUIRED_BLOCK_FIELDS = {"id", "page_index", "coords_px", "block_type"}
# Поля добавленные в v2
V2_BLOCK_FIELDS = {"coords_norm", "source", "shape_type", "created_at"}


def is_flat_format(data) -> bool:
    """
    Проверить, является ли data плоским массивом блоков (v0 - legacy формат).

    v0 формат: просто список блоков [{id, page_index, coords_px, ...}, ...]
    без обёртки {pdf_path, pages}.
    """
    if not isinstance(data, list):
        return False
    if len(data) == 0:
        return False
    first = data[0]
    return isinstance(first, dict) and "page_index" in first


def _estimate_page_size(blocks: list) -> Tuple[int, int]:
    """
    Оценить размеры страницы по coords_px блоков.

    Returns:
        (width, height) - оценённые размеры или A4 default
    """
    if not blocks:
        return 1240, 1754  # A4 default @ 150 DPI

    max_x = max_y = 0
    for block in blocks:
        coords = block.get("coords_px", [0, 0, 0, 0])
        if len(coords) >= 4:
            max_x = max(max_x, coords[2])  # x2
            max_y = max(max_y, coords[3])  # y2

    # Добавить отступ для рамки страницы
    return max(max_x + 50, 1240), max(max_y + 50, 1754)


def migrate_flat_to_structured(blocks: list, pdf_path: str = "") -> dict:
    """
    Мигрировать плоский массив блоков (v0) в структурированный формат (v2).

    Args:
        blocks: Плоский список блоков с page_index
        pdf_path: Путь к PDF (опционально)

    Returns:
        Структурированные данные аннотации
    """
    # Группировать блоки по page_index
    pages_dict: dict = {}
    for block in blocks:
        page_idx = block.get("page_index", 0)
        if page_idx not in pages_dict:
            pages_dict[page_idx] = []
        pages_dict[page_idx].append(block)

    # Создать структуру pages
    max_page = max(pages_dict.keys()) if pages_dict else 0
    pages = []
    for i in range(max_page + 1):
        page_blocks = pages_dict.get(i, [])
        width, height = _estimate_page_size(page_blocks)

        pages.append({
            "page_number": i,
            "width": width,
            "height": height,
            "blocks": page_blocks,
        })

    logger.info(
        f"Мигрирован плоский формат v0: {len(blocks)} блоков → {len(pages)} страниц"
    )

    return {
        "pdf_path": pdf_path,
        "format_version": ANNOTATION_FORMAT_VERSION,
        "pages": pages,
    }


@dataclass
class MigrationResult:
    """Результат миграции аннотации"""

    success: bool
    migrated: bool  # True если формат был изменён
    errors: List[str]  # Критические ошибки
    warnings: List[str]  # Предупреждения о восстановленных полях

    @property
    def needs_save(self) -> bool:
        """Нужно ли пересохранить файл"""
        return self.success and self.migrated


def validate_annotation_structure(data: dict) -> Tuple[bool, List[str]]:
    """
    Проверить базовую структуру аннотации.

    Returns:
        (is_valid, errors)
    """
    errors = []

    if not isinstance(data, dict):
        return False, ["Аннотация должна быть объектом JSON"]

    if "pdf_path" not in data:
        errors.append("Отсутствует поле 'pdf_path'")

    if "pages" not in data:
        errors.append("Отсутствует поле 'pages'")
    elif not isinstance(data["pages"], list):
        errors.append("Поле 'pages' должно быть массивом")
    else:
        for i, page in enumerate(data["pages"]):
            if not isinstance(page, dict):
                errors.append(f"Страница {i} должна быть объектом")
                continue
            if "blocks" in page and not isinstance(page["blocks"], list):
                errors.append(f"Блоки страницы {i} должны быть массивом")

    return len(errors) == 0, errors


def detect_annotation_version(data: dict) -> int:
    """
    Определить версию формата аннотации.

    v1: Старый формат (нет coords_norm, source)
    v2: Текущий формат (все обязательные поля)
    """
    if "format_version" in data:
        return data["format_version"]

    # Проверяем первый блок для определения версии
    for page in data.get("pages", []):
        for block in page.get("blocks", []):
            # v2 имеет coords_norm и source
            if "coords_norm" in block and "source" in block:
                return 2
            # Если есть блоки без этих полей - это v1
            return 1

    # Пустой документ - считаем актуальным
    return ANNOTATION_FORMAT_VERSION


def migrate_block_v1_to_v2(
    block: dict, page_width: int, page_height: int
) -> Tuple[dict, List[str]]:
    """
    Мигрировать блок из v1 в v2.

    Returns:
        (migrated_block, warnings)
    """
    warnings = []
    migrated = block.copy()

    # Добавляем source если нет
    if "source" not in migrated:
        migrated["source"] = "user"
        warnings.append(f"Блок {block.get('id', '?')}: добавлено source='user'")

    # Добавляем shape_type если нет
    if "shape_type" not in migrated:
        migrated["shape_type"] = "rectangle"

    # Добавляем created_at если нет
    if "created_at" not in migrated:
        migrated["created_at"] = get_moscow_time_str()

    # Вычисляем coords_norm если нет
    if "coords_norm" not in migrated:
        coords_px = migrated.get("coords_px", [0, 0, 100, 100])
        if page_width > 0 and page_height > 0:
            migrated["coords_norm"] = [
                coords_px[0] / page_width,
                coords_px[1] / page_height,
                coords_px[2] / page_width,
                coords_px[3] / page_height,
            ]
            warnings.append(f"Блок {block.get('id', '?')}: вычислены coords_norm")
        else:
            # Fallback - нормализованные координаты 0..1
            migrated["coords_norm"] = [0.0, 0.0, 0.1, 0.1]
            warnings.append(
                f"Блок {block.get('id', '?')}: coords_norm установлены по умолчанию (нет размеров страницы)"
            )

    return migrated, warnings


def migrate_annotation_data(data: dict) -> Tuple[dict, MigrationResult]:
    """
    Мигрировать аннотацию в актуальный формат.

    Returns:
        (migrated_data, result)
    """
    # Проверяем базовую структуру
    is_valid, errors = validate_annotation_structure(data)
    if not is_valid:
        return data, MigrationResult(
            success=False, migrated=False, errors=errors, warnings=[]
        )

    version = detect_annotation_version(data)

    # Уже актуальная версия
    if version >= ANNOTATION_FORMAT_VERSION:
        return data, MigrationResult(
            success=True, migrated=False, errors=[], warnings=[]
        )

    # Миграция v1 -> v2
    all_warnings = []
    migrated_data = data.copy()
    migrated_data["format_version"] = ANNOTATION_FORMAT_VERSION
    migrated_pages = []

    for page in data.get("pages", []):
        page_width = page.get("width", 0)
        page_height = page.get("height", 0)

        migrated_page = page.copy()
        migrated_blocks = []

        for block in page.get("blocks", []):
            # Проверяем обязательные поля
            missing = REQUIRED_BLOCK_FIELDS - set(block.keys())
            if missing:
                all_warnings.append(f"Блок пропущен - отсутствуют поля: {missing}")
                continue

            migrated_block, warnings = migrate_block_v1_to_v2(
                block, page_width, page_height
            )
            migrated_blocks.append(migrated_block)
            all_warnings.extend(warnings)

        migrated_page["blocks"] = migrated_blocks
        migrated_pages.append(migrated_page)

    migrated_data["pages"] = migrated_pages

    logger.info(f"Аннотация мигрирована v{version} -> v{ANNOTATION_FORMAT_VERSION}")

    return migrated_data, MigrationResult(
        success=True, migrated=True, errors=[], warnings=all_warnings
    )


class AnnotationIO:
    """Класс для работы с аннотациями (загрузка, сохранение, миграция файлов)"""

    @staticmethod
    def save_annotation(document: Document, file_path: str) -> None:
        """
        Сохранить разметку Document в JSON

        Args:
            document: экземпляр Document
            file_path: путь к выходному JSON-файлу
        """
        try:
            data = document.to_dict()
            data["format_version"] = ANNOTATION_FORMAT_VERSION
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"Разметка сохранена: {file_path}")
        except Exception as e:
            logger.error(f"Ошибка сохранения разметки: {e}")
            raise

    @staticmethod
    def load_annotation(
        file_path: str, migrate_ids: bool = True
    ) -> tuple[Optional[Document], bool]:
        """
        Загрузить разметку Document из JSON

        Args:
            file_path: путь к JSON-файлу
            migrate_ids: мигрировать legacy UUID в armor ID формат

        Returns:
            (Document, was_migrated) - документ и флаг миграции ID
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            doc, was_migrated = Document.from_dict(data, migrate_ids)
            logger.info(
                f"Разметка загружена: {file_path}"
                + (" (ID мигрированы)" if was_migrated else "")
            )
            return doc, was_migrated
        except Exception as e:
            logger.error(f"Ошибка загрузки разметки: {e}")
            return None, False

    @staticmethod
    def load_and_migrate(file_path: str) -> Tuple[Optional[Document], MigrationResult]:
        """
        Загрузить аннотацию с автоматической миграцией формата.

        Args:
            file_path: путь к JSON-файлу

        Returns:
            (Document, MigrationResult)
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return None, MigrationResult(
                success=False,
                migrated=False,
                errors=[f"Ошибка парсинга JSON: {e}"],
                warnings=[],
            )
        except Exception as e:
            return None, MigrationResult(
                success=False,
                migrated=False,
                errors=[f"Ошибка чтения файла: {e}"],
                warnings=[],
            )

        # Проверка на плоский формат v0 (legacy)
        v0_migrated = False
        if is_flat_format(data):
            # Извлечь pdf_path из имени файла аннотации
            # annotation_path: "doc_annotation.json" → pdf_path: "doc.pdf"
            from pathlib import Path

            ann_path = Path(file_path)
            pdf_name = ann_path.stem.replace("_annotation", "") + ".pdf"
            pdf_path = str(ann_path.parent / pdf_name)

            data = migrate_flat_to_structured(data, pdf_path)
            v0_migrated = True

        # Миграция данных v1 → v2
        migrated_data, result = migrate_annotation_data(data)

        if not result.success:
            return None, result

        # Конвертация в Document (с миграцией ID)
        try:
            doc, ids_migrated = Document.from_dict(migrated_data, migrate_ids=True)

            # Объединить флаги миграции
            format_migration = v0_migrated or result.migrated
            any_migration = format_migration or ids_migrated
            warnings = result.warnings.copy()

            if v0_migrated:
                warnings.insert(0, "Плоский формат v0 мигрирован в структурированный v2")
            if ids_migrated:
                warnings.append("ID блоков мигрированы в armor формат")

            if any_migration:
                result = MigrationResult(
                    success=True,
                    migrated=True,
                    errors=[],
                    warnings=warnings,
                )

            setattr(doc, "_prefer_coords_px", format_migration)
            return doc, result
        except Exception as e:
            return None, MigrationResult(
                success=False,
                migrated=False,
                errors=[f"Ошибка конвертации в Document: {e}"],
                warnings=result.warnings,
            )
