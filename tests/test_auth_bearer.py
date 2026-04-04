"""Unit tests for rd_core/ocr/http_utils.py — auth and HTTP client creation."""

from unittest.mock import patch

import httpx
import pytest

from rd_core.ocr.http_utils import create_http_client, get_lmstudio_api_key


# ── get_lmstudio_api_key ───────────────────────────────────────────


class TestGetLmstudioApiKey:
    def test_get_lmstudio_api_key(self, monkeypatch):
        """Reads API key from LMSTUDIO_API_KEY env var."""
        monkeypatch.setenv("LMSTUDIO_API_KEY", "sk-test-key-123")
        assert get_lmstudio_api_key() == "sk-test-key-123"

    def test_get_lmstudio_api_key_empty(self, monkeypatch):
        """Returns None when env var is empty or whitespace."""
        monkeypatch.setenv("LMSTUDIO_API_KEY", "")
        assert get_lmstudio_api_key() is None

        monkeypatch.setenv("LMSTUDIO_API_KEY", "   ")
        assert get_lmstudio_api_key() is None

    def test_get_lmstudio_api_key_missing(self, monkeypatch):
        """Returns None when env var is not set."""
        monkeypatch.delenv("LMSTUDIO_API_KEY", raising=False)
        assert get_lmstudio_api_key() is None


# ── create_http_client ──────────────────────────────────────────────


class TestCreateHttpClient:
    def test_create_http_client_with_api_key(self):
        """Client includes Authorization: Bearer header when api_key is provided."""
        client = create_http_client(api_key="my-secret-token", timeout=30.0)

        try:
            assert isinstance(client, httpx.Client)
            assert "Authorization" in client.headers
            assert client.headers["Authorization"] == "Bearer my-secret-token"
        finally:
            client.close()

    def test_create_http_client_no_key(self):
        """Client omits Authorization header when api_key is None."""
        client = create_http_client(api_key=None, timeout=30.0)

        try:
            assert isinstance(client, httpx.Client)
            assert "Authorization" not in client.headers
        finally:
            client.close()

    def test_create_http_client_no_key_empty_string(self):
        """Client omits Authorization header when api_key is empty string."""
        client = create_http_client(api_key="", timeout=30.0)

        try:
            # Empty string is falsy, so no header should be set
            assert "Authorization" not in client.headers
        finally:
            client.close()

    def test_create_http_client_timeout(self):
        """Client respects the provided timeout."""
        client = create_http_client(api_key=None, timeout=42.0)

        try:
            assert client.timeout.read == 42.0
            assert client.timeout.connect == 10.0
        finally:
            client.close()
