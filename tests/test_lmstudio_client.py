"""Unit tests for rd_core/ocr/lmstudio_client.py — LM Studio OCR client."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from rd_core.ocr.lmstudio_client import (
    LMStudioAuthError,
    LMStudioClient,
    OcrResult,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def _clean_env(monkeypatch):
    """Remove all LMSTUDIO_* env vars so tests start from a clean slate."""
    for var in (
        "LMSTUDIO_BASE_URL",
        "LMSTUDIO_API_KEY",
        "LMSTUDIO_TIMEOUT_MS",
        "LMSTUDIO_MAX_RETRIES",
        "LMSTUDIO_VISION_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def mock_client():
    """Return a MagicMock standing in for httpx.Client."""
    return MagicMock(spec=httpx.Client)


@pytest.fixture
def make_client(_clean_env, mock_client):
    """Factory that creates an LMStudioClient with an injected mock httpx.Client."""

    def _factory(**kwargs):
        defaults = dict(
            base_url="http://test:1234",
            api_key="test-key",
            timeout_ms=5000,
            max_retries=2,
        )
        defaults.update(kwargs)
        with patch(
            "rd_core.ocr.lmstudio_client.create_http_client",
            return_value=mock_client,
        ):
            return LMStudioClient(**defaults)

    return _factory


# ── Health check ────────────────────────────────────────────────────


class TestHealthCheck:
    def test_health_check_ok(self, make_client, mock_client):
        """GET /v1/models 200 -> True."""
        client = make_client()
        resp = MagicMock()
        resp.status_code = 200
        mock_client.get.return_value = resp

        assert client.health_check() is True
        mock_client.get.assert_called_once_with(
            "http://test:1234/v1/models", timeout=15
        )

    def test_health_check_fail(self, make_client, mock_client):
        """ConnectError -> False."""
        client = make_client()
        mock_client.get.side_effect = httpx.ConnectError("refused")

        assert client.health_check() is False


# ── List models ─────────────────────────────────────────────────────


class TestListModels:
    def test_list_models(self, make_client, mock_client):
        """Parses data[] from response JSON."""
        client = make_client()
        models_data = [
            {"id": "model-a", "type": "llm"},
            {"id": "model-b", "type": "embedding"},
        ]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": models_data}
        resp.raise_for_status = MagicMock()
        mock_client.get.return_value = resp

        result = client.list_models()

        assert result == models_data
        assert len(result) == 2
        assert result[0]["id"] == "model-a"


# ── Select vision model ────────────────────────────────────────────


class TestSelectVisionModel:
    def test_select_vision_model_from_env(self, make_client, monkeypatch):
        """LMSTUDIO_VISION_MODEL env takes priority."""
        monkeypatch.setenv("LMSTUDIO_VISION_MODEL", "env-vision-model")
        client = make_client()

        result = client.select_vision_model()

        assert result == "env-vision-model"

    def test_select_vision_model_auto(self, make_client, mock_client):
        """Auto-selects first model with type=llm and capabilities.vision=True."""
        client = make_client()
        models = [
            {"id": "text-only", "type": "llm", "capabilities": {"vision": False}},
            {"id": "vision-model", "type": "llm", "capabilities": {"vision": True}},
            {"id": "embed", "type": "embedding", "capabilities": {}},
        ]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": models}
        resp.raise_for_status = MagicMock()
        mock_client.get.return_value = resp

        result = client.select_vision_model()

        assert result == "vision-model"

    def test_select_vision_model_fallback(self, make_client, mock_client):
        """No vision model -> falls back to first model."""
        client = make_client()
        models = [
            {"id": "first-model", "type": "llm", "capabilities": {"vision": False}},
            {"id": "second-model", "type": "embedding", "capabilities": {}},
        ]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": models}
        resp.raise_for_status = MagicMock()
        mock_client.get.return_value = resp

        result = client.select_vision_model()

        assert result == "first-model"


# ── Bearer auth ─────────────────────────────────────────────────────


class TestBearerAuthHeader:
    def test_bearer_auth_header(self, _clean_env):
        """Verify Authorization: Bearer header is passed to create_http_client."""
        with patch(
            "rd_core.ocr.lmstudio_client.create_http_client"
        ) as mock_create:
            mock_create.return_value = MagicMock(spec=httpx.Client)
            LMStudioClient(
                base_url="http://test:1234",
                api_key="my-secret-key",
                timeout_ms=5000,
            )
            mock_create.assert_called_once_with(
                api_key="my-secret-key",
                timeout=5.0,
            )


# ── Retry behaviour ────────────────────────────────────────────────


class TestRetry:
    def test_retry_on_502(self, make_client, mock_client):
        """502 triggers retry with backoff; all attempts exhausted."""
        client = make_client(max_retries=2)

        resp_502 = MagicMock()
        resp_502.status_code = 502
        mock_client.post.return_value = resp_502

        with patch("rd_core.ocr.lmstudio_client.time.sleep") as mock_sleep:
            raw, warnings = client._request_with_retry(
                {"model": "m", "messages": []}, file_name="test.png", model_id="m"
            )

        assert raw == ""
        assert any("502" in w for w in warnings)
        # 2 retries -> 2 sleep calls (after attempt 1 and 2, not after last)
        assert mock_sleep.call_count == 2
        # Exponential backoff: 2^1=2, 2^2=4
        mock_sleep.assert_any_call(2)
        mock_sleep.assert_any_call(4)
        # Total calls: initial + 2 retries = 3
        assert mock_client.post.call_count == 3

    def test_no_retry_on_401(self, make_client, mock_client):
        """401 raises LMStudioAuthError immediately, no retry."""
        client = make_client(max_retries=3)

        resp_401 = MagicMock()
        resp_401.status_code = 401
        mock_client.post.return_value = resp_401

        with pytest.raises(LMStudioAuthError, match="401"):
            client._request_with_retry(
                {"model": "m", "messages": []}, file_name="test.png", model_id="m"
            )

        # Only one attempt, no retry
        assert mock_client.post.call_count == 1


# ── OCR image ───────────────────────────────────────────────────────


class TestOcrImage:
    def test_ocr_image(self, make_client, mock_client):
        """Successful OCR returns OcrResult with extracted text."""
        client = make_client()

        # Mock select_vision_model
        with patch.object(client, "_get_vision_model", return_value="test-vision"):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [
                    {
                        "message": {"content": "Extracted text from image"},
                        "finish_reason": "stop",
                    }
                ]
            }
            resp.raise_for_status = MagicMock()
            mock_client.post.return_value = resp

            result = client.ocr_image(
                image_buffer=b"\x89PNG\r\n",
                mime_type="image/png",
                file_name="page1.png",
            )

        assert isinstance(result, OcrResult)
        assert result.raw_text == "Extracted text from image"
        assert result.model == "test-vision"
        assert result.provider == "lmstudio"
        assert result.file_name == "page1.png"
        assert result.mime_type == "image/png"
        assert result.duration_ms > 0

        # Verify the POST payload structure
        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload["model"] == "test-vision"
        assert len(payload["messages"]) == 2
        assert payload["messages"][1]["content"][1]["type"] == "image_url"


# ── Timeout from env ───────────────────────────────────────────────


class TestTimeoutFromEnv:
    def test_timeout_from_env(self, _clean_env, monkeypatch):
        """LMSTUDIO_TIMEOUT_MS env is read and converted to seconds."""
        monkeypatch.setenv("LMSTUDIO_TIMEOUT_MS", "60000")

        with patch(
            "rd_core.ocr.lmstudio_client.create_http_client"
        ) as mock_create:
            mock_create.return_value = MagicMock(spec=httpx.Client)
            client = LMStudioClient(base_url="http://test:1234", api_key="k")

        assert client._timeout_s == 60.0
        mock_create.assert_called_once_with(api_key="k", timeout=60.0)
