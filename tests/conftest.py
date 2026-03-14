"""Общие фикстуры для тестов."""

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def annotation_v0_data():
    """Плоский формат v0 — массив блоков без обёртки."""
    return [
        {
            "id": "test-block-1",
            "page_index": 0,
            "coords_px": [100, 200, 500, 400],
            "block_type": "text",
        },
        {
            "id": "test-block-2",
            "page_index": 0,
            "coords_px": [100, 500, 500, 700],
            "block_type": "image",
        },
        {
            "id": "test-block-3",
            "page_index": 1,
            "coords_px": [50, 50, 300, 200],
            "block_type": "text",
        },
    ]


@pytest.fixture
def annotation_v1_data():
    """Структурированный формат v1 — без coords_norm, source."""
    return {
        "pdf_path": "test.pdf",
        "pages": [
            {
                "page_number": 0,
                "width": 1240,
                "height": 1754,
                "blocks": [
                    {
                        "id": "ACDE-FGHJ-KLM",
                        "page_index": 0,
                        "coords_px": [100, 200, 500, 400],
                        "block_type": "text",
                    },
                ],
            },
        ],
    }


@pytest.fixture
def annotation_v2_data():
    """Текущий формат v2 — полные поля."""
    return {
        "pdf_path": "test.pdf",
        "format_version": 2,
        "pages": [
            {
                "page_number": 0,
                "width": 1240,
                "height": 1754,
                "blocks": [
                    {
                        "id": "ACDE-FGHJ-KLM",
                        "page_index": 0,
                        "coords_px": [100, 200, 500, 400],
                        "coords_norm": [
                            100 / 1240,
                            200 / 1754,
                            500 / 1240,
                            400 / 1754,
                        ],
                        "block_type": "text",
                        "source": "user",
                        "shape_type": "rectangle",
                        "created_at": "2026-01-01 12:00:00",
                    },
                ],
            },
        ],
    }


@pytest.fixture
def tmp_annotation_file(tmp_path, annotation_v1_data):
    """Временный JSON-файл с аннотацией v1."""
    path = tmp_path / "test_annotation.json"
    path.write_text(json.dumps(annotation_v1_data), encoding="utf-8")
    return path
