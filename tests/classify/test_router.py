"""
Tests for the write router (mnemosyne/classify/router.py).

Tests path resolution for all classification types and agent scoping.
"""

from __future__ import annotations

from datetime import date

import pytest

from kairix.classify.router import VALID_AGENTS, resolve_target_path


@pytest.mark.unit
class TestEpisodicRouting:
    @pytest.mark.unit
    def test_episodic_builder_default_date(self):
        today = date.today().isoformat()
        path = resolve_target_path("builder", "episodic")
        assert path == f"/data/workspaces/builder/memory/{today}.md"

    @pytest.mark.unit
    def test_episodic_shape_default_date(self):
        today = date.today().isoformat()
        path = resolve_target_path("shape", "episodic")
        assert path == f"/data/workspaces/shape/memory/{today}.md"

    @pytest.mark.unit
    def test_episodic_custom_date(self):
        path = resolve_target_path("builder", "episodic", date="2026-03-23")
        assert path == "/data/workspaces/builder/memory/2026-03-23.md"

    @pytest.mark.unit
    def test_episodic_growth(self):
        today = date.today().isoformat()
        path = resolve_target_path("growth", "episodic")
        assert path.endswith(f"/memory/{today}.md")
        assert "growth" in path

    @pytest.mark.unit
    def test_episodic_consultant(self):
        today = date.today().isoformat()
        path = resolve_target_path("consultant", "episodic")
        assert path.endswith(f"/memory/{today}.md")
        assert "consultant" in path


@pytest.mark.unit
class TestProceduralRuleRouting:
    @pytest.mark.unit
    def test_builder_rules(self):
        path = resolve_target_path("builder", "procedural-rule")
        assert path == "/data/obsidian-vault/04-Agent-Knowledge/builder/rules.md"

    @pytest.mark.unit
    def test_shape_rules(self):
        path = resolve_target_path("shape", "procedural-rule")
        assert path == "/data/obsidian-vault/04-Agent-Knowledge/shape/rules.md"

    @pytest.mark.unit
    def test_shared_rules(self):
        path = resolve_target_path("shared", "procedural-rule")
        assert "shared" in path
        assert "rules.md" in path

    @pytest.mark.unit
    def test_growth_rules(self):
        path = resolve_target_path("growth", "procedural-rule")
        assert "growth" in path
        assert "rules.md" in path


@pytest.mark.unit
class TestProceduralPatternRouting:
    @pytest.mark.unit
    def test_builder_patterns(self):
        path = resolve_target_path("builder", "procedural-pattern")
        assert path == "/data/obsidian-vault/04-Agent-Knowledge/builder/patterns.md"

    @pytest.mark.unit
    def test_shape_patterns(self):
        path = resolve_target_path("shape", "procedural-pattern")
        assert path == "/data/obsidian-vault/04-Agent-Knowledge/shape/patterns.md"

    @pytest.mark.unit
    def test_shared_patterns(self):
        path = resolve_target_path("shared", "procedural-pattern")
        assert "shared" in path
        assert "patterns.md" in path


@pytest.mark.unit
class TestSemanticDecisionRouting:
    @pytest.mark.unit
    def test_builder_decisions(self):
        path = resolve_target_path("builder", "semantic-decision")
        assert path == "/data/obsidian-vault/04-Agent-Knowledge/builder/decisions.md"

    @pytest.mark.unit
    def test_shape_decisions(self):
        path = resolve_target_path("shape", "semantic-decision")
        assert path == "/data/obsidian-vault/04-Agent-Knowledge/shape/decisions.md"

    @pytest.mark.unit
    def test_shared_decisions(self):
        path = resolve_target_path("shared", "semantic-decision")
        assert "shared" in path
        assert "decisions.md" in path


@pytest.mark.unit
class TestSemanticFactRouting:
    @pytest.mark.unit
    def test_builder_facts(self):
        path = resolve_target_path("builder", "semantic-fact")
        assert path == "/data/obsidian-vault/04-Agent-Knowledge/builder/facts.md"

    @pytest.mark.unit
    def test_shape_facts(self):
        path = resolve_target_path("shape", "semantic-fact")
        assert path == "/data/obsidian-vault/04-Agent-Knowledge/shape/facts.md"

    @pytest.mark.unit
    def test_shared_facts(self):
        path = resolve_target_path("shared", "semantic-fact")
        assert "shared" in path
        assert "facts.md" in path


@pytest.mark.unit
class TestEntityRouting:
    @pytest.mark.unit
    def test_entity_person(self):
        path = resolve_target_path("builder", "entity", entity_type="person", entity_slug="jordan-blake")
        assert path == "/data/obsidian-vault/04-Agent-Knowledge/entities/person/jordan-blake.md"

    @pytest.mark.unit
    def test_entity_organisation(self):
        path = resolve_target_path("builder", "entity", entity_type="organisation", entity_slug="triad-consulting")
        assert "entities/organisation/triad-consulting.md" in path

    @pytest.mark.unit
    def test_entity_defaults(self):
        path = resolve_target_path("builder", "entity")
        assert "entities/unknown/unknown.md" in path


@pytest.mark.unit
class TestAgentScoping:
    @pytest.mark.unit
    def test_all_valid_agents(self):
        for agent in VALID_AGENTS:
            path = resolve_target_path(agent, "procedural-rule")
            assert agent in path

    @pytest.mark.unit
    def test_invalid_agent_raises(self):
        with pytest.raises(ValueError, match="Invalid agent"):
            resolve_target_path("admin", "procedural-rule")

    @pytest.mark.unit
    def test_another_agent_path(self):
        with pytest.raises(ValueError, match="Invalid agent"):
            resolve_target_path("notanagent", "episodic")

    @pytest.mark.unit
    def test_shared_is_valid(self):
        path = resolve_target_path("shared", "procedural-rule")
        assert path  # should not raise

    @pytest.mark.unit
    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown classification type"):
            resolve_target_path("builder", "nonexistent-type")

    @pytest.mark.unit
    def test_agent_isolation_builder_not_in_shape_path(self):
        """builder paths should not contain 'shape' and vice versa."""
        builder_path = resolve_target_path("builder", "procedural-rule")
        shape_path = resolve_target_path("shape", "procedural-rule")
        assert "shape" not in builder_path
        assert "builder" not in shape_path
