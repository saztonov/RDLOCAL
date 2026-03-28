"""HTML парсинг для OCR результатов"""
from __future__ import annotations

import re

from .block_id_matcher import (
    ARMOR_BLOCK_MARKER_RE,
    BLOCK_MARKER_RE,
    match_armor_code,
    match_uuid,
)
from .logging_config import get_logger

logger = get_logger(__name__)


def _extract_blocks_by_div_structure(
    html_text: str,
    expected_ids: list[str],
    expected_set: set[str],
    segments: dict[str, str],
    meta: dict[str, dict],
    score_cutoff: int = 90,
) -> None:
    """
    Фоллбек: извлекает блоки по div.block структуре HTML.
    Полезно для image блоков, где маркер [[BLOCK ID:...]] отсутствует.
    """
    # Паттерн для извлечения блоков: ищем div.block-content и следующий </div></div>
    block_pattern = re.compile(
        r'<div[^>]*class="[^"]*block\s+block-type-(\w+)[^"]*"[^>]*>\s*'
        r'<div[^>]*class="[^"]*block-header[^"]*"[^>]*>([^<]*)</div>\s*'
        r'<div[^>]*class="[^"]*block-content[^"]*"[^>]*>([\s\S]*?)</div></div>',
        re.IGNORECASE,
    )

    found_count = 0
    for match in block_pattern.finditer(html_text):
        content = match.group(3).strip()

        # Извлекаем UUID из контента (маркер или URL)
        matched_id = None
        match_score = 0.0
        marker_sample = ""

        # Сначала ищем новый маркер BLOCK: XXXX-XXXX-XXX
        armor_match = ARMOR_BLOCK_MARKER_RE.search(content)
        if armor_match:
            armor_code = armor_match.group(1)
            matched_id, match_score = match_armor_code(
                armor_code, expected_ids, expected_set
            )
            marker_sample = armor_match.group(0)[:60]

        # Fallback: legacy маркер [[BLOCK ID:...]]
        if not matched_id:
            marker_match = BLOCK_MARKER_RE.search(content)
            if marker_match:
                cand = marker_match.group(1)
                matched_id, match_score = match_uuid(
                    cand, expected_ids, expected_set, score_cutoff
                )
                marker_sample = marker_match.group(0)[:60]

        # Если маркер не найден, ищем UUID в URL (для image блоков)
        if not matched_id:
            url_pattern = re.compile(r"crops/([a-f0-9\-]{36})\.pdf", re.IGNORECASE)
            url_match = url_pattern.search(content)
            if url_match:
                cand = url_match.group(1)
                # Прямое сравнение - ID из URL точные
                if cand in expected_set:
                    matched_id = cand
                    match_score = 100.0
                    marker_sample = f"URL: {cand}"

        if matched_id and matched_id not in segments:
            # Убираем маркеры из контента
            clean_content = ARMOR_BLOCK_MARKER_RE.sub("", content)
            clean_content = BLOCK_MARKER_RE.sub("", clean_content).strip()
            # Убираем обёртку <p>...</p> вокруг маркера
            clean_content = re.sub(r"<p>\s*</p>", "", clean_content).strip()

            # Удаляем метаданные, которые будут добавлены в шапку при регенерации
            clean_content = re.sub(
                r'<p><b>Created:</b>[^<]*</p>\s*', '', clean_content, flags=re.IGNORECASE
            )
            clean_content = re.sub(
                r'<p><b>Linked block:</b>[^<]*</p>\s*', '', clean_content, flags=re.IGNORECASE
            )
            clean_content = re.sub(
                r'<p><b>Grouped blocks:</b>[^<]*</p>\s*', '', clean_content, flags=re.IGNORECASE
            )
            clean_content = re.sub(
                r'<div class="stamp-info[^"]*">.*?</div>\s*', '', clean_content, flags=re.DOTALL
            )
            clean_content = clean_content.strip()

            segments[matched_id] = clean_content
            meta[matched_id] = {
                "method": ["div_structure"],
                "match_score": match_score,
                "marker_text_sample": marker_sample,
            }
            found_count += 1

    logger.debug(
        f"_extract_blocks_by_div_structure: found {found_count} blocks by div structure"
    )


