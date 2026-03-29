"""Тесты нового IMAGE OCR контракта и канонических ключей STAMP."""
import json

import pytest

from rd_core.ocr.generator_common import (
    extract_image_ocr_data,
    format_stamp_parts,
    is_image_ocr_json,
    parse_ocr_json,
)


# ---------------------------------------------------------------------------
# parse_ocr_json (переименованная parse_stamp_json)
# ---------------------------------------------------------------------------
class TestParseOcrJson:
    def test_parse_valid_json(self):
        data = '{"fragment_type": "План", "content_summary": "тест"}'
        result = parse_ocr_json(data)
        assert result == {"fragment_type": "План", "content_summary": "тест"}

    def test_parse_json_in_code_fence(self):
        data = '```json\n{"document_code": "АР-01"}\n```'
        result = parse_ocr_json(data)
        assert result == {"document_code": "АР-01"}

    def test_parse_none(self):
        assert parse_ocr_json(None) is None

    def test_parse_empty_string(self):
        assert parse_ocr_json("") is None

    def test_parse_non_json(self):
        assert parse_ocr_json("not json at all") is None


# ---------------------------------------------------------------------------
# extract_image_ocr_data — новый контракт
# ---------------------------------------------------------------------------
class TestExtractImageOcrData:
    def test_new_contract_full(self):
        data = {
            "fragment_type": "Однолинейная схема",
            "location": {
                "grid_lines": "А-1, Б-2",
                "zone_name": "Электрощитовая",
                "level_or_elevation": "-3.600",
            },
            "content_summary": "Фрагмент однолинейной схемы ВРУ",
            "detailed_description": "Видны автоматы QF1-QF5...",
            "key_entities": ["QF1", "QF2", "ВРУ-1"],
        }
        result = extract_image_ocr_data(data)
        assert result["fragment_type"] == "Однолинейная схема"
        assert result["zone_name"] == "Электрощитовая"
        assert result["grid_lines"] == "А-1, Б-2"
        assert result["level_or_elevation"] == "-3.600"
        assert result["content_summary"] == "Фрагмент однолинейной схемы ВРУ"
        assert result["detailed_description"] == "Видны автоматы QF1-QF5..."
        assert result["key_entities"] == ["QF1", "QF2", "ВРУ-1"]

    def test_no_clean_ocr_text(self):
        """clean_ocr_text удалён из контракта."""
        data = {
            "fragment_type": "План",
            "content_summary": "тест",
        }
        result = extract_image_ocr_data(data)
        assert "clean_ocr_text" not in result

    def test_fragment_type_default(self):
        data = {"content_summary": "тест"}
        result = extract_image_ocr_data(data)
        assert result["fragment_type"] == ""

    def test_level_or_elevation_default(self):
        data = {"location": {"grid_lines": "А"}}
        result = extract_image_ocr_data(data)
        assert result["level_or_elevation"] == ""

    def test_key_entities_limit_100(self):
        data = {"key_entities": [f"E{i}" for i in range(150)]}
        result = extract_image_ocr_data(data)
        assert len(result["key_entities"]) == 100

    def test_analysis_wrapper(self):
        data = {
            "analysis": {
                "fragment_type": "Разрез",
                "content_summary": "Разрез 1-1",
            }
        }
        result = extract_image_ocr_data(data)
        assert result["fragment_type"] == "Разрез"
        assert result["content_summary"] == "Разрез 1-1"


# ---------------------------------------------------------------------------
# is_image_ocr_json — детектирование формата
# ---------------------------------------------------------------------------
class TestIsImageOcrJson:
    def test_new_contract_detected(self):
        data = {"fragment_type": "План", "detailed_description": "описание"}
        assert is_image_ocr_json(data) is True

    def test_old_contract_still_detected(self):
        """Старые данные с content_summary тоже детектируются."""
        data = {"content_summary": "тест", "detailed_description": "описание"}
        assert is_image_ocr_json(data) is True

    def test_stamp_not_detected(self):
        data = {"document_code": "АР-01", "project_name": "ЖК"}
        assert is_image_ocr_json(data) is False

    def test_empty_not_detected(self):
        assert is_image_ocr_json({}) is False

    def test_non_dict_not_detected(self):
        assert is_image_ocr_json("string") is False


# ---------------------------------------------------------------------------
# format_stamp_parts — канонические ключи + legacy fallback
# ---------------------------------------------------------------------------
class TestFormatStampParts:
    def test_canonical_keys(self):
        """Новые канонические ключи: surname, change_num, doc_num."""
        stamp = {
            "document_code": "АР-01",
            "signatures": [
                {"role": "ГИП", "surname": "Петров И.И.", "date": "01.03.2026"}
            ],
            "revisions": [
                {"change_num": "1", "doc_num": "Изм-001", "date": "15.03.2026"}
            ],
        }
        parts = format_stamp_parts(stamp)
        parts_dict = dict(parts)
        assert "Ответственные" in parts_dict
        assert "Петров И.И." in parts_dict["Ответственные"]
        assert "Статус" in parts_dict
        assert "Изм. 1" in parts_dict["Статус"]
        assert "Док. № Изм-001" in parts_dict["Статус"]

    def test_legacy_keys(self):
        """Legacy ключи: name, revision_number, document_number."""
        stamp = {
            "document_code": "АР-01",
            "signatures": [
                {"role": "ГИП", "name": "Сидоров П.П.", "date": "01.03.2026"}
            ],
            "revisions": [
                {
                    "revision_number": "2",
                    "document_number": "Изм-002",
                    "date": "20.03.2026",
                }
            ],
        }
        parts = format_stamp_parts(stamp)
        parts_dict = dict(parts)
        assert "Сидоров П.П." in parts_dict["Ответственные"]
        assert "Изм. 2" in parts_dict["Статус"]
        assert "Док. № Изм-002" in parts_dict["Статус"]

    def test_canonical_overrides_legacy(self):
        """Канонический ключ surname приоритетнее legacy name."""
        stamp = {
            "signatures": [
                {"role": "ГИП", "surname": "Новый", "name": "Старый"}
            ],
        }
        parts = format_stamp_parts(stamp)
        parts_dict = dict(parts)
        assert "Новый" in parts_dict["Ответственные"]
        assert "Старый" not in parts_dict["Ответственные"]

    def test_basic_fields(self):
        stamp = {
            "document_code": "ЭОМ-01",
            "stage": "Р",
            "sheet_number": "3",
            "total_sheets": "15",
            "project_name": "ЖК Альфа",
            "sheet_name": "План освещения",
            "organization": "ООО Проект",
        }
        parts = format_stamp_parts(stamp)
        parts_dict = dict(parts)
        assert parts_dict["Шифр"] == "ЭОМ-01"
        assert parts_dict["Стадия"] == "Р"
        assert "3" in parts_dict["Лист"]
        assert "15" in parts_dict["Лист"]
        assert parts_dict["Объект"] == "ЖК Альфа"
        assert parts_dict["Наименование"] == "План освещения"
        assert parts_dict["Организация"] == "ООО Проект"
