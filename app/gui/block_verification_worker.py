"""Фоновый worker для верификации блоков"""

import json
import logging
import re
from pathlib import PurePosixPath

from PySide6.QtCore import QThread, Signal

from app.gui.block_verification_models import BlockInfo, VerificationResult

logger = logging.getLogger(__name__)


class VerificationWorker(QThread):
    """Фоновый worker для верификации"""

    progress = Signal(str)
    finished = Signal(object)  # VerificationResult или str (ошибка)

    def __init__(self, r2_key: str, node_id: str = ""):
        super().__init__()
        self.r2_key = r2_key
        self.node_id = node_id

    def run(self):
        try:
            result = self._verify()
            self.finished.emit(result)
        except Exception as e:
            logger.error(f"Verification failed: {e}", exc_info=True)
            self.finished.emit(f"Ошибка верификации: {e}")

    def _verify(self) -> VerificationResult:
        from rd_core.r2_storage import R2Storage

        r2 = R2Storage()
        result = VerificationResult()

        # Формируем ключи файлов
        pdf_path = PurePosixPath(self.r2_key)
        pdf_stem = pdf_path.stem
        pdf_parent = str(pdf_path.parent)

        ocr_r2_key = f"{pdf_parent}/{pdf_stem}_ocr.html"
        res_r2_key = f"{pdf_parent}/{pdf_stem}_result.json"
        md_r2_key = f"{pdf_parent}/{pdf_stem}_document.md"

        # 1. Загружаем и парсим аннотацию из Supabase
        self.progress.emit("Загрузка аннотации...")
        ann_data = None
        if self.node_id:
            from app.tree_client import TreeClient
            client = TreeClient()
            ann_data = client.get_annotation(self.node_id)

        if not ann_data:
            raise ValueError("Аннотация не найдена в базе данных")

        for page in ann_data.get("pages", []):
            page_num = page.get("page_number", 0)
            for block in page.get("blocks", []):
                block_id = block.get("id", "")
                block_type = block.get("block_type", "text")
                category_code = block.get("category_code")
                linked_block_id = block.get("linked_block_id")

                block_info = BlockInfo(
                    id=block_id,
                    page_index=page_num,
                    block_type=block_type,
                    category_code=category_code,
                    linked_block_id=linked_block_id,
                )
                result.ann_blocks.append(block_info)
                result.ann_total += 1

                if block_info.is_stamp:
                    result.ann_stamp += 1
                elif block_type == "text":
                    result.ann_text += 1
                    result.expected_blocks.add(block_id)
                elif block_type == "image":
                    result.ann_image += 1
                    result.expected_blocks.add(block_id)

        # Определяем embedded TEXT блоки (связаны с IMAGE через linked_block_id)
        all_blocks_by_id = {b.id: b for b in result.ann_blocks}
        for block_info in result.ann_blocks:
            if block_info.block_type == "text" and block_info.linked_block_id:
                linked_id = block_info.linked_block_id
                if linked_id in all_blocks_by_id:
                    linked_block = all_blocks_by_id[linked_id]
                    if linked_block.block_type == "image":
                        result.embedded_text_ids.add(block_info.id)

        # 2. Загружаем и парсим ocr.html
        self.progress.emit("Загрузка ocr.html...")
        ocr_content = r2.download_text(ocr_r2_key)
        if ocr_content:
            # Ищем маркеры BLOCK: XXXX-XXXX-XXX
            block_pattern = re.compile(
                r"BLOCK:\s*([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{3})"
            )
            for match in block_pattern.finditer(ocr_content):
                result.ocr_html_blocks.add(match.group(1))

        # Индекс блоков для контентной проверки
        all_blocks_by_id = {b.id: b for b in result.ann_blocks}

        # 3. Загружаем и парсим result.json + контентная проверка
        self.progress.emit("Загрузка result.json...")
        res_content = r2.download_text(res_r2_key)
        if res_content:
            from rd_core.ocr_result import is_any_error, is_suspicious_output

            res_data = json.loads(res_content)
            for page in res_data.get("pages", []):
                for block in page.get("blocks", []):
                    block_id = block.get("id", "")
                    if block_id:
                        result.result_blocks.add(block_id)

                    # Контентная проверка OCR-результата
                    ocr_text = block.get("ocr_text", "")
                    block_info = all_blocks_by_id.get(block_id)
                    if block_info and block_info.id in result.expected_blocks:
                        if ocr_text and is_any_error(ocr_text):
                            result.error_blocks.append(block_info)
                            result.error_reasons[block_id] = ocr_text
                        elif ocr_text:
                            ocr_html = block.get("ocr_html", "")
                            suspicious, reason = is_suspicious_output(ocr_text, ocr_html)
                            if suspicious:
                                result.suspicious_blocks.append(block_info)
                                result.suspicious_reasons[block_id] = reason

        # 4. Загружаем и парсим document.md
        self.progress.emit("Загрузка document.md...")
        md_content = r2.download_text(md_r2_key)
        if md_content:
            # Ищем маркеры в формате: ### BLOCK [TYPE]: XXXX-XXXX-XXX
            block_pattern = re.compile(
                r"###\s+BLOCK\s+\[[A-Z]+\]:\s*([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{3})"
            )
            for match in block_pattern.finditer(md_content):
                result.document_md_blocks.add(match.group(1))

        # 5. Находим отсутствующие блоки
        self.progress.emit("Анализ расхождений...")

        for block_info in result.ann_blocks:
            if block_info.is_stamp:
                continue  # Штампы не проверяем

            if block_info.id not in result.ocr_html_blocks:
                result.missing_in_ocr_html.append(block_info)

            if block_info.id not in result.result_blocks:
                result.missing_in_result.append(block_info)

            if block_info.id not in result.document_md_blocks:
                # Не считать отсутствующим, если это embedded TEXT (связан с IMAGE)
                if block_info.id not in result.embedded_text_ids:
                    result.missing_in_document_md.append(block_info)

        return result
