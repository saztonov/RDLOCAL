"""Верификация и повторное распознавание пропущенных блоков.

Перенесён из services/remote_ocr/server/block_verification.py.
Серверные зависимости (settings, debounced_updater, worker_prompts,
pdf_streaming_core, pass2_images) заменены на параметры/callbacks.
"""
from __future__ import annotations

import copy
import json as _json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from rd_core.ocr_result import is_error, is_non_retriable

logger = logging.getLogger(__name__)


# ── Конфигурация верификации (инъекция серверных зависимостей) ────────


@dataclass
class VerificationConfig:
    """Конфигурация для верификации, заменяющая серверные settings и зависимости."""
    chandra_retry_delay: int = 5
    max_retry_blocks: int = 50
    verification_timeout_minutes: float = 30
    # Callback для flush прогресса (заменяет debounced_updater)
    on_flush_progress: Callable[[str], None] | None = None
    # Callback для построения промпта IMAGE/STAMP (заменяет worker_prompts)
    prompt_builder: Callable[[dict, Path], dict | None] | None = None
    # Callable для парсинга stamp JSON (заменяет pass2_images._parse_stamp_json)
    stamp_json_parser: Callable[[str], dict | None] | None = None
    # Context manager class для обработки PDF (заменяет StreamingPDFProcessor)
    pdf_processor_factory: Any = None


def _default_stamp_json_parser(ocr_text: str) -> dict | None:
    """Фоллбек парсер stamp JSON (простой json.loads)."""
    try:
        obj = _json.loads(ocr_text)
        return obj if isinstance(obj, dict) else None
    except (_json.JSONDecodeError, TypeError):
        return None


# ── Утилиты бэкендов ──────────────────────────────────────────────


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
    """Ожидать восстановления бэкенда (LM Studio)."""
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


# ── Коллекторы проблемных блоков по типам ──────────────────────────


def _collect_missing_text_blocks(pages: list[dict]) -> list[dict]:
    """Найти TEXT блоки с пустым/ошибочным/подозрительным результатом."""
    from rd_core.ocr.text_ocr_quality import classify_text_output

    missing = []
    for page in pages:
        for blk in page.get("blocks", []):
            block_type = blk.get("block_type", "text")
            if block_type != "text":
                continue
            if blk.get("category_code", "") == "stamp":
                continue
            ocr_text = blk.get("ocr_text", "")
            if is_non_retriable(ocr_text):
                continue

            ocr_html = blk.get("ocr_html", "").strip()
            reason = None
            if not ocr_html or is_error(ocr_text):
                reason = "api_error" if is_error(ocr_text) else "empty"
            else:
                quality = classify_text_output(ocr_text, ocr_html)
                if quality["quality"] == "suspicious":
                    reason = "suspicious_output"
                    logger.info(
                        f"Блок {blk.get('id', '')}: suspicious text output — {quality['reason']}"
                    )

            if reason:
                missing.append({
                    "block": blk,
                    "page_index": blk.get("page_index", 1) - 1,
                    "reason": reason,
                })
    return missing


def _collect_missing_stamp_blocks(pages: list[dict]) -> list[dict]:
    """Найти STAMP блоки с пустым/ошибочным/подозрительным результатом."""
    from rd_core.ocr.text_ocr_quality import classify_stamp_output

    missing = []
    for page in pages:
        for blk in page.get("blocks", []):
            block_type = blk.get("block_type", "text")
            category_code = blk.get("category_code", "")
            is_stamp = category_code == "stamp" or block_type == "stamp"
            if not is_stamp:
                continue
            ocr_text = blk.get("ocr_text", "")
            if is_non_retriable(ocr_text):
                continue

            stamp_data = blk.get("stamp_data")
            quality = classify_stamp_output(ocr_text, stamp_data)
            if quality["quality"] in ("empty", "api_error", "suspicious"):
                reason = quality["quality"] if quality["quality"] != "suspicious" else "suspicious_output"
                if reason == "api_error" and not is_error(ocr_text):
                    reason = "empty"
                logger.info(
                    f"Блок {blk.get('id', '')}: stamp {quality['quality']} — {quality['reason']}"
                )
                missing.append({
                    "block": blk,
                    "page_index": blk.get("page_index", 1) - 1,
                    "reason": reason,
                })
    return missing


