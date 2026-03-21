"""
Streaming обработка PDF через fitz (PyMuPDF)
Оптимизация памяти: страницы обрабатываются по одной и сразу освобождаются
"""
from __future__ import annotations

import gc
import math
from typing import Dict, List, Optional, Tuple

import fitz
from PIL import Image, ImageDraw

from rd_core.pdf_utils import normalize_coords_norm

from .logging_config import get_logger
from .memory_utils import get_pil_image_size_mb
from .settings import settings

logger = get_logger(__name__)

# Константы из настроек
PDF_RENDER_DPI = settings.pdf_render_dpi
PDF_RENDER_ZOOM = PDF_RENDER_DPI / 72.0
MAX_STRIP_HEIGHT = settings.max_strip_height
MAX_SINGLE_BLOCK_HEIGHT = settings.max_strip_height
MAX_IMAGE_PIXELS = 400_000_000

# Увеличиваем лимит PIL
Image.MAX_IMAGE_PIXELS = 500_000_000


class StreamingPDFProcessor:
    """
    Streaming процессор PDF с оптимизацией памяти.
    Обрабатывает страницы последовательно, освобождая память после каждой.
    """

    def __init__(self, pdf_path: str, zoom: float = PDF_RENDER_ZOOM):
        self.pdf_path = pdf_path
        self.zoom = zoom
        self._doc: Optional[fitz.Document] = None
        self._current_page_idx: int = -1
        self._current_page_image: Optional[Image.Image] = None

    def __enter__(self):
        self._doc = fitz.open(self.pdf_path)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._release_page_image()
        if self._doc:
            self._doc.close()
            self._doc = None
        gc.collect()

    @property
    def page_count(self) -> int:
        return len(self._doc) if self._doc else 0

    def _release_page_image(self):
        """Освободить текущее изображение страницы"""
        if self._current_page_image:
            self._current_page_image.close()
            self._current_page_image = None
            self._current_page_idx = -1

    def _get_effective_zoom(self, page: fitz.Page) -> float:
        """Вычислить zoom с учётом лимита пикселей"""
        rect = page.rect
        estimated = (rect.width * self.zoom) * (rect.height * self.zoom)
        if estimated > MAX_IMAGE_PIXELS:
            return (MAX_IMAGE_PIXELS / (rect.width * rect.height)) ** 0.5
        return self.zoom

    def get_page_image(self, page_idx: int) -> Optional[Image.Image]:
        """
        Получить изображение страницы (lazy loading).
        Кэширует текущую страницу, освобождает предыдущую.
        """
        if page_idx == self._current_page_idx and self._current_page_image:
            return self._current_page_image

        # Освобождаем предыдущую
        self._release_page_image()

        if not self._doc or page_idx < 0 or page_idx >= len(self._doc):
            return None

        try:
            page = self._doc[page_idx]
            effective_zoom = self._get_effective_zoom(page)
            mat = fitz.Matrix(effective_zoom, effective_zoom)

            # Рендерим напрямую в samples (RGB) вместо PNG
            pix = page.get_pixmap(matrix=mat)

            # Прямое создание Image из samples (быстрее чем через PNG)
            if pix.alpha:
                mode = "RGBA"
            else:
                mode = "RGB"

            self._current_page_image = Image.frombytes(
                mode, (pix.width, pix.height), pix.samples
            )
            self._current_page_idx = page_idx

            # Логируем размер страницы
            page_mb = get_pil_image_size_mb(self._current_page_image)
            logger.info(
                f"Page {page_idx} rendered: {pix.width}x{pix.height} (~{page_mb:.1f} MB, zoom={effective_zoom:.2f})"
            )

            # Освобождаем pixmap
            pix = None

            return self._current_page_image

        except Exception as e:
            logger.error(f"Error rendering page {page_idx}: {e}")
            return None

    def get_page_dimensions(self, page_idx: int) -> Optional[Tuple[int, int]]:
        """Получить размеры страницы"""
        if not self._doc or page_idx < 0 or page_idx >= len(self._doc):
            return None
        page = self._doc[page_idx]
        rect = page.rect
        zoom = self._get_effective_zoom(page)
        return (int(rect.width * zoom), int(rect.height * zoom))

    def crop_block_image(self, block, padding: int = 5) -> Optional[Image.Image]:
        """Вырезать кроп блока из текущей страницы"""
        page_image = self.get_page_image(block.page_index)
        if not page_image:
            return None

        from rd_core.models import ShapeType

        normalized_coords = normalize_coords_norm(block.coords_norm)
        if normalized_coords is None:
            logger.warning(
                "Invalid normalized coordinates for block %s on page %s: %s",
                block.id,
                block.page_index,
                block.coords_norm,
            )
            return None

        nx1, ny1, nx2, ny2 = normalized_coords
        img_w, img_h = page_image.width, page_image.height

        x1, y1 = int(nx1 * img_w), int(ny1 * img_h)
        x2, y2 = int(nx2 * img_w), int(ny2 * img_h)

        x1, y1 = max(0, x1 - padding), max(0, y1 - padding)
        x2, y2 = min(img_w, x2 + padding), min(img_h, y2 + padding)

        if x2 <= x1 or y2 <= y1:
            logger.warning(
                "Empty raster crop for block %s on page %s after padding: %s",
                block.id,
                block.page_index,
                (x1, y1, x2, y2),
            )
            return None

        if block.shape_type == ShapeType.RECTANGLE or not block.polygon_points:
            return page_image.crop((x1, y1, x2, y2)).copy()

        # Полигон с маской
        crop_w, crop_h = x2 - x1, y2 - y1
        orig_x1, orig_y1, orig_x2, orig_y2 = block.coords_px
        bbox_w, bbox_h = orig_x2 - orig_x1, orig_y2 - orig_y1

        if crop_w <= 0 or crop_h <= 0 or bbox_w <= 0 or bbox_h <= 0:
            return page_image.crop((x1, y1, x2, y2)).copy()

        adjusted_points = []
        for px, py in block.polygon_points:
            norm_px = (px - orig_x1) / bbox_w if bbox_w else 0
            norm_py = (py - orig_y1) / bbox_h if bbox_h else 0
            adjusted_points.append((norm_px * crop_w, norm_py * crop_h))

        mask = Image.new("L", (crop_w, crop_h), 0)
        ImageDraw.Draw(mask).polygon(adjusted_points, fill=255)

        cropped = page_image.crop((x1, y1, x2, y2))
        result = Image.new("RGB", cropped.size, (255, 255, 255))
        result.paste(cropped, mask=mask)
        mask.close()

        return result

    def crop_block_to_pdf(
        self, block, output_path: str, padding_pt: int = 2
    ) -> Optional[str]:
        """Вырезать блок как PDF"""
        if not self._doc:
            return None

        from rd_core.models import ShapeType

        try:
            page = self._doc[block.page_index]
            rect = page.rect
            rotation = page.rotation

            # Используем cropbox — именно его рендерит get_pixmap(),
            # и именно относительно него вычислены coords_norm.
            # page.rect может отличаться от cropbox при ротации,
            # а mediabox может отличаться при наличии CropBox в PDF (CAD-чертежи).
            cropbox = page.cropbox

            # Диагностика расхождений MediaBox/CropBox/rect
            mediabox = page.mediabox
            if (
                abs(cropbox.x0 - mediabox.x0) > 0.5
                or abs(cropbox.y0 - mediabox.y0) > 0.5
                or abs(cropbox.width - mediabox.width) > 0.5
                or abs(cropbox.height - mediabox.height) > 0.5
                or abs(cropbox.x0 - rect.x0) > 0.5
                or abs(cropbox.y0 - rect.y0) > 0.5
            ):
                logger.info(
                    "PDF crop box mismatch block=%s page=%d: "
                    "rect=(%.1f,%.1f,%.1f,%.1f) "
                    "cropbox=(%.1f,%.1f,%.1f,%.1f) "
                    "mediabox=(%.1f,%.1f,%.1f,%.1f) rotation=%d",
                    block.id,
                    block.page_index,
                    rect.x0, rect.y0, rect.x1, rect.y1,
                    cropbox.x0, cropbox.y0, cropbox.x1, cropbox.y1,
                    mediabox.x0, mediabox.y0, mediabox.x1, mediabox.y1,
                    rotation,
                )

            normalized_coords = normalize_coords_norm(block.coords_norm)
            if normalized_coords is None:
                logger.warning(
                    "Skipping PDF crop for block %s on page %s due to invalid coords: %s",
                    block.id,
                    block.page_index,
                    block.coords_norm,
                )
                return None

            nx1, ny1, nx2, ny2 = normalized_coords

            # Пересчёт coords_norm из пространства page.rect в пространство cropbox
            # Нужен когда cropbox != rect (CAD-чертежи с нестандартным origin)
            if (abs(rect.x0 - cropbox.x0) > 0.5 or abs(rect.y0 - cropbox.y0) > 0.5
                    or abs(rect.width - cropbox.width) > 0.5 or abs(rect.height - cropbox.height) > 0.5):
                if rect.width > 0 and rect.height > 0 and cropbox.width > 0 and cropbox.height > 0:
                    abs_x1 = rect.x0 + nx1 * rect.width
                    abs_y1 = rect.y0 + ny1 * rect.height
                    abs_x2 = rect.x0 + nx2 * rect.width
                    abs_y2 = rect.y0 + ny2 * rect.height
                    nx1 = (abs_x1 - cropbox.x0) / cropbox.width
                    ny1 = (abs_y1 - cropbox.y0) / cropbox.height
                    nx2 = (abs_x2 - cropbox.x0) / cropbox.width
                    ny2 = (abs_y2 - cropbox.y0) / cropbox.height
                    nx1 = max(0.0, min(1.0, nx1))
                    ny1 = max(0.0, min(1.0, ny1))
                    nx2 = max(0.0, min(1.0, nx2))
                    ny2 = max(0.0, min(1.0, ny2))

            x1_pt = max(cropbox.x0, cropbox.x0 + nx1 * cropbox.width - padding_pt)
            y1_pt = max(cropbox.y0, cropbox.y0 + ny1 * cropbox.height - padding_pt)
            x2_pt = min(cropbox.x1, cropbox.x0 + nx2 * cropbox.width + padding_pt)
            y2_pt = min(cropbox.y1, cropbox.y0 + ny2 * cropbox.height + padding_pt)

            if x2_pt <= x1_pt or y2_pt <= y1_pt:
                logger.warning(
                    "Skipping PDF crop for block %s on page %s due to empty clip: %s",
                    block.id,
                    block.page_index,
                    (x1_pt, y1_pt, x2_pt, y2_pt),
                )
                return None

            clip_rect = fitz.Rect(x1_pt, y1_pt, x2_pt, y2_pt)

            if rotation != 0:
                clip_rect = clip_rect * page.derotation_matrix
                clip_rect.normalize()

            rect_values = (
                clip_rect.x0,
                clip_rect.y0,
                clip_rect.x1,
                clip_rect.y1,
            )
            if (
                not all(math.isfinite(v) for v in rect_values)
                or clip_rect.width <= 0
                or clip_rect.height <= 0
            ):
                logger.warning(
                    "Skipping PDF crop for block %s on page %s due to invalid rotated clip: %s",
                    block.id,
                    block.page_index,
                    rect_values,
                )
                return None

            if rotation in (90, 270):
                crop_width, crop_height = clip_rect.height, clip_rect.width
            else:
                crop_width, crop_height = clip_rect.width, clip_rect.height

            if crop_width <= 0 or crop_height <= 0:
                return None

            logger.debug(
                "PDF crop block=%s: coords_norm=(%.4f,%.4f,%.4f,%.4f) "
                "clip=(%.1f,%.1f,%.1f,%.1f) size=%.1fx%.1f",
                block.id, nx1, ny1, nx2, ny2,
                clip_rect.x0, clip_rect.y0, clip_rect.x1, clip_rect.y1,
                crop_width, crop_height,
            )

            new_doc = fitz.open()
            new_page = new_doc.new_page(width=crop_width, height=crop_height)
            new_page.show_pdf_page(
                new_page.rect,
                self._doc,
                block.page_index,
                clip=clip_rect,
                rotate=-rotation,
            )

            if block.shape_type == ShapeType.POLYGON and block.polygon_points:
                orig_x1, orig_y1, orig_x2, orig_y2 = block.coords_px
                bbox_w, bbox_h = orig_x2 - orig_x1, orig_y2 - orig_y1

                if bbox_w > 0 and bbox_h > 0:
                    polygon_pts = []
                    for px, py in block.polygon_points:
                        norm_px = (px - orig_x1) / bbox_w if bbox_w else 0
                        norm_py = (py - orig_y1) / bbox_h if bbox_h else 0
                        polygon_pts.append(
                            fitz.Point(norm_px * crop_width, norm_py * crop_height)
                        )

                    shape = new_page.new_shape()
                    shape.draw_rect(new_page.rect)
                    if polygon_pts:
                        shape.draw_polyline(polygon_pts + [polygon_pts[0]])
                    shape.finish(color=None, fill=(1, 1, 1), even_odd=True)
                    shape.commit()

            new_doc.save(output_path, deflate=True, garbage=4)
            new_doc.close()

            return output_path

        except Exception as e:
            logger.error(f"PDF crop error {block.id}: {e}")
            return None


