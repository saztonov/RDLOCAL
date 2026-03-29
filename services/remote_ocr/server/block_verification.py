"""Верификация и повторное распознавание пропущенных блоков"""
from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Callable


from .logging_config import get_logger

logger = get_logger(__name__)

from .ocr_constants import is_error, is_non_retriable

# Пауза между retry для бэкендов с ограничением concurrency (Chandra/LM Studio)
def _get_chandra_retry_delay() -> int:
    from .settings import settings
    return settings.chandra_retry_delay


def _is_lmstudio_backend(backend) -> bool:
    """Проверить, является ли бэкенд LM Studio (Chandra)."""
    return type(backend).__name__ in (
        "ChandraBackend", "AsyncChandraBackend",
        "QwenBackend",
    )


def _get_engine_name(backend) -> str:
    """Получить имя движка из бэкенда для логирования."""
    cls = type(backend).__name__
    name_map = {
        "ChandraBackend": "chandra",
        "AsyncChandraBackend": "chandra",
        "QwenBackend": "qwen",
    }
    return name_map.get(cls, cls.lower())


def _check_backend_available(backend, timeout: int = 10) -> bool:
    """Быстрая проверка доступности бэкенда (LM Studio)."""
    if not _is_lmstudio_backend(backend):
        return True

    base_url = getattr(backend, "base_url", None)
    if not base_url:
        return True

    try:
        session = getattr(backend, "session", None)
        if session:
            resp = session.get(f"{base_url}/v1/models", timeout=timeout)
            return resp.status_code == 200
    except Exception as e:
        logger.warning(f"Backend availability check failed: {e}")
        return False
    return True


def _wait_for_backend(backend, max_wait: int = 300, check_interval: int = 15) -> bool:
    """Ожидать восстановления бэкенда (LM Studio).

    Args:
        backend: OCR backend
        max_wait: максимальное ожидание в секундах
        check_interval: интервал проверки в секундах

    Returns:
        True если бэкенд доступен
    """
    if _check_backend_available(backend):
        return True

    engine = _get_engine_name(backend)
    logger.warning(f"{engine} бэкенд недоступен, ожидание до {max_wait}с...")
    start = time.monotonic()
    while time.monotonic() - start < max_wait:
        time.sleep(check_interval)
        if _check_backend_available(backend):
            elapsed = time.monotonic() - start
            logger.info(f"{engine} бэкенд восстановлен через {elapsed:.0f}с")
            return True

    logger.warning(f"{engine} бэкенд не восстановлен за {max_wait}с")
    return False


