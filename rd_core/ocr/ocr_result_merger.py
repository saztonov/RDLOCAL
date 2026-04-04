"""Объединение OCR результатов: annotation dict + ocr HTML -> enriched dict."""
from __future__ import annotations

import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Optional

from rd_core.ocr.generator_common import (
    HTML_FOOTER,
    collect_inheritable_stamp_data_dict,
    extract_stamp_from_doc_name,
    format_stamp_parts,
    get_block_armor_id,
    get_html_header,
    is_stamp_block,
    parse_ocr_json,
    propagate_stamp_data,
    sanitize_html,
)

from .ocr_html_parser import build_segments_from_html

logger = logging.getLogger(__name__)


def _build_crop_url(block_id: str, r2_public_url: str, project_name: str) -> str:
    """Сформировать URL кропа для блока."""
    return f"{r2_public_url}/tree_docs/{project_name}/crops/{block_id}.pdf"


def enrich_annotation_dict(
    ann: dict,
    html_text: str,
    project_name: str,
    r2_public_url: Optional[str] = None,
    score_cutoff: int = 90,
) -> dict:
    """
    Обогатить annotation dict данными из OCR HTML. Чистая in-memory трансформация.

    Добавляет к каждому блоку:
    - ocr_html: HTML-фрагмент блока (санитизированный)
    - ocr_json: распарсенный JSON из ocr_text (для IMAGE блоков)
    - crop_url: ссылка на кроп (для IMAGE блоков, кроме штампов)
    - stamp_data: унаследованные данные штампа
    - ocr_meta: {method, match_score, marker_text_sample}

    Конвертирует page_number/page_index в 1-based для внешнего формата.

    Args:
        ann: Словарь аннотации (не модифицируется, используется deepcopy).
        html_text: Полный HTML текст OCR результата.
        project_name: Имя проекта для формирования crop_url.
        r2_public_url: Базовый URL R2 хранилища.
        score_cutoff: Порог совпадения для парсера HTML.

    Returns:
        Обогащённый словарь аннотации.
    """
    if not r2_public_url:
        r2_public_url = os.getenv("R2_PUBLIC_URL", "https://pub-9530315f35b34246a04e8ad8144e46d5.r2.dev")

    expected_ids = [
        b["id"]
        for p in ann.get("pages", [])
        for b in p.get("blocks", [])
        if b.get("category_code") != "stamp"
    ]

    result = deepcopy(ann)

    if not expected_ids:
        logger.info("Нет блоков для обработки")
        return result

    segments, meta = build_segments_from_html(
        html_text, expected_ids, score_cutoff=score_cutoff
    )

    missing: list[str] = []
    matched = 0

    for page in result.get("pages", []):
        # Конвертируем page_number в 1-based для внешнего формата
        if "page_number" in page:
            page["page_number"] = page["page_number"] + 1
        for blk in page.get("blocks", []):
            bid = blk["id"]
            block_type = blk.get("block_type", "text")

            # Конвертируем page_index в 1-based для внешнего формата
            if "page_index" in blk:
                blk["page_index"] = blk["page_index"] + 1

            # HTML фрагмент (санитизируем от артефактов datalab)
            raw_html = segments.get(bid, "")
            blk["ocr_html"] = sanitize_html(raw_html) if raw_html else ""
            blk["ocr_meta"] = meta.get(
                bid, {"method": [], "match_score": 0.0, "marker_text_sample": ""}
            )

            # Для IMAGE блоков: парсим JSON из ocr_text и добавляем crop_url
            if block_type == "image":
                ocr_text = blk.get("ocr_text", "")
                parsed_json = parse_ocr_json(ocr_text)
                if parsed_json:
                    blk["ocr_json"] = parsed_json

                # Добавляем ссылку на кроп (кроме штампов)
                if not is_stamp_block(blk):
                    if project_name:
                        blk["crop_url"] = _build_crop_url(
                            bid, r2_public_url, project_name
                        )
                    elif blk.get("image_file"):
                        crop_name = Path(blk["image_file"]).name
                        blk["crop_url"] = f"{r2_public_url}/crops/{crop_name}"

            # Stamp-блоки: парсим ocr_text → ocr_json + stamp_data для propagation
            if is_stamp_block(blk):
                ocr_text = blk.get("ocr_text", "")
                parsed_json = parse_ocr_json(ocr_text)
                if parsed_json:
                    blk["ocr_json"] = parsed_json
                    blk["stamp_data"] = parsed_json
                continue
            if blk["ocr_html"]:
                matched += 1
            else:
                missing.append(bid)

    # Собираем общие данные штампа (fallback — из имени PDF)
    inherited_stamp = collect_inheritable_stamp_data_dict(result.get("pages", []))
    if not inherited_stamp:
        pdf_path = result.get("pdf_path", "")
        doc_name_for_stamp = Path(pdf_path).stem if pdf_path else None
        inherited_stamp = extract_stamp_from_doc_name(doc_name_for_stamp)

    # Распространение данных штампа
    for page in result.get("pages", []):
        propagate_stamp_data(page, inherited_stamp)

    if missing:
        logger.warning(
            f"Не найдено HTML для {len(missing)} блоков. Примеры: {missing[:3]}"
        )

    logger.info(
        f"Annotation enriched: {matched}/{len(expected_ids)} блоков сопоставлено"
    )

    return result


