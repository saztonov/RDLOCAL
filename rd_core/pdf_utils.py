"""
Утилиты для работы с PDF
Загрузка PDF, рендеринг страниц в изображения через PyMuPDF
Извлечение текста через PyMuPDF
"""

import logging
import math
from pathlib import Path
from typing import Optional, Tuple

import fitz  # PyMuPDF
from PIL import Image

# Настройка логирования
logger = logging.getLogger(__name__)

# Увеличиваем лимит PIL для больших изображений (A0 при 300 DPI)
Image.MAX_IMAGE_PIXELS = 500_000_000

# DPI для рендеринга PDF (должен совпадать с сервером)
# Сервер использует PDF_DPI=300, zoom = DPI/72
PDF_RENDER_DPI = 300
PDF_RENDER_ZOOM = PDF_RENDER_DPI / 72.0  # ≈ 4.167

# DPI для предпросмотра (быстрый рендеринг)
PDF_PREVIEW_DPI = 150
PDF_PREVIEW_ZOOM = PDF_PREVIEW_DPI / 72.0  # ≈ 2.08


def _get_effective_render_zoom(page: fitz.Page, zoom: float) -> float:
    """Вычислить фактический zoom с учётом лимита по пикселям."""
    rect = page.rect
    max_pixels = 100_000_000
    estimated_pixels = (rect.width * zoom) * (rect.height * zoom)
    if estimated_pixels <= max_pixels:
        return zoom
    return (max_pixels / (rect.width * rect.height)) ** 0.5


def open_pdf(path: str) -> fitz.Document:
    """
    Открыть PDF-документ

    Args:
        path: путь к PDF-файлу

    Returns:
        fitz.Document - открытый PDF документ

    Raises:
        FileNotFoundError: если файл не найден
        ValueError: если файл не является PDF или повреждён
        Exception: для других ошибок открытия
    """
    pdf_path = Path(path)

    # Проверка существования файла
    if not pdf_path.exists():
        logger.error(f"PDF файл не найден: {path}")
        raise FileNotFoundError(f"PDF файл не найден: {path}")

    # Проверка расширения
    if pdf_path.suffix.lower() != ".pdf":
        logger.warning(f"Файл не имеет расширения .pdf: {path}")

    try:
        doc = fitz.open(path)
        logger.info(f"PDF открыт успешно: {path} (страниц: {len(doc)})")
        return doc
    except fitz.FileDataError as e:
        logger.error(
            f"Файл не является корректным PDF или повреждён: {path}, ошибка: {e}"
        )
        raise ValueError(
            f"Файл не является корректным PDF или повреждён: {path}"
        ) from e
    except Exception as e:
        logger.error(f"Неожиданная ошибка при открытии PDF {path}: {e}")
        raise