def split_large_crop(
    crop: Image.Image, max_height: int = MAX_SINGLE_BLOCK_HEIGHT, overlap: int = 100
) -> List[Image.Image]:
    """Разделить большой кроп на части"""
    if crop.height <= max_height:
        return [crop]

    parts = []
    y = 0
    step = max_height - overlap

    while y < crop.height:
        y_end = min(y + max_height, crop.height)
        parts.append(crop.crop((0, y, crop.width, y_end)).copy())
        y += step
        if crop.height - y < overlap:
            break

    return parts


BLOCK_SEPARATOR_HEIGHT = 60


def create_block_separator(
    block_id: str, width: int, height: int = BLOCK_SEPARATOR_HEIGHT
) -> Image.Image:
    """
    Создать разделитель с белым текстом block_id на черном фоне.
    Высота 60px, шрифт 36px, выравнивание по левому краю.
    Формат: BLOCK: XXXX-XXXX-XXX (OCR-устойчивый код)
    """
    from PIL import ImageFont

    from rd_core.models.armor_id import encode_block_id

    separator = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(separator)

    armor_code = encode_block_id(block_id)
    text = f"BLOCK: {armor_code}"

    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except (IOError, OSError):
        try:
            font = ImageFont.truetype("DejaVuSansMono.ttf", 36)
        except (IOError, OSError):
            font = ImageFont.load_default(size=36)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_height = bbox[3] - bbox[1]

    x = 50
    y = (height - text_height) // 2

    draw.text((x, y), text, fill=(255, 255, 255), font=font)
    return separator


