"""Верификация и повторное распознавание пропущенных блоков"""
from __future__ import annotations

import json
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


def _is_chandra_backend(backend) -> bool:
    """Проверить, является ли бэкенд Chandra (LM Studio). Обратная совместимость."""
    return _is_lmstudio_backend(backend)


def _is_lmstudio_backend(backend) -> bool:
    """Проверить, является ли бэкенд LM Studio (Chandra)."""
    return type(backend).__name__ in (
        "ChandraBackend", "AsyncChandraBackend",
    )


def _get_engine_name(backend) -> str:
    """Получить имя движка из бэкенда для логирования."""
    cls = type(backend).__name__
    name_map = {
        "ChandraBackend": "chandra",
        "AsyncChandraBackend": "chandra",
        "DatalabOCRBackend": "datalab",
        "OpenRouterBackend": "openrouter",
    }
    return name_map.get(cls, cls.lower())


def _check_backend_available(backend, timeout: int = 10) -> bool:
    """Быстрая проверка доступности бэкенда (ngrok tunnel для LM Studio)."""
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
    """Ожидать восстановления бэкенда (ngrok tunnel).

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
    result_json_path: Path,
    pdf_path: Path,
    work_dir: Path,
    ocr_backend,
    text_fallback_backend=None,
    on_progress: Callable[[int, int], None] = None,
    job_id: str = None,
) -> bool:
    """
    Верификация блоков после OCR и повторное распознавание пропущенных.

    Args:
        result_json_path: путь к result.json
        pdf_path: путь к PDF файлу
        work_dir: рабочая директория
        ocr_backend: OCR backend для повторного распознавания (любой OCRBackend)
        text_fallback_backend: fallback OCR backend для suspicious_output retry
        on_progress: callback (current, total) для обновления прогресса

    Returns:
        True если были найдены и обработаны пропущенные блоки
    """
    from .text_ocr_quality import classify_text_output

    if not result_json_path.exists():
        logger.warning(f"result.json не найден: {result_json_path}")
        return False

    with open(result_json_path, "r", encoding="utf-8") as f:
        result = json.load(f)

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
            if block_type not in ["text", "table"]:
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
        return False

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
    MAX_CONSECUTIVE_FAILURES = 10
    start_time = time.monotonic()
    stopped_reason = None
    base_delay = _get_chandra_retry_delay()

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

            # Выбор бэкенда: suspicious_output → fallback (если есть), иначе primary
            if reason == "suspicious_output" and text_fallback_backend:
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
                    logger.info(
                        f"Backoff delay: {backoff_delay}с "
                        f"(consecutive_failures={consecutive_failures})"
                    )
                    time.sleep(backoff_delay)
                else:
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
                    # Обновляем блок в result.json
                    from rd_core.ocr.generator_common import sanitize_html
                    blk_data["ocr_html"] = sanitize_html(ocr_text)
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
                                blk_data["ocr_html"] = sanitize_html(ocr_text)
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
                    logger.warning(f"Блок {block_id} не распознан при retry: {ocr_text[:100] if ocr_text else 'пусто'}")

            except Exception as e:
                consecutive_failures += 1
                logger.error(f"Ошибка обработки блока {block_id}: {e}", exc_info=True)
                continue

    # Сохраняем обновлённый result.json
    if successful_retries > 0:
        with open(result_json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"result.json обновлён ({successful_retries} блоков добавлено)")

        # Регенерируем HTML и MD
        _regenerate_output_files(result, work_dir, result_json_path)

        # Ресинхронизируем annotation.json с обновлённым result.json
        _resync_annotation(result, work_dir)

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
    return successful_retries > 0


def _resync_annotation(result: dict, work_dir: Path):
    """Ресинхронизировать annotation.json с обновлённым result.json.

    Верификация обновляет ocr_text/ocr_html в result.json, но annotation.json
    остаётся стейл. Клиент скачивает оба файла, поэтому они должны совпадать.
    """
    annotation_path = work_dir / "annotation.json"
    if not annotation_path.exists():
        return

    try:
        with open(annotation_path, "r", encoding="utf-8") as f:
            ann = json.load(f)

        # Собираем обновлённые ocr_text из result.json по block id
        result_texts = {}
        for page in result.get("pages", []):
            for blk in page.get("blocks", []):
                bid = blk.get("id")
                if bid:
                    result_texts[bid] = (blk.get("ocr_text", ""), blk.get("ocr_html", ""))

        # Обновляем annotation.json
        updated = 0
        for page in ann.get("pages", []):
            for blk in page.get("blocks", []):
                bid = blk.get("id")
                if bid in result_texts:
                    new_text, new_html = result_texts[bid]
                    old_text = blk.get("ocr_text", "")
                    if old_text != new_text:
                        blk["ocr_text"] = new_text
                        updated += 1

        if updated > 0:
            with open(annotation_path, "w", encoding="utf-8") as f:
                json.dump(ann, f, ensure_ascii=False, indent=2)
            logger.info(f"annotation.json ресинхронизирован ({updated} блоков обновлено)")
    except Exception as e:
        logger.warning(f"Ошибка ресинхронизации annotation.json: {e}")


def _regenerate_output_files(result: dict, work_dir: Path, result_json_path: Path):
    """Регенерировать HTML и MD после обновления result.json"""
    from .ocr_result_merger import regenerate_html_from_result, regenerate_md_from_result

    try:
        # Регенерируем HTML
        html_path = work_dir / "ocr_result.html"
        doc_name = result.get("pdf_path", "OCR Result")
        regenerate_html_from_result(result, html_path, doc_name=doc_name)
        logger.info(f"HTML регенерирован: {html_path}")

        # Регенерируем MD
        md_path = work_dir / "document.md"
        regenerate_md_from_result(result, md_path, doc_name=doc_name)
        logger.info(f"MD регенерирован: {md_path}")
    except Exception as e:
        logger.error(f"Ошибка регенерации файлов: {e}", exc_info=True)
