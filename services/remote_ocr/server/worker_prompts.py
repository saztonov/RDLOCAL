"""Промпты и парсинг для OCR воркера"""

import json
import re
from typing import Dict, List, Optional

from .logging_config import get_logger
from .ocr_constants import make_error

logger = get_logger(__name__)


def get_image_block_prompt(
    block_prompt: Optional[dict],
    category_id: Optional[str] = None,
    category_code: Optional[str] = None,
    engine: Optional[str] = None,
) -> Optional[dict]:
    """
    Получить промпт для IMAGE блока с учётом категории и движка.
    Приоритет: block.prompt > category prompt (из config.yaml)
    """
    # Если блок имеет собственный промпт — используем его
    if block_prompt and (block_prompt.get("system") or block_prompt.get("user")):
        return block_prompt

    # Иначе получаем промпт из config.yaml
    try:
        from .storage_settings import get_category_prompt

        category_prompt = get_category_prompt(category_id, category_code, engine=engine)
        if category_prompt:
            return category_prompt
    except Exception as e:
        logger.warning(f"Не удалось получить промпт категории: {e}")

    return None


def fill_image_prompt_variables(
    prompt_data: Optional[dict],
    doc_name: str,
    page_index: int,
    block_id: str,
    hint: Optional[str],
    pdfplumber_text: str,
    category_id: Optional[str] = None,
    category_code: Optional[str] = None,
    engine: Optional[str] = None,
) -> dict:
    """
    Заполнить переменные в промпте для IMAGE блока.
    Если prompt_data пуст — берёт промпт из config.yaml по категории и движку.

    Переменные:
        {DOC_NAME} - имя PDF документа
        {PAGE_NUM} - номер страницы (1-based)
        {BLOCK_ID} - ID блока
        {OPERATOR_HINT} - подсказка оператора (или пустая строка)
        {PDFPLUMBER_TEXT} - извлечённый текст pdfplumber (или пустая строка)
    """
    # Получаем промпт с учётом категории и движка
    effective_prompt = get_image_block_prompt(
        prompt_data, category_id, category_code, engine=engine
    )

    if not effective_prompt:
        return {
            "system": "",
            "user": "Опиши что изображено на картинке. Верни результат как JSON.",
        }

    result = {
        "system": effective_prompt.get("system", ""),
        "user": effective_prompt.get("user", ""),
    }

    variables = {
        "{DOC_NAME}": doc_name or "unknown",
        "{PAGE_NUM}": str(page_index + 1) if page_index is not None else "1",
        "{BLOCK_ID}": block_id or "",
        "{OPERATOR_HINT}": hint if hint else "",
        "{PDFPLUMBER_TEXT}": pdfplumber_text or "",
    }

    for key, value in variables.items():
        result["system"] = result["system"].replace(key, value)
        result["user"] = result["user"].replace(key, value)

    return result


def inject_pdfplumber_to_ocr_text(ocr_result: str, pdfplumber_text: str) -> str:
    """
    Вставить pdfplumber текст в поле ocr_text результата OCR.
    """
    if not pdfplumber_text or not pdfplumber_text.strip():
        return ocr_result

    if not ocr_result:
        return ocr_result

    try:
        json_match = re.search(r"\{[\s\S]*\}", ocr_result)
        if json_match:
            json_str = json_match.group(0)
            data = json.loads(json_str)

            if "ocr_text" in data:
                data["ocr_text"] = pdfplumber_text.strip()
                new_json = json.dumps(data, ensure_ascii=False, indent=2)

                if ocr_result.strip().startswith("```"):
                    return f"```json\n{new_json}\n```"
                return new_json
    except (json.JSONDecodeError, AttributeError) as e:
        logger.warning(f"Не удалось вставить pdfplumber текст в JSON: {e}")

    return ocr_result


