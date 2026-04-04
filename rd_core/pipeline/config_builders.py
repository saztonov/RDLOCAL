"""
Builders for OCR backend model_config dicts.

Each function accepts a config source (dict or object with attributes)
and returns a model_config dict suitable for create_ocr_engine().

This decouples config building from the server's settings singleton.
"""
from __future__ import annotations

from typing import Any


def _g(cfg: Any, key: str) -> Any:
    """Get value from cfg: supports both dict-style and attribute-style access."""
    try:
        return cfg[key]
    except (TypeError, KeyError):
        return getattr(cfg, key)


def build_chandra_config(cfg: Any) -> dict:
    """Собрать model_config для ChandraBackend из config."""
    return {
        "model_key": _g(cfg, "chandra_model_key"),
        "context_length": _g(cfg, "chandra_context_length"),
        "flash_attention": _g(cfg, "chandra_flash_attention"),
        "eval_batch_size": _g(cfg, "chandra_eval_batch_size"),
        "offload_kv_cache": _g(cfg, "chandra_offload_kv_cache"),
        "max_image_size": _g(cfg, "chandra_max_image_size"),
        "preload_timeout": _g(cfg, "chandra_preload_timeout"),
        "max_retries": _g(cfg, "chandra_max_retries"),
        "retry_delays": _g(cfg, "chandra_retry_delays"),
        "system_prompt": _g(cfg, "chandra_system_prompt"),
        "user_prompt": _g(cfg, "chandra_user_prompt"),
        "max_tokens": _g(cfg, "chandra_max_tokens"),
        "temperature": _g(cfg, "chandra_temperature"),
        "top_p": _g(cfg, "chandra_top_p"),
        "top_k": _g(cfg, "chandra_top_k"),
        "repetition_penalty": _g(cfg, "chandra_repetition_penalty"),
        "min_p": _g(cfg, "chandra_min_p"),
        "length_retry_attempts": _g(cfg, "chandra_length_retry_attempts"),
        "length_retry_max_tokens": _g(cfg, "chandra_length_retry_max_tokens"),
    }


def build_qwen_config(cfg: Any) -> dict:
    """Собрать model_config для QwenBackend из config."""
    return {
        "model_key": _g(cfg, "qwen_model_key"),
        "context_length": _g(cfg, "qwen_context_length"),
        "flash_attention": _g(cfg, "qwen_flash_attention"),
        "eval_batch_size": _g(cfg, "qwen_eval_batch_size"),
        "offload_kv_cache": _g(cfg, "qwen_offload_kv_cache"),
        "max_image_size": _g(cfg, "qwen_max_image_size"),
        "preload_timeout": _g(cfg, "qwen_preload_timeout"),
        "max_retries": _g(cfg, "qwen_max_retries"),
        "retry_delays": _g(cfg, "qwen_retry_delays"),
        "default_system_prompt": _g(cfg, "qwen_default_system_prompt"),
        "default_user_prompt": _g(cfg, "qwen_default_user_prompt"),
        "max_tokens": _g(cfg, "qwen_max_tokens"),
        "temperature": _g(cfg, "qwen_temperature"),
        "top_p": _g(cfg, "qwen_top_p"),
        "top_k": _g(cfg, "qwen_top_k"),
        "repetition_penalty": _g(cfg, "qwen_repetition_penalty"),
        "min_p": _g(cfg, "qwen_min_p"),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "image_ocr_output",
                "schema": {
                    "type": "object",
                    "properties": {
                        "fragment_type": {"type": ["string", "null"]},
                        "location": {"type": ["object", "null"]},
                        "content_summary": {"type": ["string", "null"]},
                        "detailed_description": {"type": ["string", "null"]},
                        "verification_recommendations": {"type": ["string", "null"]},
                        "key_entities": {"type": ["array", "null"]},
                    },
                    "required": ["fragment_type", "content_summary", "detailed_description"],
                },
            },
        },
    }


def build_stamp_config(cfg: Any) -> dict:
    """Собрать model_config для Stamp QwenBackend из config."""
    return {
        "model_key": _g(cfg, "stamp_model_key"),
        "context_length": _g(cfg, "stamp_context_length"),
        "flash_attention": _g(cfg, "stamp_flash_attention"),
        "eval_batch_size": _g(cfg, "stamp_eval_batch_size"),
        "offload_kv_cache": _g(cfg, "stamp_offload_kv_cache"),
        "max_image_size": _g(cfg, "stamp_max_image_size"),
        "preload_timeout": _g(cfg, "stamp_preload_timeout"),
        "max_retries": _g(cfg, "stamp_max_retries"),
        "retry_delays": _g(cfg, "stamp_retry_delays"),
        "default_system_prompt": _g(cfg, "stamp_system_prompt"),
        "default_user_prompt": _g(cfg, "stamp_user_prompt"),
        "max_tokens": _g(cfg, "stamp_max_tokens"),
        "temperature": _g(cfg, "stamp_temperature"),
        "top_p": _g(cfg, "stamp_top_p"),
        "top_k": _g(cfg, "stamp_top_k"),
        "repetition_penalty": _g(cfg, "stamp_repetition_penalty"),
        "min_p": _g(cfg, "stamp_min_p"),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "stamp_output",
                "schema": {
                    "type": "object",
                    "properties": {
                        "document_code": {"type": ["string", "null"]},
                        "project_name": {"type": ["string", "null"]},
                        "sheet_name": {"type": ["string", "null"]},
                        "stage": {"type": ["string", "null"]},
                        "sheet_number": {"type": ["string", "null"]},
                        "total_sheets": {"type": ["string", "null"]},
                        "organization": {"type": ["string", "null"]},
                        "signatures": {"type": "array"},
                        "revisions": {"type": "array"},
                    },
                    "required": ["document_code", "project_name", "sheet_name"],
                },
            },
        },
    }
