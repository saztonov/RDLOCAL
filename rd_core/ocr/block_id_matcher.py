"""Matching block ID для OCR результатов (UUID legacy + ARMOR коды)"""
from __future__ import annotations

import re
from typing import Optional

from rd_core.models.armor_id import levenshtein_ratio

# Новый формат: BLOCK: XXXX-XXXX-XXX (armor код)
# OCR может искажать: пропускать/добавлять символы и дефисы
# Ловим любые последовательности 8-14 символов (алфавит + цифры + дефисы)
ARMOR_BLOCK_MARKER_RE = re.compile(
    r"BLOCK:\s*([A-Z0-9]{2,5}[-\s]*[A-Z0-9]{2,5}[-\s]*[A-Z0-9]{2,5})", re.IGNORECASE
)

# Legacy: UUID формат
UUID_LIKE_RE = re.compile(
    r"([0-9A-Za-z]{8}[-\s_]*[0-9A-Za-z]{4}[-\s_]*[0-9A-Za-z]{4}[-\s_]*[0-9A-Za-z]{4}[-\s_]*[0-9A-Za-z]{12})"
)

# Legacy паттерн для маркеров блоков: [[BLOCK ID: uuid]]
BLOCK_MARKER_RE = re.compile(
    r"\[\[?\s*BLOCK[\s_]*ID\s*[:\-]?\s*"
    r"([0-9A-Za-z]{8}[-\s_]*[0-9A-Za-z]{4}[-\s_]*[0-9A-Za-z]{4}[-\s_]*[0-9A-Za-z]{4}[-\s_]*[0-9A-Za-z]{12})"
    r"\s*\]?\]?",
    re.IGNORECASE,
)

OCR_REPLACEMENTS = {
    "O": "0",
    "o": "0",
    "I": "1",
    "l": "1",
    "|": "1",
    "!": "1",
    "і": "1",
    "І": "1",
    "S": "5",
    "s": "5",
    "G": "6",
    "g": "6",
}


def extract_uuid_candidates(text: str) -> list[str]:
    """Извлечь кандидатов UUID из текста."""
    if not text:
        return []
    return UUID_LIKE_RE.findall(text)


def normalize_uuid_text(s: str) -> Optional[str]:
    """
    Нормализация OCR-кандидата UUID в канонический формат.
    Устойчиво к типичным OCR-ошибкам.
    """
    if not s:
        return None
    s = s.strip()

    hex_chars = []
    for ch in s:
        ch = OCR_REPLACEMENTS.get(ch, ch)
        ch_low = ch.lower()
        if ch_low in "0123456789abcdef":
            hex_chars.append(ch_low)

    hex32 = "".join(hex_chars)
    if len(hex32) < 30:
        return None

    if len(hex32) > 32:
        hex32 = hex32[:32]

    if len(hex32) != 32:
        return None

    return f"{hex32[0:8]}-{hex32[8:12]}-{hex32[12:16]}-{hex32[16:20]}-{hex32[20:32]}"


def match_armor_code(
    armor_code: str,
    expected_ids: list[str],
    expected_set: set[str],
) -> tuple[Optional[str], float]:
    """
    Сопоставить armor код (XXXX-XXXX-XXX) с ожидаемыми UUID.
    Использует ArmorID для восстановления и декодирования.
    """
    from rd_core.models.armor_id import match_armor_to_uuid

    matched_uuid, score = match_armor_to_uuid(armor_code, expected_ids)
    return matched_uuid, score


def match_uuid(
    candidate_raw: str,
    expected_ids: list[str],
    expected_set: set[str],
    score_cutoff: int = 90,
) -> tuple[Optional[str], float]:
    """
    Сопоставить кандидата UUID с ожидаемыми ID (legacy).
    Использует нечёткий поиск с порогом 90%.
    """
    norm = normalize_uuid_text(candidate_raw)
    if norm and norm in expected_set:
        return norm, 100.0

    # Fuzzy matching с учётом вставок/удалений (Левенштейн)
    if norm:
        best_match = None
        best_score = 0.0
        for expected in expected_ids:
            score = levenshtein_ratio(norm, expected)
            if score > best_score and score >= score_cutoff:
                best_match = expected
                best_score = score
        if best_match:
            return best_match, best_score

    # Фоллбек: пробуем без нормализации (для случаев когда OCR сильно исказил)
    if candidate_raw:
        clean = re.sub(r"[^a-f0-9\-]", "", candidate_raw.lower())
        if len(clean) >= 30:
            best_match = None
            best_score = 0.0
            for expected in expected_ids:
                score = levenshtein_ratio(clean, expected)
                if score > best_score and score >= score_cutoff:
                    best_match = expected
                    best_score = score
            if best_match:
                return best_match, best_score

    return None, 0.0
