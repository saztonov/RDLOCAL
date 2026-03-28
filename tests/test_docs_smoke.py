"""Smoke-тесты документации: проверка актуальности manifests, engines, references."""
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


class TestManifests:
    """Проверка файлов зависимостей."""

    def test_pyproject_toml_exists(self):
        assert (PROJECT_ROOT / "pyproject.toml").exists()

    def test_no_root_requirements_txt(self):
        """requirements.txt в корне не должен существовать — зависимости в pyproject.toml."""
        assert not (PROJECT_ROOT / "requirements.txt").exists(), \
            "Root requirements.txt не должен существовать. Зависимости в pyproject.toml."

    def test_server_requirements_exists(self):
        assert (PROJECT_ROOT / "services" / "remote_ocr" / "requirements.txt").exists()

    def test_no_requests_dependency(self):
        """requests удалён из зависимостей — используется httpx."""
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "requests" not in pyproject.split("[project.optional-dependencies]")[0], \
            "requests не должен быть в зависимостях pyproject.toml (используется httpx)"


class TestOutdatedReferences:
    """Проверка что устаревшие ссылки удалены из документации."""

    @pytest.fixture
    def docs_content(self):
        """Собрать весь текст из docs/ + CLAUDE.md."""
        texts = {}
        for md_file in (PROJECT_ROOT / "docs").glob("*.md"):
            texts[md_file.name] = md_file.read_text(encoding="utf-8")
        claude_md = PROJECT_ROOT / "CLAUDE.md"
        if claude_md.exists():
            texts["CLAUDE.md"] = claude_md.read_text(encoding="utf-8")
        return texts

    def test_no_openrouter_in_active_docs(self, docs_content):
        """OpenRouter не должен упоминаться как активный бэкенд."""
        for name, content in docs_content.items():
            # Допускаем упоминание в комментариях/deprecated секциях
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                line_lower = line.lower()
                if "openrouter" in line_lower:
                    # Допустимо в контексте "deprecated", "removed", "legacy", комментарий
                    context = line_lower + (lines[i] if i < len(lines) else "")
                    assert any(w in context for w in ("deprecated", "removed", "legacy", "удалён", "#")), \
                        f"{name}:{i} содержит активную ссылку на OpenRouter: {line.strip()}"

    def test_no_datalab_in_active_docs(self, docs_content):
        """Datalab не должен упоминаться как активный бэкенд."""
        for name, content in docs_content.items():
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                line_lower = line.lower()
                if "datalab" in line_lower and "datalab" not in ("deprecated", "removed", "legacy"):
                    context = line_lower + (lines[i] if i < len(lines) else "")
                    assert any(w in context for w in ("deprecated", "removed", "legacy", "удалён", "#")), \
                        f"{name}:{i} содержит активную ссылку на Datalab: {line.strip()}"


class TestSupportedEngines:
    """Проверка что backend_factory поддерживает только актуальные движки."""

    def test_valid_engines_lmstudio_only(self):
        """backend_factory должен поддерживать только lmstudio/chandra."""
        factory_path = PROJECT_ROOT / "services" / "remote_ocr" / "server" / "backend_factory.py"
        content = factory_path.read_text(encoding="utf-8")
        match = re.search(r'_VALID_ENGINES\s*=\s*\{([^}]+)\}', content)
        assert match, "Не найден _VALID_ENGINES в backend_factory.py"
        engines = {e.strip().strip('"').strip("'") for e in match.group(1).split(",")}
        assert engines == {"lmstudio", "chandra"}, f"Неожиданные engines: {engines}"

    def test_env_example_no_cloud_keys(self):
        """В .env.example не должно быть OPENROUTER/DATALAB ключей."""
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        assert "OPENROUTER" not in env_example
        assert "DATALAB" not in env_example
