"""Чтение конфигурации OCR из YAML (services/remote_ocr/server/config.yaml)"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "services"
    / "remote_ocr"
    / "server"
    / "config.yaml"
)

_cached_config: dict | None = None


def _load_config() -> dict:
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    try:
        import yaml

        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _cached_config = yaml.safe_load(f) or {}
    except ImportError:
        logger.warning("PyYAML not installed, using fallback defaults")
        _cached_config = {}
    except FileNotFoundError:
        logger.warning(f"Config not found: {_CONFIG_PATH}, using fallback defaults")
        _cached_config = {}
    except Exception as e:
        logger.warning(f"Failed to load OCR config: {e}")
        _cached_config = {}

    return _cached_config


def get_ocr_defaults() -> Dict[str, str]:
    """Получить дефолтные модели и движок из YAML конфига"""
    config = _load_config()
    return {
        "image_model": config.get(
            "default_image_model", "google/gemini-3.1-flash-lite-preview"
        ),
        "stamp_model": config.get(
            "default_stamp_model", "qwen/qwen3.5-9b"
        ),
        "engine": config.get("default_engine", "datalab"),
    }
