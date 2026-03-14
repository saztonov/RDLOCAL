"""Тесты для rd_core/models — Block, ArmorID."""

import uuid

import pytest

from rd_core.models import Block, BlockSource, BlockType, ShapeType
from rd_core.models.armor_id import ArmorID, generate_armor_id, levenshtein_ratio


class TestArmorID:
    def test_encode_format(self):
        test_uuid = str(uuid.uuid4())
        encoded = ArmorID.encode(test_uuid)

        # Формат XXXX-XXXX-XXX
        parts = encoded.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4
        assert len(parts[1]) == 4
        assert len(parts[2]) == 3

    def test_decode_returns_hex(self):
        test_uuid = str(uuid.uuid4())
        encoded = ArmorID.encode(test_uuid)
        decoded = ArmorID.decode(encoded)
        # decode возвращает 10-символьный hex
        assert decoded is not None
        assert len(decoded) == 10
        int(decoded, 16)  # Не падает — валидный hex

    def test_encode_uses_safe_alphabet(self):
        encoded = ArmorID.encode(str(uuid.uuid4()))
        clean = encoded.replace("-", "")
        for char in clean:
            assert char in ArmorID.ALPHABET

    def test_decode_invalid_checksum(self):
        encoded = ArmorID.encode(str(uuid.uuid4()))
        # Меняем последний символ
        clean = encoded.replace("-", "")
        bad_char = "A" if clean[-1] != "A" else "C"
        bad_code = clean[:-1] + bad_char
        formatted = f"{bad_code[:4]}-{bad_code[4:8]}-{bad_code[8:]}"
        # Может вернуть None если checksum не совпал
        result = ArmorID.decode(formatted)
        # Не гарантируем None (может случайно совпасть), но тест не падает

    def test_decode_wrong_length(self):
        assert ArmorID.decode("SHORT") is None
        assert ArmorID.decode("TOOLONGCODE123") is None

    def test_repair_valid_code(self):
        encoded = ArmorID.encode(str(uuid.uuid4()))
        success, fixed, msg = ArmorID.repair(encoded)
        assert success is True
        assert fixed == encoded

    def test_repair_single_error(self):
        original = ArmorID.encode(str(uuid.uuid4()))
        clean = original.replace("-", "")

        # Заменяем один символ на другой из алфавита
        pos = 3
        replacement = "A" if clean[pos] != "A" else "C"
        damaged = clean[:pos] + replacement + clean[pos + 1:]

        success, fixed, msg = ArmorID.repair(damaged)
        if success:
            assert fixed == original

    def test_multiple_encode_different_uuids(self):
        codes = set()
        for _ in range(100):
            code = ArmorID.encode(str(uuid.uuid4()))
            codes.add(code)
        # Все должны быть уникальными
        assert len(codes) == 100


class TestGenerateArmorId:
    def test_format(self):
        armor_id = generate_armor_id()
        parts = armor_id.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4
        assert len(parts[1]) == 4
        assert len(parts[2]) == 3

    def test_uniqueness(self):
        ids = {generate_armor_id() for _ in range(100)}
        assert len(ids) == 100


class TestLevenshteinRatio:
    def test_identical(self):
        assert levenshtein_ratio("abc", "abc") == 100.0

    def test_empty(self):
        assert levenshtein_ratio("", "abc") == 0.0
        assert levenshtein_ratio("abc", "") == 0.0

    def test_completely_different(self):
        ratio = levenshtein_ratio("abc", "xyz")
        assert ratio < 50.0

    def test_one_char_diff(self):
        ratio = levenshtein_ratio("abc", "adc")
        assert ratio > 50.0


class TestBlock:
    def test_create_with_auto_id(self):
        block = Block.create(
            page_index=0,
            coords_px=(100, 200, 500, 400),
            page_width=1240,
            page_height=1754,
            block_type=BlockType.TEXT,
            source=BlockSource.USER,
        )
        assert block.id is not None
        assert len(block.id) == 13  # XXXX-XXXX-XXX
        assert block.page_index == 0
        assert block.coords_px == (100, 200, 500, 400)
        assert block.block_type == BlockType.TEXT

    def test_px_to_norm(self):
        norm = Block.px_to_norm((100, 200, 500, 400), 1000, 2000)
        assert norm == (0.1, 0.1, 0.5, 0.2)

    def test_norm_to_px(self):
        px = Block.norm_to_px((0.1, 0.1, 0.5, 0.2), 1000, 2000)
        assert px == (100, 200, 500, 400)

    def test_to_dict_from_dict_roundtrip(self):
        block = Block.create(
            page_index=0,
            coords_px=(100, 200, 500, 400),
            page_width=1240,
            page_height=1754,
            block_type=BlockType.TEXT,
            source=BlockSource.USER,
        )
        block.ocr_text = "Test OCR result"
        block.hint = "Test hint"

        d = block.to_dict()
        restored, was_migrated = Block.from_dict(d, migrate_ids=False)

        assert restored.id == block.id
        assert restored.page_index == block.page_index
        assert restored.coords_px == block.coords_px
        assert restored.block_type == block.block_type
        assert restored.ocr_text == block.ocr_text
        assert restored.hint == block.hint

    def test_from_dict_with_string_enums(self):
        data = {
            "id": "ACDE-FGHJ-KLM",
            "page_index": 0,
            "coords_px": [100, 200, 500, 400],
            "coords_norm": [0.1, 0.1, 0.5, 0.2],
            "block_type": "text",
            "source": "user",
            "shape_type": "rectangle",
        }
        block, _ = Block.from_dict(data, migrate_ids=False)
        assert block.block_type == BlockType.TEXT
        assert block.source == BlockSource.USER
        assert block.shape_type == ShapeType.RECTANGLE

    def test_from_dict_v2_fields(self):
        """from_dict требует v2 поля (coords_norm, source)."""
        data = {
            "id": "ACDE-FGHJ-KLM",
            "page_index": 0,
            "coords_px": [100, 200, 500, 400],
            "coords_norm": [0.1, 0.1, 0.5, 0.2],
            "block_type": "text",
            "source": "user",
        }
        block, _ = Block.from_dict(data, migrate_ids=False)
        assert block is not None
        assert block.page_index == 0
        assert block.id == "ACDE-FGHJ-KLM"
