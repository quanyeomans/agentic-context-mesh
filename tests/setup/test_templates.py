"""Tests for ontology templates — verify each loads and produces valid config."""

from pathlib import Path

import pytest
import yaml

TEMPLATE_DIR = Path(__file__).parent.parent.parent / "kairix" / "setup" / "templates"


@pytest.mark.unit
class TestOntologyTemplates:
    def test_consulting_template_loads(self) -> None:
        path = TEMPLATE_DIR / "consulting.yaml"
        assert path.exists()
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["name"] == "consulting"
        assert data["retrieval"]["fusion_strategy"] == "bm25_primary"
        assert data["retrieval"]["boosts"]["entity"]["enabled"] is True

    def test_technical_template_loads(self) -> None:
        path = TEMPLATE_DIR / "technical.yaml"
        assert path.exists()
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["name"] == "technical"
        assert data["retrieval"]["boosts"]["entity"]["enabled"] is False
        assert data["retrieval"]["boosts"]["procedural"]["factor"] == 1.5

    def test_general_template_loads(self) -> None:
        path = TEMPLATE_DIR / "general.yaml"
        assert path.exists()
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["name"] == "general"
        assert data["retrieval"]["boosts"]["entity"]["factor"] == 0.15

    def test_all_templates_have_fusion_strategy(self) -> None:
        for template_file in TEMPLATE_DIR.glob("*.yaml"):
            with open(template_file) as f:
                data = yaml.safe_load(f)
            assert "retrieval" in data, f"{template_file.name} missing 'retrieval'"
            assert "fusion_strategy" in data["retrieval"], f"{template_file.name} missing fusion_strategy"

    def test_all_templates_use_bm25_primary(self) -> None:
        for template_file in TEMPLATE_DIR.glob("*.yaml"):
            with open(template_file) as f:
                data = yaml.safe_load(f)
            assert data["retrieval"]["fusion_strategy"] == "bm25_primary"
