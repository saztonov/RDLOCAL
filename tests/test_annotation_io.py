"""Тесты для rd_core/annotation_io.py — миграция форматов и round-trip."""

import json

from rd_core.annotation_io import (
    ANNOTATION_FORMAT_VERSION,
    AnnotationIO,
    detect_annotation_version,
    is_flat_format,
    migrate_annotation_data,
    migrate_flat_to_structured,
    validate_annotation_structure,
)


class TestIsFlatFormat:
    def test_flat_list_with_page_index(self, annotation_v0_data):
        assert is_flat_format(annotation_v0_data) is True

    def test_structured_dict(self, annotation_v1_data):
        assert is_flat_format(annotation_v1_data) is False

    def test_empty_list(self):
        assert is_flat_format([]) is False

    def test_non_list(self):
        assert is_flat_format({"pages": []}) is False

    def test_list_without_page_index(self):
        assert is_flat_format([{"name": "test"}]) is False


class TestValidateAnnotationStructure:
    def test_valid_v1(self, annotation_v1_data):
        is_valid, errors = validate_annotation_structure(annotation_v1_data)
        assert is_valid is True
        assert errors == []

    def test_valid_v2(self, annotation_v2_data):
        is_valid, errors = validate_annotation_structure(annotation_v2_data)
        assert is_valid is True

    def test_missing_pdf_path(self):
        is_valid, errors = validate_annotation_structure({"pages": []})
        assert is_valid is False
        assert any("pdf_path" in e for e in errors)

    def test_missing_pages(self):
        is_valid, errors = validate_annotation_structure({"pdf_path": "test.pdf"})
        assert is_valid is False
        assert any("pages" in e for e in errors)

    def test_non_dict(self):
        is_valid, errors = validate_annotation_structure([])
        assert is_valid is False


class TestDetectAnnotationVersion:
    def test_explicit_v2(self, annotation_v2_data):
        assert detect_annotation_version(annotation_v2_data) == 2

    def test_v1_without_coords_norm(self, annotation_v1_data):
        assert detect_annotation_version(annotation_v1_data) == 1

    def test_empty_pages(self):
        data = {"pdf_path": "", "pages": []}
        assert detect_annotation_version(data) == ANNOTATION_FORMAT_VERSION


class TestMigrateFlatToStructured:
    def test_groups_by_page_index(self, annotation_v0_data):
        result = migrate_flat_to_structured(annotation_v0_data, "test.pdf")

        assert result["pdf_path"] == "test.pdf"
        assert result["format_version"] == ANNOTATION_FORMAT_VERSION
        assert len(result["pages"]) == 2

        page0_blocks = result["pages"][0]["blocks"]
        page1_blocks = result["pages"][1]["blocks"]
        assert len(page0_blocks) == 2
        assert len(page1_blocks) == 1

    def test_empty_blocks(self):
        result = migrate_flat_to_structured([], "test.pdf")
        # Пустой массив → 1 пустая страница (page 0)
        assert len(result["pages"]) == 1
        assert result["pages"][0]["blocks"] == []


class TestMigrateAnnotationData:
    def test_v1_to_v2_adds_fields(self, annotation_v1_data):
        migrated, result = migrate_annotation_data(annotation_v1_data)

        assert result.success is True
        assert result.migrated is True
        assert migrated["format_version"] == ANNOTATION_FORMAT_VERSION

        block = migrated["pages"][0]["blocks"][0]
        assert "coords_norm" in block
        assert "source" in block
        assert "shape_type" in block
        assert "created_at" in block
        assert block["source"] == "user"
        assert block["shape_type"] == "rectangle"

    def test_v2_no_migration(self, annotation_v2_data):
        migrated, result = migrate_annotation_data(annotation_v2_data)

        assert result.success is True
        assert result.migrated is False
        assert migrated is annotation_v2_data  # Не копируется

    def test_coords_norm_calculation(self, annotation_v1_data):
        migrated, _ = migrate_annotation_data(annotation_v1_data)
        block = migrated["pages"][0]["blocks"][0]

        # coords_px = [100, 200, 500, 400], page = 1240x1754
        expected = [100 / 1240, 200 / 1754, 500 / 1240, 400 / 1754]
        for actual, exp in zip(block["coords_norm"], expected):
            assert abs(actual - exp) < 1e-6

    def test_invalid_data_fails(self):
        _, result = migrate_annotation_data({"no_pages": True})
        assert result.success is False


class TestAnnotationIOFileRoundTrip:
    def test_save_and_load(self, tmp_path, annotation_v2_data):
        """Round-trip: save → load → compare."""
        from rd_core.models import Document

        doc, _ = Document.from_dict(annotation_v2_data, migrate_ids=False)

        file_path = str(tmp_path / "test_annotation.json")
        AnnotationIO.save_annotation(doc, file_path)

        loaded_doc, was_migrated = AnnotationIO.load_annotation(file_path, migrate_ids=False)
        assert loaded_doc is not None
        assert len(loaded_doc.pages) == len(doc.pages)
        assert len(loaded_doc.pages[0].blocks) == len(doc.pages[0].blocks)

    def test_load_and_migrate_v1(self, tmp_annotation_file):
        """Загрузка v1 файла с автоматической миграцией."""
        doc, result = AnnotationIO.load_and_migrate(str(tmp_annotation_file))

        assert result.success is True
        assert result.migrated is True
        assert doc is not None
        assert len(doc.pages) == 1
        assert len(doc.pages[0].blocks) == 1

    def test_load_nonexistent_file(self, tmp_path):
        doc, was_migrated = AnnotationIO.load_annotation(
            str(tmp_path / "nonexistent.json")
        )
        assert doc is None
        assert was_migrated is False

    def test_load_invalid_json(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json", encoding="utf-8")

        doc, result = AnnotationIO.load_and_migrate(str(bad_file))
        assert result.success is False
        assert doc is None

    def test_saved_file_has_format_version(self, tmp_path, annotation_v2_data):
        from rd_core.models import Document

        doc, _ = Document.from_dict(annotation_v2_data, migrate_ids=False)
        file_path = str(tmp_path / "test.json")
        AnnotationIO.save_annotation(doc, file_path)

        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["format_version"] == ANNOTATION_FORMAT_VERSION
