"""Smoke-тесты документации: актуальность входных точек, env и ссылок."""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"


def _load_active_docs() -> dict[str, str]:
    texts: dict[str, str] = {}
    texts["README.md"] = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    for md_file in sorted(DOCS_DIR.glob("*.md")):
        texts[f"docs/{md_file.name}"] = md_file.read_text(encoding="utf-8")

    claude_md = PROJECT_ROOT / "CLAUDE.md"
    if claude_md.exists():
        texts["CLAUDE.md"] = claude_md.read_text(encoding="utf-8")

    return texts


class TestManifests:
    """Проверка основных файлов зависимостей и env."""

    def test_pyproject_toml_exists(self):
        assert (PROJECT_ROOT / "pyproject.toml").exists()

    def test_no_root_requirements_txt(self):
        assert not (PROJECT_ROOT / "requirements.txt").exists(), (
            "Root requirements.txt не должен существовать. "
            "Зависимости живут в pyproject.toml."
        )

    def test_server_requirements_exists(self):
        assert (PROJECT_ROOT / "services" / "remote_ocr" / "requirements.txt").exists()

    def test_env_example_exists(self):
        assert (PROJECT_ROOT / ".env.example").exists()

    def test_no_requests_dependency(self):
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        main_deps = pyproject.split("[project.optional-dependencies]")[0]
        assert "requests" not in main_deps, (
            "requests не должен быть в основных зависимостях: используется httpx"
        )


class TestDocsSet:
    """Проверка актуального набора документов."""

    def test_docs_index_exists(self):
        assert (DOCS_DIR / "README.md").exists()

    def test_developer_guide_removed(self):
        assert not (DOCS_DIR / "DEVELOPER_GUIDE.md").exists()


class TestOutdatedReferences:
    """Проверка, что в актуальной документации нет устаревших ссылок."""

    @pytest.fixture
    def docs_content(self):
        return _load_active_docs()

    def test_root_readme_included(self, docs_content):
        assert "README.md" in docs_content

    def test_no_openrouter_in_active_docs(self, docs_content):
        for name, content in docs_content.items():
            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                line_lower = line.lower()
                if "openrouter" in line_lower:
                    context = line_lower
                    if i < len(lines):
                        context += " " + lines[i].lower()
                    assert any(
                        word in context
                        for word in ("deprecated", "removed", "legacy", "удал", "#")
                    ), f"{name}:{i} содержит активную ссылку на OpenRouter: {line.strip()}"

    def test_no_datalab_in_active_docs(self, docs_content):
        for name, content in docs_content.items():
            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                line_lower = line.lower()
                if "datalab" in line_lower:
                    context = line_lower
                    if i < len(lines):
                        context += " " + lines[i].lower()
                    assert any(
                        word in context
                        for word in ("deprecated", "removed", "legacy", "удал", "#")
                    ), f"{name}:{i} содержит активную ссылку на Datalab: {line.strip()}"

    def test_no_stale_file_references(self, docs_content):
        stale_tokens = {
            "remote_ocr_client.py",
            "tree_client.py",
            "PDFAnnotation.exe",
            "PDFAnnotationTool.spec",
            "test_remote_ocr_client.py",
            "test_tree_client.py",
        }
        for name, content in docs_content.items():
            for token in stale_tokens:
                assert token not in content, f"{name} содержит устаревшую ссылку: {token}"


class TestSupportedEngines:
    """Проверка, что backend_factory поддерживает только актуальные движки."""

    def test_valid_engines_lmstudio_only(self):
        factory_path = PROJECT_ROOT / "services" / "remote_ocr" / "server" / "backend_factory.py"
        content = factory_path.read_text(encoding="utf-8")
        match = re.search(r'_VALID_ENGINES\s*=\s*\{([^}]+)\}', content)
        assert match, "Не найден _VALID_ENGINES в backend_factory.py"
        engines = {e.strip().strip('"').strip("'") for e in match.group(1).split(",")}
        assert engines == {"lmstudio", "chandra"}, f"Неожиданные engines: {engines}"

    def test_env_example_no_cloud_keys(self):
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        assert "OPENROUTER" not in env_example
        assert "DATALAB" not in env_example