def render_page_to_image(
    doc: fitz.Document, page_index: int, zoom: float = PDF_RENDER_ZOOM
) -> Image.Image:
    """
    Рендеринг страницы PDF в изображение PIL

    Args:
        doc: открытый PDF документ
        page_index: индекс страницы (начиная с 0)
        zoom: коэффициент масштабирования (2.0 = 200% = 144 DPI)
              Масштабирование применяется одинаково по X и Y для сохранения пропорций

    Returns:
        PIL.Image.Image - отрендеренная страница

    Raises:
        IndexError: если page_index выходит за пределы документа
        ValueError: если zoom <= 0
        Exception: для других ошибок рендеринга
    """
    # Валидация zoom
    if zoom <= 0:
        logger.error(f"Некорректный zoom: {zoom}, должен быть > 0")
        raise ValueError(f"Zoom должен быть положительным числом, получено: {zoom}")

    # Проверка индекса страницы
    page_count = len(doc)
    if page_index < 0 or page_index >= page_count:
        logger.error(
            f"Индекс страницы {page_index} выходит за пределы документа (0-{page_count-1})"
        )
        raise IndexError(
            f"Индекс страницы {page_index} выходит за пределы (доступно: 0-{page_count-1})"
        )

    try:
        # Получаем страницу
        page = doc[page_index]

        # Адаптивный zoom для больших страниц (лимит ~100 млн пикселей для скорости)
        effective_zoom = _get_effective_render_zoom(page, zoom)
        if effective_zoom != zoom:
            logger.warning(
                f"Страница {page_index} слишком большая, zoom снижен: {zoom:.2f} -> {effective_zoom:.2f}"
            )

        # Создаём матрицу масштабирования (одинаковый zoom по X и Y для сохранения пропорций)
        mat = fitz.Matrix(effective_zoom, effective_zoom)

        # Рендерим страницу в pixmap
        pix = page.get_pixmap(matrix=mat)

        logger.debug(
            f"Страница {page_index} отрендерена: {pix.width}x{pix.height}px, zoom={effective_zoom}"
        )

        # Прямая конвертация в PIL Image без PNG encoding/decoding
        if pix.alpha:
            img = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
        else:
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        return img

    except IndexError:
        # Перебрасываем IndexError дальше
        raise
    except Exception as e:
        logger.error(f"Ошибка рендеринга страницы {page_index}: {e}")
        raise Exception(f"Не удалось отрендерить страницу {page_index}") from e


# ========== КЛАСС-ОБЁРТКА ДЛЯ СОВМЕСТИМОСТИ ==========


class PDFDocument:
    """
    Обёртка над PyMuPDF для работы с PDF-документами
    Использует функции выше для реализации
    """

    def __init__(self, pdf_path: str):
        """
        Инициализация PDF-документа

        Args:
            pdf_path: путь к PDF-файлу
        """
        self.pdf_path = pdf_path
        self.doc: Optional[fitz.Document] = None
        self.page_count = 0

    def open(self) -> bool:
        """
        Открыть PDF-документ

        Returns:
            True если успешно открыт, False в случае ошибки
        """
        try:
            self.doc = open_pdf(self.pdf_path)
            self.page_count = len(self.doc)
            return True
        except Exception as e:
            logger.error(f"Не удалось открыть PDF через PDFDocument: {e}")
            return False

    def close(self):
        """Закрыть PDF-документ"""
        if self.doc:
            self.doc.close()
            self.doc = None
            logger.debug(f"PDF документ закрыт: {self.pdf_path}")

    def render_page(
        self, page_number: int, zoom: float = PDF_PREVIEW_ZOOM
    ) -> Optional[Image.Image]:
        """
        Рендеринг страницы в изображение PIL

        Args:
            page_number: номер страницы (начиная с 0)
            zoom: коэффициент масштабирования (по умолчанию preview 150 DPI)

        Returns:
            PIL.Image или None в случае ошибки
        """
        if not self.doc or page_number < 0 or page_number >= self.page_count:
            logger.warning(
                f"Некорректный запрос рендеринга: page={page_number}, doc_opened={self.doc is not None}"
            )
            return None

        try:
            return render_page_to_image(self.doc, page_number, zoom)
        except Exception as e:
            logger.error(f"Ошибка рендеринга страницы {page_number}: {e}")
            return None

    def get_page_dimensions(
        self, page_number: int, zoom: float = PDF_PREVIEW_ZOOM
    ) -> Optional[tuple]:
        """
        Получить размеры страницы после рендеринга

        Args:
            page_number: номер страницы
            zoom: коэффициент масштабирования

        Returns:
            (width, height) или None
        """
        if not self.doc or page_number < 0 or page_number >= self.page_count:
            return None

        try:
            page = self.doc[page_number]
            rect = page.rect
            effective_zoom = _get_effective_render_zoom(page, zoom)
            width = int(rect.width * effective_zoom)
            height = int(rect.height * effective_zoom)
            return (width, height)
        except Exception as e:
            logger.error(f"Ошибка получения размеров страницы {page_number}: {e}")
            return None

    def __enter__(self):
        """Context manager entry"""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()