def verify_and_retry_missing_blocks(
    enriched_ann: dict,
    pdf_path: Path,
    work_dir: Path,
    ocr_backend,
    text_fallback_backend=None,
    on_progress: Callable[[int, int], None] = None,
    job_id: str = None,
    deadline: float | None = None,
) -> dict:
    """
    Верификация блоков после OCR и повторное распознавание пропущенных.

    Args:
        enriched_ann: enriched annotation dict (результат OCR)
        pdf_path: путь к PDF файлу
        work_dir: рабочая директория
        ocr_backend: OCR backend для повторного распознавание (любой OCRBackend)
        text_fallback_backend: fallback OCR backend для suspicious_output retry
        on_progress: callback (current, total) для обновления прогресса
        deadline: абсолютное время (time.time()) до которого нужно завершить верификацию.
            Если задан — заменяет verification_timeout_minutes.

    Returns:
        Обновлённый dict с результатами повторного распознавания
    """
    from .text_ocr_quality import classify_text_output

    result = copy.deepcopy(enriched_ann)

    engine_name = _get_engine_name(ocr_backend)

    # Находим блоки без OCR результата, с ошибками API, или с подозрительным output
    missing_blocks = []
    for page in result.get("pages", []):
        for blk in page.get("blocks", []):
            block_type = blk.get("block_type", "text")
            block_id = blk.get("id", "")
            ocr_html = blk.get("ocr_html", "").strip()
            ocr_text = blk.get("ocr_text", "")
            category_code = blk.get("category_code", "")

            # Пропускаем штампы и image блоки (они обрабатываются отдельно)
            if category_code == "stamp" or block_type == "image":
                continue

            # Проверяем только текстовые и табличные блоки
            if block_type != "text":
                continue

            # Неповторяемые ошибки (context exceeded, невалидные координаты) — не ретраим
            if is_non_retriable(ocr_text):
                continue

            # Определяем reason для retry
            reason = None
            if not ocr_html or is_error(ocr_text):
                reason = "api_error" if is_error(ocr_text) else "empty"
            else:
                # Проверка suspicious output (layout-dump, bbox JSON, etc.)
                quality = classify_text_output(ocr_text, ocr_html)
                if quality["quality"] == "suspicious":
                    reason = "suspicious_output"
                    logger.info(
                        f"Блок {block_id}: suspicious output — {quality['reason']}"
                    )

            if reason:
                missing_blocks.append({
                    "block": blk,
                    "page_index": blk.get("page_index", 1) - 1,  # Конвертируем в 0-based
                    "reason": reason,
                })

    if not missing_blocks:
        logger.info("Все текстовые блоки распознаны")
        return result

    total_found = len(missing_blocks)
    error_count = sum(1 for b in missing_blocks if b["reason"] == "api_error")
    empty_count = sum(1 for b in missing_blocks if b["reason"] == "empty")
    suspicious_count = sum(1 for b in missing_blocks if b["reason"] == "suspicious_output")
    logger.warning(
        f"Найдено {total_found} нераспознанных текстовых блоков "
        f"(пустых: {empty_count}, ошибок API: {error_count}, "
        f"подозрительных: {suspicious_count}), engine: {engine_name}",
        extra={
            "event": "verification_missing_blocks",
            "job_id": job_id,
            "total_blocks": total_found,
            "block_count": error_count,
            "suspicious_count": suspicious_count,
            "backend": engine_name,
        },
    )

    # Лимиты верификации из конфигурации
    from .settings import settings
    max_blocks = settings.max_retry_blocks  # default 0 (без лимита)
    timeout_min = settings.verification_timeout_minutes  # default 30

    is_lmstudio = _is_lmstudio_backend(ocr_backend)

    # Для LM Studio: гарантируем минимальный таймаут 30 мин
    if is_lmstudio:
        timeout_min = max(timeout_min, 30)

    # Если передан deadline задачи — используем его вместо timeout_min.
    # Резервируем 60с на сохранение результатов и upload.
    _VERIFICATION_RESERVE = 60
    if deadline is not None:
        remaining = deadline - time.time() - _VERIFICATION_RESERVE
        if remaining <= 0:
            logger.warning(
                f"Верификация пропущена: до deadline задачи осталось {remaining + _VERIFICATION_RESERVE:.0f}с"
            )
            return result
        # Пересчитываем timeout_min на основе deadline
        timeout_min = remaining / 60
        logger.info(
            f"Верификация: deadline задачи через {remaining:.0f}с ({timeout_min:.1f} мин)"
        )

    # Обновляем deadline для backend (Chandra) — иначе _is_budget_exhausted сразу True
    if deadline is not None:
        verification_deadline = deadline - _VERIFICATION_RESERVE
        if hasattr(ocr_backend, "set_deadline"):
            ocr_backend.set_deadline(verification_deadline)
        if text_fallback_backend and hasattr(text_fallback_backend, "set_deadline"):
            text_fallback_backend.set_deadline(verification_deadline)

    if max_blocks > 0 and len(missing_blocks) > max_blocks:
        logger.warning(
            f"Ограничение верификации: {len(missing_blocks)} -> {max_blocks} блоков"
        )
        missing_blocks = missing_blocks[:max_blocks]

    # Создаём директорию для кропов
    retry_crops_dir = work_dir / "retry_crops"
    retry_crops_dir.mkdir(exist_ok=True)

    # Обрабатываем каждый блок отдельно
    from .pdf_streaming_core import StreamingPDFProcessor
    from rd_core.models import Block

    successful_retries = 0
    consecutive_failures = 0
    consecutive_budget_exhausted = 0  # счётчик "time budget exhausted" ошибок подряд
    MAX_CONSECUTIVE_FAILURES = 10
    MAX_BUDGET_EXHAUSTED_BEFORE_FALLBACK = 3  # после 3 budget exhausted — переключаемся на fallback
    start_time = time.monotonic()
    stopped_reason = None
    base_delay = _get_chandra_retry_delay()
    primary_backend_disabled = False  # True если primary отказал, используем только fallback

    fallback_engine_name = _get_engine_name(text_fallback_backend) if text_fallback_backend else None

    # Для LM Studio: ждём доступности бэкенда перед началом retry
    if is_lmstudio:
        if not _wait_for_backend(ocr_backend, max_wait=300, check_interval=15):
            logger.warning("Бэкенд недоступен, начинаем верификацию с надеждой на восстановление")

    with StreamingPDFProcessor(str(pdf_path)) as processor:
        for idx, item in enumerate(missing_blocks):
            # Проверка таймаута верификации
            if timeout_min > 0:
                elapsed_min = (time.monotonic() - start_time) / 60
                if elapsed_min > timeout_min:
                    stopped_reason = f"таймаут ({elapsed_min:.1f} мин > {timeout_min} мин)"
                    logger.warning(f"Верификация прервана: {stopped_reason}")
                    break

            # Проверка серии ошибок подряд — ждём восстановления бэкенда
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                if is_lmstudio:
                    logger.warning(
                        f"{consecutive_failures} ошибок подряд, проверяем доступность бэкенда..."
                    )
                    if _wait_for_backend(ocr_backend, max_wait=180, check_interval=15):
                        consecutive_failures = 0
                        logger.info("Бэкенд восстановлен, продолжаем верификацию")
                    else:
                        stopped_reason = f"бэкенд недоступен после {MAX_CONSECUTIVE_FAILURES} ошибок"
                        logger.warning(f"Верификация прервана: {stopped_reason}")
                        break
                else:
                    stopped_reason = f"{MAX_CONSECUTIVE_FAILURES} ошибок подряд (backend недоступен)"
                    logger.warning(f"Верификация прервана: {stopped_reason}")
                    break

            # Вызываем callback прогресса перед обработкой блока
            if on_progress:
                on_progress(idx, len(missing_blocks))

            # Гарантируем отправку обновлений каждые 5 блоков
            if job_id and idx % 5 == 0:
                from .debounced_updater import get_debounced_updater
                get_debounced_updater(job_id).flush()

            blk_data = item["block"]
            block_id = blk_data["id"]
            reason = item["reason"]

            # Выбор бэкенда: suspicious_output или primary disabled → fallback
            use_fallback = (
                (reason == "suspicious_output" and text_fallback_backend)
                or (primary_backend_disabled and text_fallback_backend)
            )
            if use_fallback:
                retry_backend = text_fallback_backend
                retry_engine = fallback_engine_name
                method_prefix = "fallback"
            else:
                retry_backend = ocr_backend
                retry_engine = engine_name
                method_prefix = "retry"

            logger.info(
                f"[{idx+1}/{len(missing_blocks)}] Повторное распознавание блока "
                f"{block_id} ({reason}), engine: {retry_engine}"
            )

            # Пауза перед retry с exponential backoff при ошибках
            # (только для LM Studio primary backend)
            if _is_lmstudio_backend(retry_backend) and idx > 0:
                if consecutive_failures > 0:
                    backoff_delay = min(base_delay * (2 ** consecutive_failures), 120)
                    # Проверяем deadline ПЕРЕД sleep — избегаем SoftTimeLimitExceeded
                    if deadline is not None and time.time() + backoff_delay > deadline - _VERIFICATION_RESERVE:
                        stopped_reason = f"deadline задачи (до sleep {backoff_delay}с)"
                        logger.warning(f"Верификация прервана: {stopped_reason}")
                        break
                    logger.info(
                        f"Backoff delay: {backoff_delay}с "
                        f"(consecutive_failures={consecutive_failures})"
                    )
                    time.sleep(backoff_delay)
                else:
                    if deadline is not None and time.time() + base_delay > deadline - _VERIFICATION_RESERVE:
                        stopped_reason = "deadline задачи (до sleep base_delay)"
                        logger.warning(f"Верификация прервана: {stopped_reason}")
                        break
                    time.sleep(base_delay)

            # Промежуточная проверка доступности при множественных ошибках
            if _is_lmstudio_backend(retry_backend) and consecutive_failures > 0 and consecutive_failures % 3 == 0:
                if not _check_backend_available(retry_backend):
                    logger.info("Бэкенд недоступен, ожидание 60с...")
                    time.sleep(60)

            try:
                # Создаём Block объект для crop
                block_obj, _ = Block.from_dict(blk_data, migrate_ids=False)
                # result.json хранит page_index в 1-based, Block ожидает 0-based
                block_obj.page_index = item["page_index"]

                # Вырезаем кроп
                crop = processor.crop_block_image(block_obj, padding=5)
                if not crop:
                    logger.warning(f"Не удалось создать кроп для блока {block_id}")
                    consecutive_failures += 1
                    continue

                # Сохраняем кроп для отладки
                crop_path = retry_crops_dir / f"{block_id}.png"
                crop.save(crop_path, "PNG")

                # Отправляем на распознавание
                ocr_text = retry_backend.recognize(crop)
                crop.close()

                if ocr_text and not is_error(ocr_text):
                    # Нормализуем: structured JSON → HTML, fallback на raw text
                    from rd_core.ocr.generator_common import sanitize_html
                    from rd_core.ocr._chandra_common import (
                        _try_extract_structured_ocr,
                        _try_extract_structured_array,
                    )
                    from rd_core.ocr_result import is_suspicious_output
                    normalized = _try_extract_structured_ocr(ocr_text)
                    if normalized is None:
                        normalized = _try_extract_structured_array(ocr_text)
                    if normalized is None:
                        normalized = ocr_text
                    sanitized = sanitize_html(normalized)
                    # Проверка: retry результат может быть suspicious (layout-dump и пр.)
                    suspicious, sus_reason = is_suspicious_output(ocr_text, sanitized)
                    if suspicious:
                        logger.warning(
                            f"Блок {block_id}: retry результат подозрительный — {sus_reason}"
                        )
                        consecutive_failures += 1
                        continue
                    blk_data["ocr_html"] = sanitized
                    blk_data["ocr_text"] = ocr_text
                    blk_data["ocr_meta"] = {
                        "method": [f"{method_prefix}_{retry_engine}"],
                        "match_score": 100.0,
                        "marker_text_sample": "",
                    }
                    successful_retries += 1
                    consecutive_failures = 0
                    logger.info(f"Блок {block_id} успешно распознан {method_prefix} ({len(ocr_text)} символов)")
                else:
                    # Для suspicious_output: если fallback не помог, попробовать primary один раз
                    if reason == "suspicious_output" and retry_backend is text_fallback_backend:
                        logger.info(
                            f"Блок {block_id}: fallback не помог, пробуем primary backend"
                        )
                        crop = processor.crop_block_image(block_obj, padding=5)
                        if crop:
                            ocr_text = ocr_backend.recognize(crop)
                            crop.close()
                            if ocr_text and not is_error(ocr_text):
                                from rd_core.ocr.generator_common import sanitize_html
                                from rd_core.ocr._chandra_common import (
                                    _try_extract_structured_ocr,
                                    _try_extract_structured_array,
                                )
                                from rd_core.ocr_result import is_suspicious_output
                                normalized = _try_extract_structured_ocr(ocr_text)
                                if normalized is None:
                                    normalized = _try_extract_structured_array(ocr_text)
                                if normalized is None:
                                    normalized = ocr_text
                                sanitized = sanitize_html(normalized)
                                suspicious, sus_reason = is_suspicious_output(ocr_text, sanitized)
                                if suspicious:
                                    logger.warning(
                                        f"Блок {block_id}: primary retry подозрительный — {sus_reason}"
                                    )
                                    break  # не принимаем, выходим из fallback
                                blk_data["ocr_html"] = sanitized
                                blk_data["ocr_text"] = ocr_text
                                blk_data["ocr_meta"] = {
                                    "method": [f"retry_{engine_name}"],
                                    "match_score": 100.0,
                                    "marker_text_sample": "",
                                }
                                successful_retries += 1
                                consecutive_failures = 0
                                logger.info(f"Блок {block_id} успешно распознан primary retry ({len(ocr_text)} символов)")
                                continue

                    consecutive_failures += 1
                    # Детектируем "time budget exhausted" — бесполезные retry
                    if ocr_text and "budget exhausted" in ocr_text:
                        consecutive_budget_exhausted += 1
                        if (
                            consecutive_budget_exhausted >= MAX_BUDGET_EXHAUSTED_BEFORE_FALLBACK
                            and not primary_backend_disabled
                        ):
                            if text_fallback_backend:
                                primary_backend_disabled = True
                                consecutive_failures = 0
                                consecutive_budget_exhausted = 0
                                logger.warning(
                                    f"Primary backend ({engine_name}) отключён после "
                                    f"{MAX_BUDGET_EXHAUSTED_BEFORE_FALLBACK} 'budget exhausted' ошибок, "
                                    f"переключение на fallback ({fallback_engine_name})"
                                )
                            else:
                                stopped_reason = (
                                    f"primary backend budget exhausted × {consecutive_budget_exhausted}, "
                                    "fallback недоступен"
                                )
                                logger.warning(f"Верификация прервана: {stopped_reason}")
                                break
                    else:
                        consecutive_budget_exhausted = 0
                    logger.warning(f"Блок {block_id} не распознан при retry: {ocr_text[:100] if ocr_text else 'пусто'}")

            except Exception as e:
                consecutive_failures += 1
                logger.error(f"Ошибка обработки блока {block_id}: {e}", exc_info=True)
                continue

    if successful_retries > 0:
        logger.info(f"Верификация: {successful_retries} блоков обновлено в dict")

    elapsed_total = (time.monotonic() - start_time) / 60
    status_parts = [f"{successful_retries}/{len(missing_blocks)} блоков восстановлено"]
    if total_found > len(missing_blocks):
        status_parts.append(f"из {total_found} найденных")
    if stopped_reason:
        status_parts.append(f"прервано: {stopped_reason}")
    status_parts.append(f"за {elapsed_total:.1f} мин")

    logger.info(
        f"Верификация завершена: {', '.join(status_parts)}",
        extra={
            "event": "verification_completed",
            "job_id": job_id,
            "recognized_count": successful_retries,
            "total_blocks": len(missing_blocks),
            "duration_ms": int((time.monotonic() - start_time) * 1000),
            "backend": engine_name,
        },
    )
    return result