def build_strip_prompt(blocks: list, block_ids: Optional[List[str]] = None) -> dict:
    """
    Построить промпт для batch запроса (полоса TEXT/TABLE блоков).
    Формат ответа: BLOCK: XXXX-XXXX-XXX с результатом каждого блока.
    """
    if len(blocks) == 1:
        block = blocks[0]
        if block.prompt:
            return block.prompt
        return {
            "system": "You are an expert OCR system. Extract text accurately.",
            "user": "Распознай текст на изображении. Сохрани форматирование.",
        }

    system = (
        "You are an expert OCR system. Extract text from each block accurately. "
        "Each block is separated by a black bar with white text 'BLOCK: XXXX-XXXX-XXX'. "
        "You MUST include these BLOCK markers in your response to separate each block's content."
    )
    user = "Распознай текст на изображении."

    batch_instruction = (
        f"\n\nНа изображении {len(blocks)} блоков, разделённых чёрными полосами.\n"
        f"Каждый блок начинается с маркера 'BLOCK: XXXX-XXXX-XXX' (белый текст на чёрном фоне).\n"
        f"ВАЖНО: В ответе выводи маркер BLOCK: перед текстом КАЖДОГО блока.\n"
        f"Формат ответа:\n"
        f"BLOCK: XXXX-XXXX-XXX\n<текст первого блока>\n\n"
        f"BLOCK: YYYY-YYYY-YYY\n<текст второго блока>\n...\n\n"
        f"Не объединяй блоки. Каждый блок — отдельный фрагмент документа."
    )

    return {"system": system, "user": user + batch_instruction}


def parse_batch_response_by_block_id(
    block_ids: List[str], response_text: str
) -> Dict[str, str]:
    """
    Парсинг ответа с разделителями BLOCK: XXXX-XXXX-XXX (armor код).
    Также поддерживает legacy формат [[[BLOCK_ID: uuid]]].
    Returns: Dict[block_id -> text]
    """
    from rd_core.models.armor_id import match_armor_to_uuid

    results: Dict[str, str] = {}

    if response_text is None:
        for bid in block_ids:
            results[bid] = ""
        return results

    # Новый формат: BLOCK: XXXX-XXXX-XXX
    armor_pattern = r"BLOCK:\s*([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{3})"
    armor_matches = list(re.finditer(armor_pattern, response_text, re.IGNORECASE))

    if armor_matches:
        logger.info(
            f"Найдено {len(armor_matches)} разделителей BLOCK (armor) в OCR ответе"
        )

        for i, match in enumerate(armor_matches):
            armor_code = match.group(1)
            start_pos = match.end()

            if i + 1 < len(armor_matches):
                end_pos = armor_matches[i + 1].start()
            else:
                end_pos = len(response_text)

            block_text = response_text[start_pos:end_pos].strip()

            # Сопоставляем armor код с uuid
            matched_uuid, score = match_armor_to_uuid(armor_code, block_ids)
            if matched_uuid:
                results[matched_uuid] = block_text
            else:
                logger.warning(
                    f"Armor код {armor_code} не сопоставлен ни с одним block_id"
                )

        for bid in block_ids:
            if bid not in results:
                results[bid] = ""
                logger.warning(f"BLOCK_ID {bid} не найден в OCR ответе")

        return results

    # Legacy формат: [[[BLOCK_ID: uuid]]]
    legacy_pattern = r"\[\[\[BLOCK_ID:\s*([a-f0-9\-]+)\]\]\]"
    legacy_matches = list(re.finditer(legacy_pattern, response_text, re.IGNORECASE))

    if legacy_matches:
        logger.info(
            f"Найдено {len(legacy_matches)} разделителей BLOCK_ID (legacy) в OCR ответе"
        )

        for i, match in enumerate(legacy_matches):
            block_id = match.group(1)
            start_pos = match.end()

            if i + 1 < len(legacy_matches):
                end_pos = legacy_matches[i + 1].start()
            else:
                end_pos = len(response_text)

            block_text = response_text[start_pos:end_pos].strip()
            results[block_id] = block_text

        for bid in block_ids:
            if bid not in results:
                results[bid] = ""
                logger.warning(f"BLOCK_ID {bid} не найден в OCR ответе")

        return results

    # Fallback: если разделителей нет, весь текст первому блоку
    logger.warning("Разделители BLOCK не найдены, текст целиком первому блоку")
    for i, bid in enumerate(block_ids):
        if i == 0:
            results[bid] = response_text.strip()
        else:
            results[bid] = ""

    return results


