"""Ручная миграция legacy JSON sidecar-файлов в Supabase."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import List, Optional

from PySide6.QtWidgets import QMessageBox

logger = logging.getLogger(__name__)


@dataclass
class MigrationReport:
    """Отчёт о миграции."""

    blocks_enriched: int = 0
    r2_deleted: List[str] = field(default_factory=list)
    node_files_deleted: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


def _build_legacy_keys(r2_key: str) -> dict[str, str]:
    """Построить ключи legacy JSON файлов по r2_key PDF."""
    pdf_path = PurePosixPath(r2_key)
    pdf_stem = pdf_path.stem
    pdf_parent = str(pdf_path.parent)
    return {
        "result": f"{pdf_parent}/{pdf_stem}_result.json",
        "annotation": f"{pdf_parent}/{pdf_stem}_annotation.json",
        "blocks": f"{pdf_parent}/{pdf_stem}_blocks.json",
    }


def _enrich_annotation(
    ann_data: dict, result_data: dict, report: MigrationReport
) -> None:
    """Мержит enriched поля из result.json в аннотацию (in-place)."""
    enriched_by_id: dict[str, dict] = {}
    for page in result_data.get("pages", []):
        for blk in page.get("blocks", []):
            block_id = blk.get("id")
            if not block_id:
                continue
            enriched_by_id[block_id] = {
                k: blk.get(k)
                for k in (
                    "ocr_html",
                    "ocr_json",
                    "ocr_meta",
                    "crop_url",
                    "stamp_data",
                    "ocr_text",
                )
                if blk.get(k)
            }

    for page in ann_data.get("pages", []):
        for blk in page.get("blocks", []):
            block_id = blk.get("id")
            enriched = enriched_by_id.get(block_id)
            if not enriched:
                continue
            for key, value in enriched.items():
                if not blk.get(key):
                    blk[key] = value
                    report.blocks_enriched += 1


def migrate_legacy_json_to_supabase(
    node_id: str, r2_key: str, parent_widget: Optional[object] = None
) -> MigrationReport:
    """Перенести разметку блоков из legacy JSON файлов в Supabase.

    Workflow:
    1. Загружает текущую аннотацию из Supabase
    2. Скачивает result.json из R2, извлекает enriched поля
    3. Мержит enriched поля в аннотацию
    4. Сохраняет enriched аннотацию в Supabase
    5. Удаляет legacy JSON файлы из R2
    6. Удаляет записи из node_files
    """
    from app.tree_client import FileType, TreeClient
    from rd_core.r2_storage import R2Storage

    report = MigrationReport()
    client = TreeClient()
    r2 = R2Storage()

    legacy_keys = _build_legacy_keys(r2_key)

    # 1. Загрузить текущую аннотацию из Supabase
    ann_data = client.get_annotation(node_id)
    if not ann_data:
        report.errors.append("Аннотация не найдена в Supabase")
        return report

    # 2. Скачать result.json и обогатить аннотацию
    result_key = legacy_keys["result"]
    try:
        result_text = r2.download_text(result_key)
        if result_text:
            result_data = json.loads(result_text)
            _enrich_annotation(ann_data, result_data, report)
            logger.info(f"Enriched {report.blocks_enriched} block fields from {result_key}")
    except Exception as e:
        logger.warning(f"Could not load result.json: {e}")

    # 3. Сохранить enriched аннотацию в Supabase
    try:
        success = client.save_annotation(node_id, ann_data)
        if not success:
            report.errors.append("Не удалось сохранить аннотацию в Supabase")
            return report
    except Exception as e:
        report.errors.append(f"Ошибка сохранения: {e}")
        return report

    # 4. Удалить legacy JSON из R2
    for label, key in legacy_keys.items():
        try:
            if r2.exists(key, use_cache=False):
                r2.delete_object(key)
                report.r2_deleted.append(key)
                logger.info(f"Deleted from R2: {key}")
        except Exception as e:
            logger.warning(f"Failed to delete {key} from R2: {e}")

    # 5. Удалить записи из node_files
    legacy_file_types = {FileType.RESULT_JSON, FileType.BLOCKS_INDEX, FileType.ANNOTATION}
    try:
        node_files = client.get_node_files(node_id)
        for nf in node_files:
            if nf.file_type in legacy_file_types:
                client.delete_node_file(nf.id)
                report.node_files_deleted.append(
                    f"{nf.file_type.value}: {nf.file_name}"
                )
                logger.info(f"Deleted node_file: {nf.file_type.value} ({nf.file_name})")
    except Exception as e:
        report.errors.append(f"Ошибка удаления node_files: {e}")

    return report


def show_migration_report(
    report: MigrationReport, parent_widget: Optional[object] = None
) -> None:
    """Показать отчёт о миграции в диалоге."""
    lines: list[str] = []

    if report.blocks_enriched > 0:
        lines.append(f"Обогащено полей блоков: {report.blocks_enriched}")

    if report.r2_deleted:
        lines.append(f"\nУдалено из R2 ({len(report.r2_deleted)}):")
        for key in report.r2_deleted:
            lines.append(f"  - {key}")

    if report.node_files_deleted:
        lines.append(f"\nУдалено из node_files ({len(report.node_files_deleted)}):")
        for entry in report.node_files_deleted:
            lines.append(f"  - {entry}")

    if report.errors:
        lines.append(f"\nОшибки ({len(report.errors)}):")
        for err in report.errors:
            lines.append(f"  ! {err}")

    if not lines:
        lines.append("Legacy JSON файлы не найдены, нечего мигрировать.")

    title = "Миграция завершена" if report.success else "Миграция завершена с ошибками"
    icon = QMessageBox.Information if report.success else QMessageBox.Warning

    msg = QMessageBox(parent_widget)
    msg.setIcon(icon)
    msg.setWindowTitle(title)
    msg.setText("\n".join(lines))
    msg.exec()
