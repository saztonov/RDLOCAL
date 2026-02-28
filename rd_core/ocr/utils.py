"""Утилиты для работы с изображениями в OCR"""
import base64
import io
import logging
import re

from PIL import Image

_logger = logging.getLogger(__name__)

# ── Очистка <think> блоков из ответов LLM ─────────────────────────
_THINK_BLOCK_RE = re.compile(r'<think>.*?</think>', re.DOTALL | re.IGNORECASE)
_THINK_UNCLOSED_RE = re.compile(r'<think>.*$', re.DOTALL | re.IGNORECASE)
_THINK_ORPHAN_CLOSE_RE = re.compile(r'^.*?</think>', re.DOTALL | re.IGNORECASE)


def strip_think_tags(text: str, backend_name: str = "LLM") -> str:
    """Удалить <think>...</think> блоки (reasoning) из ответа LLM.

    Обрабатывает:
    - Полные <think>...</think> блоки
    - Незакрытый <think> (модель не завершила reasoning)
    - Сиротский </think> без открывающего <think>
    """
    if not text or ('<think' not in text.lower() and '</think' not in text.lower()):
        return text

    original_len = len(text)
    cleaned = text
    cleaned = _THINK_BLOCK_RE.sub('', cleaned)
    cleaned = _THINK_UNCLOSED_RE.sub('', cleaned)
    cleaned = _THINK_ORPHAN_CLOSE_RE.sub('', cleaned)
    cleaned = cleaned.strip()

    removed_len = original_len - len(cleaned)
    if removed_len > 0:
        _logger.info(
            f"{backend_name}: удалены <think> блоки "
            f"(удалено {removed_len} симв. reasoning, "
            f"осталось {len(cleaned)} симв.)"
        )
    return cleaned


# ── Очистка не-тегированного reasoning ───────────────────────────
_REASONING_PREFIX_RE = re.compile(
    r'^(?:'
    r'\d+\.\s+\*\*|'                                                    # "1. **Analyze..."
    r'(?:Let me|I need to|I will|First[,.]|Looking at|Analyzing|'
    r'To (?:analyze|process|extract|transcribe))\b|'                    # English reasoning
    r'\*\*(?:Analyze|Step|Plan|Approach|Solution|Observation)\b|'        # **Analysis...
    r'(?:Давай|Мне нужно|Сначала|Анализируя|Рассмотрим)\b'              # Russian reasoning
    r')',
    re.IGNORECASE
)
_HTML_START_RE = re.compile(r'<(?:p|table|h[1-6]|div|ul|ol|span|br|hr|img)\b', re.IGNORECASE)


def strip_untagged_reasoning(text: str, backend_name: str = "LLM") -> str:
    """Обнаружить и отрезать не-тегированный reasoning перед HTML-контентом.

    Для случаев когда модель генерирует цепочку рассуждений БЕЗ <think> тегов,
    а затем HTML-контент. Обрезает всё до первого HTML-тега.
    """
    if not text:
        return text

    stripped = text.lstrip()

    # Быстрый выход: если текст начинается с HTML или code fence — всё хорошо
    if stripped.startswith('<') or stripped.startswith('```'):
        return text

    # Проверяем: начало похоже на reasoning?
    if not _REASONING_PREFIX_RE.match(stripped):
        return text

    # Ищем первый HTML-тег
    html_match = _HTML_START_RE.search(text)
    if not html_match:
        # Нет HTML вообще — возвращаем как есть
        return text

    reasoning_part = text[:html_match.start()]
    html_part = text[html_match.start():]

    _logger.warning(
        f"{backend_name}: обнаружен не-тегированный reasoning "
        f"({len(reasoning_part)} симв. обрезано), HTML: {len(html_part)} симв."
    )
    return html_part


def image_to_base64(image: Image.Image, max_size: int = 1500) -> str:
    """
    Конвертировать PIL Image в base64 с опциональным ресайзом

    Args:
        image: PIL изображение
        max_size: максимальный размер стороны

    Returns:
        Base64 строка
    """
    if image.width > max_size or image.height > max_size:
        ratio = min(max_size / image.width, max_size / image.height)
        new_size = (int(image.width * ratio), int(image.height * ratio))
        image = image.resize(new_size, Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def image_to_pdf_base64(image: Image.Image) -> str:
    """
    Конвертировать PIL Image в PDF base64 (векторное качество)

    Args:
        image: PIL изображение

    Returns:
        Base64 строка PDF
    """
    buffer = io.BytesIO()
    if image.mode == "RGBA":
        rgb_image = Image.new("RGB", image.size, (255, 255, 255))
        rgb_image.paste(image, mask=image.split()[3])
        image = rgb_image
    elif image.mode != "RGB":
        image = image.convert("RGB")

    image.save(buffer, format="PDF", resolution=300.0)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")