def _collect_missing_image_blocks(pages: list[dict]) -> list[dict]:
    """Найти IMAGE блоки (не штампы) с пустым/ошибочным/подозрительным результатом."""
    from rd_core.ocr.text_ocr_quality import classify_image_output

    missing = []
    for page in pages:
        for blk in page.get("blocks", []):
            block_type = blk.get("block_type", "text")
            category_code = blk.get("category_code", "")
            if block_type != "image" or category_code == "stamp":
                continue
            ocr_text = blk.get("ocr_text", "")
            if is_non_retriable(ocr_text):
                continue

            ocr_json = blk.get("ocr_json")
            quality = classify_image_output(ocr_text, ocr_json)
            if quality["quality"] in ("empty", "api_error", "suspicious"):
                reason = quality["quality"] if quality["quality"] != "suspicious" else "suspicious_output"
                if reason == "api_error" and not is_error(ocr_text):
                    reason = "empty"
                logger.info(
                    f"Блок {blk.get('id', '')}: image {quality['quality']} — {quality['reason']}"
                )
                missing.append({
                    "block": blk,
                    "page_index": blk.get("page_index", 1) - 1,
                    "reason": reason,
                })
    return missing


# ── Post-process callbacks для разных типов ──────────────────────────


def _process_text_result(ocr_text: str, blk_data: dict, method_prefix: str, engine_name: str) -> bool:
    """Обработать результат retry TEXT блока: structured OCR → sanitized HTML."""
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
        logger.warning(f"Блок {blk_data['id']}: retry результат подозрительный — {sus_reason}")
        return False

    blk_data["ocr_html"] = sanitized
    blk_data["ocr_text"] = ocr_text
    blk_data["ocr_meta"] = {
        "method": [f"{method_prefix}_{engine_name}"],
        "match_score": 100.0,
        "marker_text_sample": "",
    }
    return True


def _make_stamp_processor(config: VerificationConfig) -> Callable:
    """Создать post-process callback для STAMP блоков с инъектированным парсером."""
    stamp_parser = config.stamp_json_parser or _default_stamp_json_parser

    def _process_stamp_result(ocr_text: str, blk_data: dict, method_prefix: str, engine_name: str) -> bool:
        stamp_obj = stamp_parser(ocr_text)
        if stamp_obj is None:
            logger.warning(f"Блок {blk_data['id']}: retry stamp — невалидный JSON")
            return False

        blk_data["ocr_text"] = _json.dumps(stamp_obj, ensure_ascii=False)
        blk_data["stamp_data"] = stamp_obj
        blk_data["ocr_json"] = stamp_obj
        blk_data["ocr_meta"] = {
            "method": [f"{method_prefix}_{engine_name}"],
            "match_score": 100.0,
            "marker_text_sample": "",
        }
        return True

    return _process_stamp_result


def _process_image_result(ocr_text: str, blk_data: dict, method_prefix: str, engine_name: str) -> bool:
    """Обработать результат retry IMAGE блока: парсинг JSON."""
    try:
        obj = _json.loads(ocr_text)
    except (_json.JSONDecodeError, TypeError):
        logger.warning(f"Блок {blk_data['id']}: retry image — невалидный JSON")
        return False

    if not obj:
        logger.warning(f"Блок {blk_data['id']}: retry image — пустой JSON")
        return False

    blk_data["ocr_text"] = ocr_text
    blk_data["ocr_json"] = obj
    blk_data["ocr_meta"] = {
        "method": [f"{method_prefix}_{engine_name}"],
        "match_score": 100.0,
        "marker_text_sample": "",
    }
    return True


# ── Построение промпта для IMAGE/STAMP retry ──────────────────────


def _build_retry_prompt(blk_data: dict, pdf_path: Path, config: VerificationConfig) -> Optional[dict]:
    """Построить промпт для IMAGE/STAMP retry из данных блока."""
    if not config.prompt_builder:
        return blk_data.get("prompt")

    block_id = blk_data.get("id", "")
    page_index = blk_data.get("page_index", 1) - 1  # enriched dict хранит 1-based
    category_code = blk_data.get("category_code", "")
    doc_name = Path(pdf_path).name

    return config.prompt_builder(
        blk_data.get("prompt"),
        doc_name,
        page_index,
        block_id,
        category_code or None,
    )


