"""Tests for setup wizard — config generation and template loading."""

from pathlib import Path

import pytest
import yaml


@pytest.mark.unit
def test_load_template_consulting() -> None:
    from kairix.setup.wizard import _load_template

    template = _load_template("consulting")
    assert template["name"] == "consulting"
    assert "retrieval" in template


@pytest.mark.unit
def test_load_template_missing_returns_empty() -> None:
    from kairix.setup.wizard import _load_template

    template = _load_template("nonexistent")
    assert template == {}


@pytest.mark.unit
def test_count_documents(tmp_path: Path) -> None:
    from kairix.setup.wizard import _count_documents

    # Create some test files
    (tmp_path / "doc1.md").write_text("hello")
    (tmp_path / "doc2.md").write_text("world " * 100)
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "doc3.md").write_text("nested")
    (tmp_path / "not-markdown.txt").write_text("ignored")

    count, size = _count_documents(str(tmp_path))
    assert count == 3  # .md files only
    assert size > 0


@pytest.mark.unit
def test_count_documents_empty_dir(tmp_path: Path) -> None:
    from kairix.setup.wizard import _count_documents

    count, size = _count_documents(str(tmp_path))
    assert count == 0
    assert size == 0.0


@pytest.mark.unit
def test_count_documents_nonexistent_path() -> None:
    from kairix.setup.wizard import _count_documents

    count, size = _count_documents("/nonexistent/path")
    assert count == 0
    assert size == 0.0


@pytest.mark.unit
def test_docker_compose_valid_yaml() -> None:
    """Verify docker-compose.yml is valid YAML."""
    compose_path = Path(__file__).parent.parent.parent / "docker-compose.yml"
    if compose_path.exists():
        with open(compose_path) as f:
            data = yaml.safe_load(f)
        assert "services" in data
        assert "kairix" in data["services"]
        assert "neo4j" in data["services"]