def build_segments_from_html(
    html_text: str, expected_ids: list[str], score_cutoff: int = 90
) -> tuple[dict[str, str], dict[str, dict]]:
    """
    Построить сегменты HTML для каждого блока используя regex.

    Логика: ищем маркеры BLOCK: XXXX-XXXX-XXX (новый формат) или [[BLOCK ID: uuid]] (legacy)
    и извлекаем контент ПОСЛЕ каждого маркера до следующего маркера.

    Returns:
        segments: dict[block_id -> html_fragment]
        meta: dict[block_id -> {method, match_score, marker_text_sample}]
    """
    expected_set = set(expected_ids)
    segments: dict[str, str] = {}
    meta: dict[str, dict] = {}

    # Находим все маркеры блоков с их позициями
    markers = []

    # Новый формат: BLOCK: XXXX-XXXX-XXX
    for match in ARMOR_BLOCK_MARKER_RE.finditer(html_text):
        armor_code = match.group(1)
        matched_id, score = match_armor_code(armor_code, expected_ids, expected_set)
        if matched_id:
            markers.append(
                {
                    "start": match.start(),
                    "end": match.end(),
                    "block_id": matched_id,
                    "score": score,
                    "marker_text": match.group(0)[:60],
                }
            )

    # Legacy формат: [[BLOCK ID: uuid]]
    if not markers:
        for match in BLOCK_MARKER_RE.finditer(html_text):
            uuid_candidate = match.group(1)
            matched_id, score = match_uuid(
                uuid_candidate, expected_ids, expected_set, score_cutoff
            )
            if matched_id:
                markers.append(
                    {
                        "start": match.start(),
                        "end": match.end(),
                        "block_id": matched_id,
                        "score": score,
                        "marker_text": match.group(0)[:120],
                    }
                )

    if not markers:
        # Фоллбек: ищем блоки по div.block структуре
        _extract_blocks_by_div_structure(
            html_text, expected_ids, expected_set, segments, meta, score_cutoff
        )
        return segments, meta

    # Сортируем маркеры по позиции
    markers.sort(key=lambda x: x["start"])

    # Дедупликация: оставляем первый маркер для каждого block_id.
    # OCR может воспроизвести BLOCK-маркер из визуального разделителя
    # внутри содержимого блока (например, в page-header div от Datalab),
    # что создаёт дубликат маркера и ломает баланс div-тегов.
    seen_ids: set[str] = set()
    unique_markers = []
    for marker in markers:
        if marker["block_id"] not in seen_ids:
            seen_ids.add(marker["block_id"])
            unique_markers.append(marker)
        else:
            logger.debug(
                f"Дубль маркера для {marker['block_id']} на позиции {marker['start']}, пропущен"
            )
    markers = unique_markers

    # Извлекаем контент между маркерами
    for i, marker in enumerate(markers):
        block_id = marker["block_id"]
        content_start = marker["end"]
        content_end = (
            markers[i + 1]["start"] if i + 1 < len(markers) else len(html_text)
        )

        # Извлекаем HTML между текущим и следующим маркером
        fragment = html_text[content_start:content_end]

        # Убираем закрывающий тег </p> или </div> сразу после маркера (обёртка маркера)
        # Формат: BLOCK: XXXX-XXXX-XXX</p>\n...
        fragment = re.sub(r"^\s*\]?\]?\s*</\w+>\s*", "", fragment)
        # Новый формат: убираем пробелы после BLOCK: code
        fragment = re.sub(r"^\s+", "", fragment)
        # Убираем маркер <p>BLOCK: ...</p> для следующего блока в конце фрагмента
        fragment = re.sub(
            r"\s*<p>\s*BLOCK:\s*[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{3}\s*</p>\s*$",
            "",
            fragment,
            flags=re.IGNORECASE,
        )

        # Убираем открывающий тег <p> или подобный перед следующим маркером
        # Ищем конец полезного контента - до div.block-header или до открывающего <p>[[BLOCK
        fragment = re.sub(
            r'<div[^>]*class="[^"]*block-header[^"]*"[^>]*>[\s\S]*$', "", fragment
        )
        fragment = re.sub(
            r'</div>\s*</div>\s*<div[^>]*class="[^"]*block[\s\S]*$', "", fragment
        )

        # Убираем <p> перед следующим маркером (может содержать только пробелы)
        fragment = re.sub(r"\s*<p>\s*$", "", fragment)

        # Удаляем метаданные, которые будут добавлены в шапку при регенерации
        # Created: и stamp-info (уже есть в annotation.json)
        fragment = re.sub(
            r'<p><b>Created:</b>[^<]*</p>\s*', '', fragment, flags=re.IGNORECASE
        )
        fragment = re.sub(
            r'<p><b>Linked block:</b>[^<]*</p>\s*', '', fragment, flags=re.IGNORECASE
        )
        fragment = re.sub(
            r'<p><b>Grouped blocks:</b>[^<]*</p>\s*', '', fragment, flags=re.IGNORECASE
        )
        fragment = re.sub(
            r'<div class="stamp-info[^"]*">.*?</div>\s*', '', fragment, flags=re.DOTALL
        )

        fragment = fragment.strip()

        if not fragment:
            continue

        if block_id in segments:
            segments[block_id] += "\n" + fragment
            meta[block_id]["match_score"] = max(
                meta[block_id]["match_score"], marker["score"]
            )
        else:
            segments[block_id] = fragment
            meta[block_id] = {
                "method": ["marker"],
                "match_score": marker["score"],
                "marker_text_sample": marker["marker_text"],
            }

    # Фоллбек для блоков, которые не нашлись по маркерам (image блоки и т.д.)
    missing_ids = [bid for bid in expected_ids if bid not in segments]
    if missing_ids:
        logger.info(f"Trying fallback for {len(missing_ids)} missing blocks")
        before_count = len(segments)
        _extract_blocks_by_div_structure(
            html_text, missing_ids, set(missing_ids), segments, meta, score_cutoff
        )
        after_count = len(segments)
        logger.info(f"Fallback found {after_count - before_count} additional blocks")

    return segments, meta