# ── Общий retry-цикл для фазы ──────────────────────────────────


_VERIFICATION_RESERVE = 60
MAX_CONSECUTIVE_FAILURES = 10
MAX_BUDGET_EXHAUSTED_BEFORE_FALLBACK = 3


def _retry_block_phase(
    missing_blocks: list[dict],
    retry_backend,
    fallback_backend,
    processor,
    retry_crops_dir: Path,
    phase_name: str,
    post_process: Callable[[str, dict, str, str], bool],
    *,
    pdf_path: Path,
    deadline: float | None,
    timeout_min: float,
    start_time: float,
    job_id: str | None,
    on_progress: Callable[[int, int], None] | None,
    progress_offset: int,
    total_all_phases: int,
    use_prompt: bool = False,
    config: VerificationConfig | None = None,
) -> tuple[int, str | None]:
    """Общий retry-цикл для одной фазы верификации."""
    from rd_core.models import Block

    if not missing_blocks or not retry_backend:
        return 0, None

    cfg = config or VerificationConfig()
    engine_name = _get_engine_name(retry_backend)
    fallback_engine_name = _get_engine_name(fallback_backend) if fallback_backend else None
    is_lmstudio = _is_lmstudio_backend(retry_backend)
    base_delay = cfg.chandra_retry_delay

    successful_retries = 0
    consecutive_failures = 0
    consecutive_budget_exhausted = 0
    primary_backend_disabled = False
    stopped_reason = None

    # Для LM Studio: ждём доступности бэкенда перед началом retry
    if is_lmstudio:
        if not _wait_for_backend(retry_backend, max_wait=300, check_interval=15):
            logger.warning(f"{phase_name}: бэкенд недоступен, начинаем с надеждой на восстановление")

    for idx, item in enumerate(missing_blocks):
        # Проверка таймаута
        if timeout_min > 0:
            elapsed_min = (time.monotonic() - start_time) / 60
            if elapsed_min > timeout_min:
                stopped_reason = f"таймаут ({elapsed_min:.1f} мин > {timeout_min} мин)"
                logger.warning(f"{phase_name} верификация прервана: {stopped_reason}")
                break

        # Проверка серии ошибок подряд
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            if is_lmstudio:
                logger.warning(
                    f"{phase_name}: {consecutive_failures} ошибок подряд, проверяем доступность..."
                )
                if _wait_for_backend(retry_backend, max_wait=180, check_interval=15):
                    consecutive_failures = 0
                    logger.info(f"{phase_name}: бэкенд восстановлен, продолжаем")
                else:
                    stopped_reason = f"бэкенд недоступен после {MAX_CONSECUTIVE_FAILURES} ошибок"
                    logger.warning(f"{phase_name} верификация прервана: {stopped_reason}")
                    break
            else:
                stopped_reason = f"{MAX_CONSECUTIVE_FAILURES} ошибок подряд (backend недоступен)"
                logger.warning(f"{phase_name} верификация прервана: {stopped_reason}")
                break

        # Callback прогресса
        if on_progress:
            on_progress(progress_offset + idx, total_all_phases)

        # Flush прогресса каждые 5 блоков
        if job_id and idx % 5 == 0 and cfg.on_flush_progress:
            try:
                cfg.on_flush_progress(job_id)
            except Exception:
                pass

        blk_data = item["block"]
        block_id = blk_data["id"]
        reason = item["reason"]

        # Выбор бэкенда
        use_fallback = (
            (reason == "suspicious_output" and fallback_backend)
            or (primary_backend_disabled and fallback_backend)
        )
        if use_fallback:
            current_backend = fallback_backend
            current_engine = fallback_engine_name
            method_prefix = "fallback"
        else:
            current_backend = retry_backend
            current_engine = engine_name
            method_prefix = "retry"

        logger.info(
            f"[{phase_name}][{idx+1}/{len(missing_blocks)}] Повторное распознавание блока "
            f"{block_id} ({reason}), engine: {current_engine}"
        )

        # Пауза с exponential backoff (для LM Studio)
        if _is_lmstudio_backend(current_backend) and idx > 0:
            if consecutive_failures > 0:
                backoff_delay = min(base_delay * (2 ** consecutive_failures), 120)
                if deadline is not None and time.time() + backoff_delay > deadline - _VERIFICATION_RESERVE:
                    stopped_reason = f"deadline задачи (до sleep {backoff_delay}с)"
                    logger.warning(f"{phase_name} верификация прервана: {stopped_reason}")
                    break
                logger.info(f"Backoff delay: {backoff_delay}с (consecutive_failures={consecutive_failures})")
                time.sleep(backoff_delay)
            else:
                if deadline is not None and time.time() + base_delay > deadline - _VERIFICATION_RESERVE:
                    stopped_reason = "deadline задачи (до sleep base_delay)"
                    logger.warning(f"{phase_name} верификация прервана: {stopped_reason}")
                    break
                time.sleep(base_delay)

        # Промежуточная проверка доступности
        if _is_lmstudio_backend(current_backend) and consecutive_failures > 0 and consecutive_failures % 3 == 0:
            if not _check_backend_available(current_backend):
                logger.info(f"{phase_name}: бэкенд недоступен, ожидание 60с...")
                time.sleep(60)

        try:
            block_obj, _ = Block.from_dict(blk_data, migrate_ids=False)
            block_obj.page_index = item["page_index"]

            crop = processor.crop_block_image(block_obj, padding=5)
            if not crop:
                logger.warning(f"Не удалось создать кроп для блока {block_id}")
                consecutive_failures += 1
                continue

            crop_path = retry_crops_dir / f"{block_id}.png"
            crop.save(crop_path, "PNG")

            # Распознавание — с промптом или без
            if use_prompt:
                prompt = _build_retry_prompt(blk_data, pdf_path, cfg)
                ocr_text = current_backend.recognize(crop, prompt=prompt)
            else:
                ocr_text = current_backend.recognize(crop)
            crop.close()

            if ocr_text and not is_error(ocr_text):
                if post_process(ocr_text, blk_data, method_prefix, current_engine):
                    successful_retries += 1
                    consecutive_failures = 0
                    logger.info(
                        f"Блок {block_id} успешно распознан {method_prefix} ({len(ocr_text)} символов)"
                    )
                else:
                    consecutive_failures += 1
            else:
                # Для TEXT suspicious_output: если fallback не помог, попробовать primary
                if (
                    phase_name == "text"
                    and reason == "suspicious_output"
                    and current_backend is fallback_backend
                ):
                    logger.info(f"Блок {block_id}: fallback не помог, пробуем primary backend")
                    crop = processor.crop_block_image(block_obj, padding=5)
                    if crop:
                        ocr_text = retry_backend.recognize(crop)
                        crop.close()
                        if ocr_text and not is_error(ocr_text):
                            if post_process(ocr_text, blk_data, "retry", engine_name):
                                successful_retries += 1
                                consecutive_failures = 0
                                logger.info(
                                    f"Блок {block_id} успешно распознан primary retry ({len(ocr_text)} символов)"
                                )
                                continue

                consecutive_failures += 1
                # Детекция "time budget exhausted"
                if ocr_text and "budget exhausted" in ocr_text:
                    consecutive_budget_exhausted += 1
                    if (
                        consecutive_budget_exhausted >= MAX_BUDGET_EXHAUSTED_BEFORE_FALLBACK
                        and not primary_backend_disabled
                    ):
                        if fallback_backend:
                            primary_backend_disabled = True
                            consecutive_failures = 0
                            consecutive_budget_exhausted = 0
                            logger.warning(
                                f"{phase_name}: primary backend ({engine_name}) отключён после "
                                f"{MAX_BUDGET_EXHAUSTED_BEFORE_FALLBACK} 'budget exhausted' ошибок, "
                                f"переключение на fallback ({fallback_engine_name})"
                            )
                        else:
                            stopped_reason = (
                                f"primary backend budget exhausted × {consecutive_budget_exhausted}, "
                                "fallback недоступен"
                            )
                            logger.warning(f"{phase_name} верификация прервана: {stopped_reason}")
                            break
                else:
                    consecutive_budget_exhausted = 0
                logger.warning(
                    f"Блок {block_id} не распознан при retry: "
                    f"{ocr_text[:100] if ocr_text else 'пусто'}"
                )

        except Exception as e:
            consecutive_failures += 1
            logger.error(f"Ошибка обработки блока {block_id}: {e}", exc_info=True)
            continue

    return successful_retries, stopped_reason