# ========== ФУНКЦИИ ИЗВЛЕЧЕНИЯ ТЕКСТА PyMuPDF ==========


def normalize_coords_norm(
    coords_norm: Tuple[float, float, float, float],
) -> Optional[Tuple[float, float, float, float]]:
    """Clamp and normalize block bounds to a valid 0..1 rectangle.

    Returns None for invalid/degenerate coordinates (NaN, Inf, zero-area).
    """
    try:
        x1, y1, x2, y2 = (float(v) for v in coords_norm)
    except (TypeError, ValueError):
        return None

    if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
        return None

    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))

    if x2 - x1 <= 1e-6 or y2 - y1 <= 1e-6:
        return None

    return x1, y1, x2, y2


def extract_text_pdfplumber(
    pdf_path: str,
    page_index: int,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> str:
    """
    Извлечь текст из PDF страницы с помощью PyMuPDF

    Args:
        pdf_path: путь к PDF файлу
        page_index: индекс страницы (начиная с 0)
        bbox: ограничивающий прямоугольник (x0, y0, x1, y1) в PDF-координатах
              Если None - извлекается весь текст страницы

    Returns:
        Извлечённый текст (может быть пустой строкой)
    """
    try:
        doc = fitz.open(pdf_path)
        if page_index < 0 or page_index >= len(doc):
            logger.warning(f"Страница {page_index} не существует в PDF")
            doc.close()
            return ""

        page = doc[page_index]

        if bbox:
            # PyMuPDF использует fitz.Rect(x0, y0, x1, y1)
            rect = fitz.Rect(bbox)
            text = page.get_text("text", clip=rect) or ""
        else:
            text = page.get_text("text") or ""

        doc.close()
        return text.strip()

    except Exception as e:
        logger.error(f"Ошибка извлечения текста PyMuPDF: {e}")
        return ""


def extract_text_for_block(
    pdf_path: str,
    page_index: int,
    coords_norm: Tuple[float, float, float, float],
    page_width_pdf: float,
    page_height_pdf: float,
) -> str:
    """
    Извлечь текст для блока используя нормализованные координаты

    Args:
        pdf_path: путь к PDF файлу
        page_index: индекс страницы
        coords_norm: нормализованные координаты блока (x1, y1, x2, y2) в диапазоне 0..1
        page_width_pdf: ширина страницы в PDF-единицах
        page_height_pdf: высота страницы в PDF-единицах

    Returns:
        Извлечённый текст
    """
    # Конвертируем нормализованные координаты в PDF-координаты
    normalized = normalize_coords_norm(coords_norm)
    if normalized is None:
        return ""

    x0_norm, y0_norm, x1_norm, y1_norm = normalized

    x0 = x0_norm * page_width_pdf
    y0 = y0_norm * page_height_pdf
    x1 = x1_norm * page_width_pdf
    y1 = y1_norm * page_height_pdf

    # PyMuPDF использует (x0, y0, x1, y1)
    bbox = (x0, y0, x1, y1)

    return extract_text_pdfplumber(pdf_path, page_index, bbox)


def get_pdf_page_size(pdf_path: str, page_index: int) -> Optional[Tuple[float, float]]:
    """
    Получить размер страницы PDF в PDF-единицах (points)

    Args:
        pdf_path: путь к PDF файлу
        page_index: индекс страницы

    Returns:
        (width, height) в PDF-единицах или None при ошибке
    """
    try:
        doc = fitz.open(pdf_path)
        if page_index < 0 or page_index >= len(doc):
            doc.close()
            return None
        page = doc[page_index]
        rect = page.rect
        doc.close()
        return (rect.width, rect.height)
    except Exception as e:
        logger.error(f"Ошибка получения размера страницы: {e}")
        return None