def parse_batch_response_by_index(
    num_blocks: int, response_text: str, block_ids: Optional[List[str]] = None
) -> Dict[int, str]:
    """
    Парсинг ответа с маркерами [1], [2], ... или BLOCK: XXXX-XXXX-XXX
    Returns: Dict[index -> text] (индекс 0-based)
    """
    results: Dict[int, str] = {}

    if response_text is None:
        for i in range(num_blocks):
            results[i] = make_error("пустой ответ OCR")
        return results

    if num_blocks == 1:
        # Для одного блока убираем разделитель если есть
        text = response_text.strip()
        # Убираем новый формат
        text = re.sub(
            r"BLOCK:\s*[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{3}\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        # Убираем legacy формат
        text = re.sub(
            r"\[\[\[BLOCK_ID:\s*[a-f0-9\-]+\]\]\]\s*", "", text, flags=re.IGNORECASE
        )
        results[0] = text.strip()
        return results

    # Новый формат: BLOCK: XXXX-XXXX-XXX
    armor_pattern = r"BLOCK:\s*([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{3})"
    armor_matches = list(re.finditer(armor_pattern, response_text, re.IGNORECASE))

    if armor_matches:
        logger.info(
            f"Парсинг по BLOCK (armor) разделителям: найдено {len(armor_matches)}"
        )

        # Если есть block_ids - сопоставляем по armor кодам
        if block_ids:
            from rd_core.models.armor_id import match_armor_to_uuid

            # Создаём маппинг block_id -> index
            id_to_index = {bid: i for i, bid in enumerate(block_ids)}

            for i, match in enumerate(armor_matches):
                armor_code = match.group(1)
                start_pos = match.end()

                if i + 1 < len(armor_matches):
                    end_pos = armor_matches[i + 1].start()
                else:
                    end_pos = len(response_text)

                block_text = response_text[start_pos:end_pos].strip()

                # Сопоставляем armor код с uuid
                matched_uuid, score = match_armor_to_uuid(armor_code, block_ids)
                if matched_uuid and matched_uuid in id_to_index:
                    idx = id_to_index[matched_uuid]
                    results[idx] = block_text
                else:
                    logger.warning(
                        f"Armor код {armor_code} не сопоставлен ни с одним block_id"
                    )
        else:
            # Fallback: по порядку (старое поведение)
            for i, match in enumerate(armor_matches):
                if i >= num_blocks:
                    break

                start_pos = match.end()
                if i + 1 < len(armor_matches):
                    end_pos = armor_matches[i + 1].start()
                else:
                    end_pos = len(response_text)

                block_text = response_text[start_pos:end_pos].strip()
                results[i] = block_text

        for i in range(num_blocks):
            if i not in results:
                results[i] = ""

        return results

    # Legacy: [[[BLOCK_ID: uuid]]]
    block_id_pattern = r"\[\[\[BLOCK_ID:\s*([a-f0-9\-]+)\]\]\]"
    block_id_matches = list(re.finditer(block_id_pattern, response_text, re.IGNORECASE))

    if block_id_matches and len(block_id_matches) >= num_blocks:
        logger.info(
            f"Парсинг по BLOCK_ID (legacy) разделителям: найдено {len(block_id_matches)}"
        )

        for i, match in enumerate(block_id_matches):
            if i >= num_blocks:
                break

            start_pos = match.end()
            if i + 1 < len(block_id_matches):
                end_pos = block_id_matches[i + 1].start()
            else:
                end_pos = len(response_text)

            block_text = response_text[start_pos:end_pos].strip()
            results[i] = block_text

        for i in range(num_blocks):
            if i not in results:
                results[i] = ""

        return results

    # Fallback: парсим по [1], [2], ...
    parts = re.split(r"\n?\[(\d+)\]\s*", response_text)

    parsed = {}
    for i in range(1, len(parts) - 1, 2):
        try:
            idx = int(parts[i]) - 1
            text = parts[i + 1].strip()
            if 0 <= idx < num_blocks:
                parsed[idx] = text
        except (ValueError, IndexError):
            continue

    if not parsed:
        alt_parts = re.split(r"\n{3,}|(?:\n-{3,}\n)", response_text.strip())
        if len(alt_parts) >= num_blocks:
            for i in range(num_blocks):
                results[i] = alt_parts[i].strip()
            return results
        for i in range(num_blocks):
            if i == 0:
                results[i] = response_text.strip()
            else:
                results[i] = ""
        logger.warning(
            f"Batch response без маркеров [N], весь текст присвоен первому элементу"
        )
        return results

    for i in range(num_blocks):
        if i in parsed:
            results[i] = parsed[i]
        else:
            results[i] = ""
            logger.warning(f"Элемент {i} не найден в batch response")

    return results
