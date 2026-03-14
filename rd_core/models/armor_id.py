"""
ArmorID - OCR-устойчивый формат идентификаторов блоков.

ArmorID использует алфавит из 26 символов, устойчивых к ошибкам OCR,
и включает контрольную сумму для валидации.

Unified-модуль: генерация/валидация (клиент) + восстановление/сопоставление (сервер).
"""
import itertools
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

# Московский часовой пояс (UTC+3)
_MSK_TZ = timezone(timedelta(hours=3))


def get_moscow_time_str() -> str:
    """Получить текущее московское время в формате 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now(_MSK_TZ).strftime("%Y-%m-%d %H:%M:%S")


def levenshtein_ratio(s1: str, s2: str) -> float:
    """
    Вычислить схожесть двух строк (0-100%) на основе расстояния Левенштейна.
    Устойчиво к вставкам/удалениям символов (OCR-ошибки).
    """
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 100.0

    len1, len2 = len(s1), len(s2)

    # Матрица расстояний (оптимизация памяти - только 2 строки)
    prev = list(range(len2 + 1))
    curr = [0] * (len2 + 1)

    for i in range(1, len1 + 1):
        curr[0] = i
        for j in range(1, len2 + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,  # удаление
                curr[j - 1] + 1,  # вставка
                prev[j - 1] + cost,  # замена
            )
        prev, curr = curr, prev

    distance = prev[len2]
    max_len = max(len1, len2)
    return ((max_len - distance) / max_len) * 100


class ArmorID:
    """
    Кодирование uuid в короткий OCR-устойчивый код формата XXXX-XXXX-XXX.

    - Использует алфавит из 26 символов, устойчивых к OCR-ошибкам
    - 8 символов payload (кодировка первых 10 hex символов uuid)
    - 3 символа контрольной суммы для восстановления
    """

    # Безопасный алфавит (26 символов) - без визуально похожих
    ALPHABET = "34679ACDEFGHJKLMNPQRTUVWXY"

    # Маппинг символ -> индекс
    CHAR_MAP = {char: idx for idx, char in enumerate(ALPHABET)}

    # Матрица визуальной путаницы OCR
    CONFUSION = {
        "0": ["O", "D", "Q", "C"],
        "1": ["L", "T", "J"],
        "2": ["Z", "7"],
        "5": ["S", "6"],
        "8": ["B", "3", "6", "9"],
        "Z": ["2", "7"],
        "B": ["8", "3", "6", "E", "R"],
        "S": ["5", "6"],
        "O": ["0", "D", "Q"],
        "I": ["1", "L", "T"],
        # Внутренние путаницы
        "3": ["8", "9", "E"],
        "4": ["A", "H"],
        "6": ["G", "8", "5"],
        "7": ["T", "2", "Y"],
        "9": ["P", "8", "6"],
        "A": ["4", "H", "R"],
        "D": ["0", "O", "Q"],
        "E": ["F", "3", "B"],
        "F": ["E", "P"],
        "G": ["6", "C", "Q"],
        "H": ["A", "4", "M", "N"],
        "K": ["X", "R"],
        "M": ["N", "H", "W"],
        "N": ["M", "H"],
        "P": ["R", "F", "9"],
        "Q": ["0", "O", "D"],
        "R": ["P", "K", "A"],
        "T": ["7", "Y", "1"],
        "U": ["V", "W"],
        "V": ["U", "Y"],
        "W": ["M", "V"],
        "X": ["K", "Y"],
        "Y": ["V", "T", "7"],
    }

    @classmethod
    def encode(cls, uuid_str: str) -> str:
        """
        Закодировать uuid в формат XXXX-XXXX-XXX.
        Использует первые 10 hex символов uuid (40 бит).
        """
        clean = uuid_str.replace("-", "").lower()
        hex_prefix = clean[:10]
        num = int(hex_prefix, 16)
        payload = cls._num_to_base26(num, 8)
        checksum = cls._calculate_checksum(payload)
        full_code = payload + checksum
        return f"{full_code[:4]}-{full_code[4:8]}-{full_code[8:]}"

    @classmethod
    def decode(cls, armor_code: str) -> Optional[str]:
        """
        Декодировать armor код обратно в hex prefix (10 символов).
        Возвращает None если код невалидный.
        """
        clean = armor_code.replace("-", "").replace(" ", "").upper()

        if len(clean) != 11:
            return None

        payload = clean[:8]
        checksum = clean[8:]

        if checksum != cls._calculate_checksum(payload):
            return None

        try:
            num = cls._base26_to_num(payload)
        except (KeyError, ValueError):
            return None

        return f"{num:010x}"

    @classmethod
    def repair(cls, input_code: str) -> Tuple[bool, Optional[str], str]:
        """
        Восстановить повреждённый код (до 3 ошибок).
        Поддерживает коды 10-12 символов (OCR может добавить/убрать символы).
        Returns: (success, fixed_code, message)
        """
        clean = input_code.replace("-", "").replace(" ", "").upper()

        # Проверяем "в лоб"
        if cls._is_valid(clean):
            formatted = f"{clean[:4]}-{clean[4:8]}-{clean[8:]}"
            return True, formatted, "Код корректен"

        # Укороченный код (10 символов) — пробуем вставить символ в каждую позицию
        if len(clean) == 10:
            for pos in range(11):
                for char in cls.ALPHABET:
                    candidate = clean[:pos] + char + clean[pos:]
                    if cls._is_valid(candidate):
                        formatted = f"{candidate[:4]}-{candidate[4:8]}-{candidate[8:]}"
                        return True, formatted, "Восстановлен отсутствующий символ"

        # Удлинённый код (12 символов) — пробуем удалить лишний
        if len(clean) == 12:
            for i in range(12):
                candidate = clean[:i] + clean[i + 1 :]
                if cls._is_valid(candidate):
                    formatted = f"{candidate[:4]}-{candidate[4:8]}-{candidate[8:]}"
                    return True, formatted, "Удалён лишний символ"

        # Строим кандидатов для каждой позиции
        # Ограничиваем до 8 вариантов на позицию (приоритет confusion matrix)
        _MAX_CANDIDATES_PER_POS = 8
        candidates_per_pos = []
        for char in clean:
            options = [char] if char in cls.ALPHABET else []

            if char in cls.CONFUSION:
                options.extend([c for c in cls.CONFUSION[char] if c in cls.ALPHABET])

            if not options:
                options = list(cls.ALPHABET)[:_MAX_CANDIDATES_PER_POS]
            else:
                options = list(set(options))[:_MAX_CANDIDATES_PER_POS]

            candidates_per_pos.append(options)

        original = list(clean)

        # Таймаут для предотвращения зависания на сильно повреждённых кодах
        _deadline = time.monotonic() + 0.5  # 500мс

        # Пробуем 1, 2, 3 ошибки
        for errors in range(1, 4):
            for positions in itertools.combinations(range(len(clean)), errors):
                substitutions = []
                for pos in positions:
                    opts = [c for c in candidates_per_pos[pos] if c != original[pos]]
                    if opts:
                        substitutions.append(opts)
                    else:
                        substitutions.append(candidates_per_pos[pos])

                for sub in itertools.product(*substitutions):
                    if time.monotonic() > _deadline:
                        return False, None, "Таймаут восстановления"

                    temp = list(original)
                    for idx, char_idx in enumerate(positions):
                        temp[char_idx] = sub[idx]

                    candidate = "".join(temp)
                    if cls._is_valid(candidate):
                        formatted = f"{candidate[:4]}-{candidate[4:8]}-{candidate[8:]}"
                        return True, formatted, f"Восстановлено {errors} ошибок"

        return False, None, "Не удалось восстановить"

    @classmethod
    def match_to_uuid(
        cls, armor_code: str, expected_uuids: List[str], score_cutoff: float = 70.0
    ) -> Tuple[Optional[str], float]:
        """
        Сопоставить armor код с ожидаемыми ID (UUID или armor ID).
        Использует нечёткий поиск если точное/восстановленное совпадение не найдено.
        Returns: (matched_id, score)
        """
        input_clean = armor_code.replace("-", "").replace(" ", "").upper()

        # 1. Пробуем восстановить код через repair()
        success, fixed, _ = cls.repair(armor_code)

        if success and fixed:
            fixed_clean = fixed.replace("-", "").upper()

            # Прямое совпадение с armor ID
            for expected in expected_uuids:
                expected_clean = expected.replace("-", "").upper()
                if len(expected_clean) == 11 and all(
                    c in cls.ALPHABET for c in expected_clean
                ):
                    if expected_clean == fixed_clean:
                        return expected, 100.0

            # Legacy: декодируем в hex prefix и ищем UUID
            hex_prefix = cls.decode(fixed)
            if hex_prefix:
                for uuid in expected_uuids:
                    clean_uuid = uuid.replace("-", "").lower()
                    if len(clean_uuid) == 32 and all(
                        c in "0123456789abcdef" for c in clean_uuid
                    ):
                        if clean_uuid.startswith(hex_prefix):
                            return uuid, 100.0

        # 2. Если repair не помог - используем нечёткий поиск (Левенштейн)
        best_match = None
        best_score = 0.0

        for expected in expected_uuids:
            expected_clean = expected.replace("-", "").upper()

            if len(expected_clean) != 11:
                continue
            if not all(c in cls.ALPHABET for c in expected_clean):
                continue

            score = levenshtein_ratio(input_clean, expected_clean)

            if score > best_score:
                best_score = score
                best_match = expected

        if best_match and best_score >= score_cutoff:
            return best_match, best_score

        return None, 0.0

    @classmethod
    def _num_to_base26(cls, num: int, length: int) -> str:
        """Конвертировать число в base26 строку фиксированной длины."""
        if num == 0:
            return cls.ALPHABET[0] * length

        result = []
        while num > 0:
            result.append(cls.ALPHABET[num % 26])
            num //= 26

        while len(result) < length:
            result.append(cls.ALPHABET[0])

        return "".join(reversed(result[-length:]))

    @classmethod
    def _base26_to_num(cls, s: str) -> int:
        """Конвертировать base26 строку в число."""
        num = 0
        for char in s:
            num = num * 26 + cls.CHAR_MAP[char]
        return num

    @classmethod
    def _calculate_checksum(cls, payload: str) -> str:
        """Вычислить 3-символьную контрольную сумму."""
        v1, v2, v3 = 0, 0, 0
        for i, char in enumerate(payload):
            val = cls.CHAR_MAP.get(char, 0)
            v1 += val
            v2 += val * (i + 3)
            v3 += val * (i + 7) * (i + 1)

        return cls.ALPHABET[v1 % 26] + cls.ALPHABET[v2 % 26] + cls.ALPHABET[v3 % 26]

    @classmethod
    def _is_valid(cls, code: str) -> bool:
        """Проверить валидность кода (11 символов: 8 payload + 3 checksum)."""
        if len(code) != 11:
            return False

        if not all(c in cls.ALPHABET for c in code):
            return False

        payload = code[:8]
        checksum = code[8:]
        return checksum == cls._calculate_checksum(payload)


# ─── Функции верхнего уровня (обратная совместимость) ───

# ArmorID алфавит (26 OCR-устойчивых символов)
_ARMOR_ALPHABET = ArmorID.ALPHABET
_ARMOR_CHAR_MAP = ArmorID.CHAR_MAP


def _num_to_base26(num: int, length: int) -> str:
    """Конвертировать число в base26 строку фиксированной длины."""
    return ArmorID._num_to_base26(num, length)


def _calculate_checksum(payload: str) -> str:
    """Вычислить 3-символьную контрольную сумму."""
    return ArmorID._calculate_checksum(payload)


def generate_armor_id() -> str:
    """
    Генерировать уникальный ID блока в формате XXXX-XXXX-XXX.

    40 бит энтропии (8 символов payload) + 3 символа контрольной суммы.
    """
    random_bytes = secrets.token_bytes(5)
    num = int.from_bytes(random_bytes, "big")

    payload = _num_to_base26(num, 8)
    checksum = _calculate_checksum(payload)
    full_code = payload + checksum
    return f"{full_code[:4]}-{full_code[4:8]}-{full_code[8:]}"


def is_armor_id(block_id: str) -> bool:
    """Проверить, является ли ID armor форматом (XXXX-XXXX-XXX)."""
    clean = block_id.replace("-", "").upper()
    return len(clean) == 11 and all(c in _ARMOR_ALPHABET for c in clean)


def uuid_to_armor_id(uuid_str: str) -> str:
    """Конвертировать UUID в armor ID формат."""
    return ArmorID.encode(uuid_str)


def migrate_block_id(block_id: str) -> tuple[str, bool]:
    """
    Мигрировать ID блока в armor формат если нужно.

    Returns: (new_id, was_migrated)
    """
    if is_armor_id(block_id):
        return block_id, False
    return uuid_to_armor_id(block_id), True


# ─── Функции-обёртки для серверного кода (обратная совместимость) ───


def encode_block_id(block_id: str) -> str:
    """
    Закодировать block_id в armor формат.
    Если уже armor ID - возвращает как есть.
    """
    clean = block_id.replace("-", "").upper()
    if len(clean) == 11 and all(c in ArmorID.ALPHABET for c in clean):
        return f"{clean[:4]}-{clean[4:8]}-{clean[8:]}"
    return ArmorID.encode(block_id)


def decode_armor_code(armor_code: str) -> Optional[str]:
    """Декодировать armor код в hex prefix."""
    return ArmorID.decode(armor_code)


def match_armor_to_uuid(
    armor_code: str, expected_uuids: List[str], score_cutoff: float = 70.0
) -> Tuple[Optional[str], float]:
    """Сопоставить armor код с uuid. Использует нечёткий поиск для сильно искажённых кодов."""
    return ArmorID.match_to_uuid(armor_code, expected_uuids, score_cutoff)
