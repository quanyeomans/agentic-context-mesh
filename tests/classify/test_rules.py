"""
Tests for the rule-based classifier (mnemosyne/classify/rules.py).

Tests all rule patterns plus edge cases.
"""

from __future__ import annotations

import pytest

from mnemosyne.classify.rules import ClassificationResult, classify_by_rules, classify_content

# ---------------------------------------------------------------------------
# classify_by_rules unit tests
# ---------------------------------------------------------------------------


class TestEpisodic:
    def test_basic_timestamp_header(self):
        content = "## 09:15\nFixed the RRF path dedup bug in mnemosyne/search/rrf.py"
        result_type, reason = classify_by_rules(content)
        assert result_type == "episodic"
        assert "09:15" in reason or "HH:MM" in reason or "header" in reason

    def test_different_time(self):
        content = "## 14:30\nStarted working on Phase 3"
        result_type, _ = classify_by_rules(content)
        assert result_type == "episodic"

    def test_episodic_with_leading_whitespace(self):
        content = "  ## 08:00\nSession started"
        result_type, _ = classify_by_rules(content)
        assert result_type == "episodic"

    def test_not_episodic_wrong_format(self):
        content = "## No timestamp here\nSome content"
        result_type, _ = classify_by_rules(content)
        # Should not match episodic (no HH:MM format)
        assert result_type != "episodic"

    def test_episodic_multiline(self):
        content = "## 09:15\nDid some work\n\nAlso did more work"
        result_type, _ = classify_by_rules(content)
        assert result_type == "episodic"


class TestProceduralRule:
    def test_never_write(self):
        content = "Never write credentials to disk. Always fetch from Key Vault at runtime."
        result_type, _reason = classify_by_rules(content)
        assert result_type == "procedural-rule"

    def test_always_prefix(self):
        content = "Always use the `trash` command instead of `rm`."
        result_type, _ = classify_by_rules(content)
        assert result_type == "procedural-rule"

    def test_rule_colon(self):
        content = "rule: never commit secrets to git"
        result_type, _ = classify_by_rules(content)
        assert result_type == "procedural-rule"

    def test_constraint_colon(self):
        content = "constraint: must use Key Vault for all secrets"
        result_type, _ = classify_by_rules(content)
        assert result_type == "procedural-rule"

    def test_never_do(self):
        content = "never do direct DB writes without a migration"
        result_type, _ = classify_by_rules(content)
        assert result_type == "procedural-rule"

    def test_case_insensitive(self):
        content = "NEVER write passwords in config files"
        result_type, _ = classify_by_rules(content)
        assert result_type == "procedural-rule"


class TestProceduralPattern:
    def test_pattern_colon(self):
        content = "Pattern: Entity boost must always be scoped to candidate results. Step 1: get candidates."
        result_type, _ = classify_by_rules(content)
        assert result_type == "procedural-pattern"

    def test_workflow_colon(self):
        content = "workflow: how to add a new agent to the platform"
        result_type, _ = classify_by_rules(content)
        assert result_type == "procedural-pattern"

    def test_how_to(self):
        content = "How to run the embedding pipeline on a new vault"
        result_type, _ = classify_by_rules(content)
        assert result_type == "procedural-pattern"

    def test_steps_header(self):
        content = "## Steps\n1. Do this\n2. Then that"
        result_type, _ = classify_by_rules(content)
        assert result_type == "procedural-pattern"

    def test_step_1(self):
        content = "step 1: clone the repo\nstep 2: install dependencies"
        result_type, _ = classify_by_rules(content)
        assert result_type == "procedural-pattern"


class TestSemanticDecision:
    def test_we_decided(self):
        content = (
            "We decided to use GPT-4o-mini for synthesis. Rationale: Phi-4-mini cold-start failures make it unreliable."
        )
        result_type, _ = classify_by_rules(content)
        assert result_type == "semantic-decision"

    def test_decided_colon(self):
        content = "decided: use Azure text-embedding-3-large for all embeddings"
        result_type, _ = classify_by_rules(content)
        assert result_type == "semantic-decision"

    def test_decision_colon(self):
        content = "decision: store entity facts in SQLite, not Obsidian"
        result_type, _ = classify_by_rules(content)
        assert result_type == "semantic-decision"

    def test_adr_reference(self):
        content = "ADR-007: use BM25 + vector hybrid search with RRF fusion"
        result_type, _ = classify_by_rules(content)
        assert result_type == "semantic-decision"

    def test_we_chose(self):
        content = "we chose to build the classifier in Python rather than TypeScript"
        result_type, _ = classify_by_rules(content)
        assert result_type == "semantic-decision"

    def test_rationale_colon(self):
        content = "rationale: the existing architecture already uses SQLite"
        result_type, _ = classify_by_rules(content)
        assert result_type == "semantic-decision"