def regenerate_md_from_result(
    result: dict, output_path: Path, doc_name: Optional[str] = None
) -> None:
    """Регенерировать Markdown файл из result.json."""
    from rd_core.ocr.md import generate_md_from_result

    try:
        generate_md_from_result(result, output_path, doc_name=doc_name)
    except Exception as e:
        logger.warning(f"Ошибка регенерации MD: {e}")


def regenerate_html_from_result(
    result: dict, output_path: Path, doc_name: Optional[str] = None
) -> None:
    """
    Регенерировать HTML файл из result.json с правильно разделёнными блоками.
    Использует ocr_html (уже разделённый по маркерам) вместо ocr_text.
    """
    from rd_core.ocr.export_stats import ExportStats

    if not doc_name:
        doc_name = result.get("pdf_path", "OCR Result")

    # Используем общий HTML шаблон
    html_parts = [get_html_header(doc_name)]

    total_blocks = sum(len(p.get("blocks", [])) for p in result.get("pages", []))
    excluded_stamp = 0
    block_count = 0

    for page in result.get("pages", []):
        page_num = page.get("page_number", "")

        for idx, blk in enumerate(page.get("blocks", [])):
            # Пропускаем блоки штампа
            if is_stamp_block(blk):
                excluded_stamp += 1
                continue

            block_id = blk.get("id", "")
            block_type = blk.get("block_type", "text")
            ocr_html = blk.get("ocr_html", "")
            ocr_text = blk.get("ocr_text", "")
            created_at = blk.get("created_at")

            # Блок отображается если есть контент ИЛИ метаданные
            if not ocr_html and not created_at and not ocr_text:
                continue

            block_count += 1

            html_parts.append(f'<div class="block block-type-{block_type}">')
            html_parts.append(
                f'<div class="block-header">Блок #{idx + 1} (стр. {page_num}) | Тип: {block_type}</div>'
            )
            html_parts.append('<div class="block-content">')
            html_parts.append(f"<p>BLOCK: {get_block_armor_id(block_id)}</p>")

            # Linked block - в шапку
            linked_id = blk.get("linked_block_id")
            if linked_id:
                linked_armor = get_block_armor_id(linked_id)
                html_parts.append(f"<p><b>Linked block:</b> {linked_armor}</p>")

            # Created - в шапку
            if created_at:
                html_parts.append(f"<p><b>Created:</b> {created_at}</p>")

            # Stamp info - в шапку блока
            stamp_data = blk.get("stamp_data")
            if stamp_data:
                parts = format_stamp_parts(stamp_data)
                if parts:
                    stamp_html_parts = [f"<b>{key}:</b> {value}" for key, value in parts]
                    html_parts.append(
                        '<div class="stamp-info">' + " | ".join(stamp_html_parts) + "</div>"
                    )

            # Для IMAGE блоков добавляем ссылку на кроп
            if block_type == "image" and blk.get("crop_url"):
                if "Открыть кроп изображения" not in ocr_html:
                    crop_url = blk["crop_url"]
                    html_parts.append(
                        f'<p><a href="{crop_url}" target="_blank"><b>🖼️ Открыть кроп изображения</b></a></p>'
                    )

            # Defensive: ocr_html может содержать JSON-dump из старых данных
            if ocr_html:
                from rd_core.ocr_result import is_suspicious_output
                suspicious, _ = is_suspicious_output(ocr_html, ocr_html)
                if suspicious:
                    ocr_html = ""

            # Санитизируем HTML от мусорных артефактов datalab
            if ocr_html:
                html_parts.append(sanitize_html(ocr_html))
            elif ocr_text:
                from rd_core.ocr.html_generator import _extract_html_from_ocr_text

                fallback_html = _extract_html_from_ocr_text(ocr_text)
                if fallback_html:
                    html_parts.append(fallback_html)
            html_parts.append("</div></div>")

    html_parts.append(HTML_FOOTER)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html_parts))

    stats = ExportStats(
        total_blocks=total_blocks,
        excluded_stamp_blocks=excluded_stamp,
        exported_blocks=block_count,
    )
    logger.info(
        f"HTML регенерирован: {output_path} ({stats.log_summary('HTML')})"
    )
