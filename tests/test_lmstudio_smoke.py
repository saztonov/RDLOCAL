"""Integration smoke test для LM Studio через reverse proxy.

Требует доступ к LMSTUDIO_BASE_URL (https://llm.fvds.ru).
Запуск: pytest tests/test_lmstudio_smoke.py -v -m integration
"""
import os

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    """Создать LMStudioClient с реальным подключением."""
    base_url = os.getenv("LMSTUDIO_BASE_URL", "").strip()
    if not base_url:
        pytest.skip("LMSTUDIO_BASE_URL not set")

    from rd_core.ocr.lmstudio_client import LMStudioClient

    c = LMStudioClient()
    yield c
    c.close()


def test_health_check(client):
    """GET /v1/models -> 200."""
    assert client.health_check() is True


def test_list_models(client):
    """GET /v1/models возвращает список моделей."""
    models = client.list_models()
    assert isinstance(models, list)
    assert len(models) > 0
    assert "id" in models[0]


def test_select_vision_model(client):
    """Vision model выбирается (auto или из env)."""
    model = client.select_vision_model()
    assert model is not None
    assert isinstance(model, str)
    assert len(model) > 0


def test_ocr_test_image(client):
    """Отправить тестовое изображение на OCR."""
    # Минимальный PNG 1x1 пиксель
    import base64

    # 1x1 белый PNG
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
        "2mP8/58BAwAI/AL+hc2rNAAAAABJRU5ErkJggg=="
    )
    image_buffer = base64.b64decode(png_b64)

    result = client.ocr_image(
        image_buffer=image_buffer,
        mime_type="image/png",
        file_name="test_1x1.png",
    )

    assert result.provider == "lmstudio"
    assert result.model != ""
    assert result.duration_ms > 0
    # Модель должна вернуть хоть что-то (пустое изображение → пустой текст или описание)
    assert isinstance(result.raw_text, str)