class TestSemanticFact:
    def test_ip_address(self):
        content = "The PostgreSQL server is at 10.0.1.5 (private subnet)"
        result_type, _ = classify_by_rules(content)
        assert result_type == "semantic-fact"

    def test_endpoint_colon(self):
        content = "endpoint: https://my-openai.openai.azure.com/"
        result_type, _ = classify_by_rules(content)
        assert result_type == "semantic-fact"

    def test_version_colon(self):
        content = "version: 2024-02-01 (Azure OpenAI API)"
        result_type, _ = classify_by_rules(content)
        assert result_type == "semantic-fact"

    def test_vcpu(self):
        content = "VM spec: 4 vCPU, 16 GB RAM, 128 GB SSD"
        result_type, _ = classify_by_rules(content)
        assert result_type == "semantic-fact"

    def test_port_number(self):
        content = "The service listens on port 8080"
        result_type, _ = classify_by_rules(content)
        assert result_type == "semantic-fact"

    def test_port_in_url(self):
        content = "Connect to http://localhost:5432 for PostgreSQL"
        result_type, _ = classify_by_rules(content)
        assert result_type == "semantic-fact"


class TestNoMatch:
    def test_empty_content(self):
        result_type, _ = classify_by_rules("")
        assert result_type is None

    def test_whitespace_only(self):
        result_type, _ = classify_by_rules("   \n  ")
        assert result_type is None

    def test_ambiguous_content(self):
        # Generic discussion without clear markers
        content = "The team met to discuss the project status and planned next steps."
        result_type, _ = classify_by_rules(content)
        # Should not confidently match any rule
        assert result_type is None

    def test_plain_note(self):
        content = "Reviewed the mnemosyne codebase today."
        result_type, _ = classify_by_rules(content)
        assert result_type is None


# ---------------------------------------------------------------------------
# classify_content public API tests
# ---------------------------------------------------------------------------


class TestClassifyContent:
    def test_rule_match_returns_result(self):
        result = classify_content(
            "Never write credentials to disk. Always fetch from Key Vault at runtime.",
            agent="builder",
        )
        assert isinstance(result, ClassificationResult)
        assert result.type == "procedural-rule"
        assert result.confidence >= 0.80
        assert not result.needs_confirmation

    def test_no_match_returns_unknown(self):
        result = classify_content(
            "The project is going well.",
            agent="builder",
        )
        assert result.type == "unknown"
        assert result.needs_confirmation is True

    def test_invalid_agent_raises(self):
        with pytest.raises(ValueError, match="Invalid agent"):
            classify_content("some content", agent="invalid_agent")

    def test_episodic_path_contains_memory(self):
        result = classify_content(
            "## 10:30\nCompleted the refactor",
            agent="builder",
        )
        assert result.type == "episodic"
        assert "memory" in result.target_path

    def test_procedural_rule_path(self):
        result = classify_content(
            "rule: always test before committing",
            agent="builder",
        )
        assert result.type == "procedural-rule"
        assert "rules.md" in result.target_path
        assert "builder" in result.target_path

    def test_semantic_decision_path(self):
        result = classify_content(
            "we decided to use Redis for caching",
            agent="shape",
        )
        assert result.type == "semantic-decision"
        assert "decisions.md" in result.target_path
        assert "shape" in result.target_path

    def test_shared_agent(self):
        result = classify_content(
            "never write credentials in source code",
            agent="shared",
        )
        assert result.type == "procedural-rule"
        assert "shared" in result.target_path


# ---------------------------------------------------------------------------
# Benchmark suite alignment tests (CL01-CL04)
# ---------------------------------------------------------------------------


class TestBenchmarkCases:
    """Direct verification that the benchmark classification cases pass."""

    def test_cl01_procedural_rule(self):
        content = "Never write credentials to disk. Always fetch from Key Vault at runtime."
        result_type, _ = classify_by_rules(content)
        assert result_type == "procedural-rule", f"Expected procedural-rule, got {result_type}"

    def test_cl02_episodic(self):
        content = "## 09:15\nFixed the RRF path dedup bug in mnemosyne/search/rrf.py"
        result_type, _ = classify_by_rules(content)
        assert result_type == "episodic", f"Expected episodic, got {result_type}"

    def test_cl03_semantic_decision(self):
        content = (
            "We decided to use GPT-4o-mini for synthesis. Rationale: Phi-4-mini cold-start failures make it unreliable."
        )
        result_type, _ = classify_by_rules(content)
        assert result_type == "semantic-decision", f"Expected semantic-decision, got {result_type}"

    def test_cl04_procedural_pattern(self):
        content = (
            "Pattern: Entity boost must always be scoped to candidate results. "
            "Step 1: get candidates. Step 2: count mentions within candidates."
        )
        result_type, _ = classify_by_rules(content)
        assert result_type == "procedural-pattern", f"Expected procedural-pattern, got {result_type}"
