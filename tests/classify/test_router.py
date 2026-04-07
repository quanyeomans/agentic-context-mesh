"""
Tests for the write router (mnemosyne/classify/router.py).

Tests path resolution for all classification types and agent scoping.
"""

from __future__ import annotations

from datetime import date

import pytest

from mnemosyne.classify.router import VALID_AGENTS, resolve_target_path


class TestEpisodicRouting:
    def test_episodic_builder_default_date(self):
        today = date.today().isoformat()
        path = resolve_target_path("builder", "episodic")
        assert path == f"/data/workspaces/builder/memory/{today}.md"

    def test_episodic_shape_default_date(self):
        today = date.today().isoformat()
        path = resolve_target_path("shape", "episodic")
        assert path == f"/data/workspaces/shape/memory/{today}.md"

    def test_episodic_custom_date(self):
        path = resolve_target_path("builder", "episodic", date="2026-03-23")
        assert path == "/data/workspaces/builder/memory/2026-03-23.md"

    def test_episodic_growth(self):
        today = date.today().isoformat()
        path = resolve_target_path("growth", "episodic")
        assert path.endswith(f"/memory/{today}.md")
        assert "growth" in path

    def test_episodic_consultant(self):
        today = date.today().isoformat()
        path = resolve_target_path("consultant", "episodic")
        assert path.endswith(f"/memory/{today}.md")
        assert "consultant" in path


class TestProceduralRuleRouting:
    def test_builder_rules(self):
        path = resolve_target_path("builder", "procedural-rule")
        assert path == "/vault/agent-knowledge/builder/rules.md"

    def test_shape_rules(self):
        path = resolve_target_path("shape", "procedural-rule")
        assert path == "/vault/agent-knowledge/shape/rules.md"

    def test_shared_rules(self):
        path = resolve_target_path("shared", "procedural-rule")
        assert "shared" in path
        assert "rules.md" in path

    def test_growth_rules(self):
        path = resolve_target_path("growth", "procedural-rule")
        assert "growth" in path
        assert "rules.md" in path


class TestProceduralPatternRouting:
    def test_builder_patterns(self):
        path = resolve_target_path("builder", "procedural-pattern")
        assert path == "/vault/agent-knowledge/builder/patterns.md"

    def test_shape_patterns(self):
        path = resolve_target_path("shape", "procedural-pattern")
        assert path == "/vault/agent-knowledge/shape/patterns.md"

    def test_shared_patterns(self):
        path = resolve_target_path("shared", "procedural-pattern")
        assert "shared" in path
        assert "patterns.md" in path


class TestSemanticDecisionRouting:
    def test_builder_decisions(self):
        path = resolve_target_path("builder", "semantic-decision")
        assert path == "/vault/agent-knowledge/builder/decisions.md"

    def test_shape_decisions(self):
        path = resolve_target_path("shape", "semantic-decision")
        assert path == "/vault/agent-knowledge/shape/decisions.md"

    def test_shared_decisions(self):
        path = resolve_target_path("shared", "semantic-decision")
        assert "shared" in path
        assert "decisions.md" in path


class TestSemanticFactRouting:
    def test_builder_facts(self):
        path = resolve_target_path("builder", "semantic-fact")
        assert path == "/vault/agent-knowledge/builder/facts.md"

    def test_shape_facts(self):
        path = resolve_target_path("shape", "semantic-fact")
        assert path == "/vault/agent-knowledge/shape/facts.md"

    def test_shared_facts(self):
        path = resolve_target_path("shared", "semantic-fact")
        assert "shared" in path
        assert "facts.md" in path


class TestEntityRouting:
    def test_entity_person(self):
        path = resolve_target_path("builder", "entity", entity_type="person", entity_slug="alice-chen")
        assert path == "/vault/agent-knowledge/entities/person/alice-chen.md"

    def test_entity_organisation(self):
        path = resolve_target_path("builder", "entity", entity_type="organisation", entity_slug="triad-consulting")
        assert "entities/organisation/triad-consulting.md" in path

    def test_entity_defaults(self):
        path = resolve_target_path("builder", "entity")
        assert "entities/unknown/unknown.md" in path


class TestAgentScoping:
    def test_all_valid_agents(self):
        for agent in VALID_AGENTS:
            path = resolve_target_path(agent, "procedural-rule")
            assert agent in path

    def test_invalid_agent_raises(self):
        with pytest.raises(ValueError, match="Invalid agent"):
            resolve_target_path("admin", "procedural-rule")

    def test_another_agent_path(self):
        with pytest.raises(ValueError, match="Invalid agent"):
            resolve_target_path("notanagent", "episodic")

    def test_shared_is_valid(self):
        path = resolve_target_path("shared", "procedural-rule")
        assert path  # should not raise

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown classification type"):
            resolve_target_path("builder", "nonexistent-type")

    def test_agent_isolation_builder_not_in_shape_path(self):
        """builder paths should not contain 'shape' and vice versa."""
        builder_path = resolve_target_path("builder", "procedural-rule")
        shape_path = resolve_target_path("shape", "procedural-rule")
        assert "shape" not in builder_path
        assert "builder" not in shape_path
