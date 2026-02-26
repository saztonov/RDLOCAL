"""Обратная совместимость: re-export из lmstudio_lifecycle.py"""
from .lmstudio_lifecycle import (  # noqa: F401
    acquire_chandra,
    acquire_lmstudio,
    release_chandra,
    release_lmstudio,
)