def merge_crops_vertically(
    crops: List[Image.Image], gap: int = 20, block_ids: Optional[List[str]] = None
) -> Image.Image:
    """
    Объединить кропы вертикально с опциональными разделителями block_id.
    Разделитель вставляется только при смене block_id (не перед каждой частью блока).
    """
    if not crops:
        raise ValueError("Empty crops list")

    use_separators = block_ids is not None and len(block_ids) == len(crops)
    max_width = max(c.width for c in crops)

    # Считаем количество уникальных переходов между блоками
    if use_separators:
        separator_count = 0
        prev_id = None
        for bid in block_ids:
            if bid != prev_id:
                separator_count += 1
                prev_id = bid
        separator_height = BLOCK_SEPARATOR_HEIGHT
        total_height = (
            sum(c.height for c in crops)
            + separator_height * separator_count
            + gap * (len(crops) - separator_count)
        )
    else:
        total_height = sum(c.height for c in crops) + gap * (len(crops) - 1)

    merged = Image.new("RGB", (max_width, total_height), (255, 255, 255))
    y_offset = 0
    prev_block_id = None

    for i, crop in enumerate(crops):
        if use_separators:
            current_block_id = block_ids[i]
            if current_block_id != prev_block_id:
                # Новый блок - вставляем разделитель
                separator = create_block_separator(current_block_id, max_width)
                merged.paste(separator, (0, y_offset))
                y_offset += separator.height
                prev_block_id = current_block_id
            elif i > 0:
                # Часть того же блока - только gap
                y_offset += gap
        elif i > 0:
            y_offset += gap

        x_offset = (max_width - crop.width) // 2
        if crop.mode in ("RGBA", "LA"):
            crop = crop.convert("RGB")
        merged.paste(crop, (x_offset, y_offset))
        y_offset += crop.height

    return merged


def get_page_dimensions_streaming(pdf_path: str) -> Dict[int, Tuple[int, int]]:
    """Получить размеры всех страниц без полного рендеринга"""
    dims = {}
    with StreamingPDFProcessor(pdf_path) as processor:
        for i in range(processor.page_count):
            d = processor.get_page_dimensions(i)
            if d:
                dims[i] = d
    return dims