# ── Главная функция верификации ──────────────────────────────────


def verify_and_retry_missing_blocks(
    enriched_ann: dict,
    pdf_path: Path,
    work_dir: Path,
    ocr_backend,
    text_fallback_backend=None,
    image_backend=None,
    stamp_backend=None,
    on_progress: Callable[[int, int], None] = None,
    job_id: str = None,
    deadline: float | None = None,
    before_stamp_phase: Callable = None,
    before_image_phase: Callable = None,
    config: VerificationConfig | None = None,
) -> dict:
    """
    Верификация блоков после OCR и повторное распознавание пропущенных.

    Обрабатывает все типы блоков в три фазы: TEXT → STAMP → IMAGE.

    Args:
        enriched_ann: enriched annotation dict (результат OCR)
        pdf_path: путь к PDF файлу
        work_dir: рабочая директория
        ocr_backend: OCR backend для TEXT блоков (Chandra)
        text_fallback_backend: fallback OCR backend для TEXT suspicious_output
        image_backend: OCR backend для IMAGE блоков (Qwen 27b)
        stamp_backend: OCR backend для STAMP блоков (Qwen 9b)
        on_progress: callback (current, total) для обновления прогресса
        job_id: ID задачи
        deadline: абсолютное время (time.time()) до которого нужно завершить верификацию
        before_stamp_phase: callback для model swap перед STAMP фазой
        before_image_phase: callback для model swap перед IMAGE фазой
        config: конфигурация верификации (лимиты, callbacks)

    Returns:
        Обновлённый dict с результатами повторного распознавания
    """
    cfg = config or VerificationConfig()

    result = copy.deepcopy(enriched_ann)
    pages = result.get("pages", [])

    # ── Сбор проблемных блоков по типам ──
    missing_text = _collect_missing_text_blocks(pages)
    missing_stamp = _collect_missing_stamp_blocks(pages) if stamp_backend else []
    missing_image = _collect_missing_image_blocks(pages) if image_backend else []

    total_missing = len(missing_text) + len(missing_stamp) + len(missing_image)
    if total_missing == 0:
        logger.info("Все блоки распознаны корректно")
        return result

    logger.warning(
        f"Найдено {total_missing} нераспознанных блоков "
        f"(text: {len(missing_text)}, stamp: {len(missing_stamp)}, image: {len(missing_image)})",
        extra={
            "event": "verification_missing_blocks",
            "job_id": job_id,
            "total_blocks": total_missing,
            "text_count": len(missing_text),
            "stamp_count": len(missing_stamp),
            "image_count": len(missing_image),
        },
    )

    # ── Лимиты верификации ──
    max_blocks = cfg.max_retry_blocks
    timeout_min = cfg.verification_timeout_minutes

    is_lmstudio = _is_lmstudio_backend(ocr_backend)
    if is_lmstudio:
        timeout_min = max(timeout_min, 30)

    if deadline is not None:
        remaining = deadline - time.time() - _VERIFICATION_RESERVE
        if remaining <= 0:
            logger.warning(
                f"Верификация пропущена: до deadline задачи осталось {remaining + _VERIFICATION_RESERVE:.0f}с"
            )
            return result
        timeout_min = remaining / 60
        logger.info(
            f"Верификация: deadline задачи через {remaining:.0f}с ({timeout_min:.1f} мин)"
        )

    # Обновляем deadline для бэкендов
    if deadline is not None:
        verification_deadline = deadline - _VERIFICATION_RESERVE
        for backend in (ocr_backend, text_fallback_backend, image_backend, stamp_backend):
            if backend and hasattr(backend, "set_deadline"):
                backend.set_deadline(verification_deadline)

    # Применяем лимит блоков (пропорционально по фазам)
    if max_blocks > 0 and total_missing > max_blocks:
        logger.warning(f"Ограничение верификации: {total_missing} -> {max_blocks} блоков")
        ratio = max_blocks / total_missing
        missing_text = missing_text[:max(1, int(len(missing_text) * ratio))]
        missing_stamp = missing_stamp[:max(1, int(len(missing_stamp) * ratio))] if missing_stamp else []
        missing_image = missing_image[:max(1, int(len(missing_image) * ratio))] if missing_image else []
        total_missing = len(missing_text) + len(missing_stamp) + len(missing_image)

    retry_crops_dir = work_dir / "retry_crops"
    retry_crops_dir.mkdir(exist_ok=True)

    # PDF processor — инъектированный или fallback импорт
    if cfg.pdf_processor_factory is None:
        from services.remote_ocr.server.pdf_streaming_core import StreamingPDFProcessor
        processor_factory = StreamingPDFProcessor
    else:
        processor_factory = cfg.pdf_processor_factory

    start_time = time.monotonic()
    total_successful = 0
    all_stopped_reasons = []

    # Stamp post-processor с инъектированным парсером
    stamp_post_process = _make_stamp_processor(cfg)

    with processor_factory(str(pdf_path)) as processor:

        # ── ФАЗА 1: TEXT блоки ──
        if missing_text:
            logger.info(f"Верификация TEXT фазы: {len(missing_text)} блоков")
            success, stopped = _retry_block_phase(
                missing_text,
                retry_backend=ocr_backend,
                fallback_backend=text_fallback_backend,
                processor=processor,
                retry_crops_dir=retry_crops_dir,
                phase_name="text",
                post_process=_process_text_result,
                pdf_path=pdf_path,
                deadline=deadline,
                timeout_min=timeout_min,
                start_time=start_time,
                job_id=job_id,
                on_progress=on_progress,
                progress_offset=0,
                total_all_phases=total_missing,
                use_prompt=False,
                config=cfg,
            )
            total_successful += success
            if stopped:
                all_stopped_reasons.append(f"text: {stopped}")

        # ── ФАЗА 2: STAMP блоки ──
        if missing_stamp:
            if before_stamp_phase:
                try:
                    logger.info("Верификация: model swap → stamp backend")
                    before_stamp_phase()
                except Exception as e:
                    logger.warning(f"Model swap to stamp failed: {e}")

            logger.info(f"Верификация STAMP фазы: {len(missing_stamp)} блоков")
            success, stopped = _retry_block_phase(
                missing_stamp,
                retry_backend=stamp_backend,
                fallback_backend=None,
                processor=processor,
                retry_crops_dir=retry_crops_dir,
                phase_name="stamp",
                post_process=stamp_post_process,
                pdf_path=pdf_path,
                deadline=deadline,
                timeout_min=timeout_min,
                start_time=start_time,
                job_id=job_id,
                on_progress=on_progress,
                progress_offset=len(missing_text),
                total_all_phases=total_missing,
                use_prompt=True,
                config=cfg,
            )
            total_successful += success
            if stopped:
                all_stopped_reasons.append(f"stamp: {stopped}")

        # ── ФАЗА 3: IMAGE блоки ──
        if missing_image:
            if before_image_phase:
                try:
                    logger.info("Верификация: model swap → image backend")
                    before_image_phase()
                except Exception as e:
                    logger.warning(f"Model swap to image failed: {e}")

            logger.info(f"Верификация IMAGE фазы: {len(missing_image)} блоков")
            success, stopped = _retry_block_phase(
                missing_image,
                retry_backend=image_backend,
                fallback_backend=None,
                processor=processor,
                retry_crops_dir=retry_crops_dir,
                phase_name="image",
                post_process=_process_image_result,
                pdf_path=pdf_path,
                deadline=deadline,
                timeout_min=timeout_min,
                start_time=start_time,
                job_id=job_id,
                on_progress=on_progress,
                progress_offset=len(missing_text) + len(missing_stamp),
                total_all_phases=total_missing,
                use_prompt=True,
                config=cfg,
            )
            total_successful += success
            if stopped:
                all_stopped_reasons.append(f"image: {stopped}")

    # ── Итоговый лог ──
    elapsed_total = (time.monotonic() - start_time) / 60
    status_parts = [f"{total_successful}/{total_missing} блоков восстановлено"]
    if all_stopped_reasons:
        status_parts.append(f"прервано: {'; '.join(all_stopped_reasons)}")
    status_parts.append(f"за {elapsed_total:.1f} мин")

    logger.info(
        f"Верификация завершена: {', '.join(status_parts)}",
        extra={
            "event": "verification_completed",
            "job_id": job_id,
            "recognized_count": total_successful,
            "total_blocks": total_missing,
            "duration_ms": int((time.monotonic() - start_time) * 1000),
        },
    )
    return result
