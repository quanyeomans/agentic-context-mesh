"""
Tests for kairix.agents.mcp.server — MCP tool implementations.

Tool functions are pure Python and importable without the ``mcp`` package.
Tests use dependency injection (DI) — no monkey-patching required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.agents.mcp.server import (
    tool_entity,
    tool_usage_guide,
)

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class FakeNeo4jClient:
    """Minimal stand-in for the Neo4j graph client."""

    def __init__(self, rows: list[dict] | None = None, *, available: bool = True):
        self._rows = rows or []
        self.available = available

    def cypher(self, query: str, params: dict | None = None) -> list[dict]:
        return self._rows


# tool_search behaviour is now covered in tests/use_cases/test_search.py
# (Phase 2 of #168 — tool_search is a thin adapter around run_search).


# ---------------------------------------------------------------------------
# tool_entity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tool_entity_neo4j_primary() -> None:
    fake = FakeNeo4jClient(
        [
            {
                "id": "acme",
                "name": "Acme",
                "type": "Organisation",
                "vault_path": "02-Areas/00-Clients/Acme/Acme.md",
                "role": None,
                "org": None,
                "tier": None,
                "engagement_status": None,
                "domain": None,
                "industry": None,
                "category": None,
            }
        ]
    )
    result = tool_entity(name="Acme", neo4j_client=fake)

    assert result["id"] == "acme"
    assert result["name"] == "Acme"
    assert result["type"] == "Organisation"
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_entity_neo4j_not_found_returns_error() -> None:
    fake = FakeNeo4jClient([])
    result = tool_entity(name="Unknown Entity", neo4j_client=fake)

    assert result["id"] == ""
    assert result["error"].startswith("EntityNotFound:")


@pytest.mark.unit
def test_tool_entity_neo4j_unavailable_returns_error() -> None:
    fake = FakeNeo4jClient(available=False)
    result = tool_entity(name="Test", neo4j_client=fake)

    assert result["error"] != ""


@pytest.mark.unit
def test_tool_entity_neo4j_exception_returns_error() -> None:
    """When the injected neo4j_client raises during query, the function surfaces an error dict."""

    class _RaisingNeo4jClient:
        @property
        def available(self) -> bool:
            return True

        def cypher(self, *args: object, **kwargs: object) -> list[dict[str, object]]:
            raise RuntimeError("no neo4j")

    result = tool_entity(name="Anything", neo4j_client=_RaisingNeo4jClient())
    assert result["error"] != ""
    assert result["id"] == ""


class AliasAwareFakeNeo4jClient:
    """Fake that evaluates the entity-card cypher against in-memory rows.

    Only honours the alias-match branch when the cypher string actually
    contains the ``any(alias IN coalesce(n.aliases, ...))`` clause — so
    reverting that clause in ``_ENTITY_CARD_CYPHER`` makes the alias
    lookup miss, which is what sabotage-proves the #253 fix.
    """

    def __init__(self, entities: list[dict]):
        self._entities = entities
        self.available = True

    def cypher(self, query: str, params: dict | None = None) -> list[dict]:
        params = params or {}
        target_id = (params.get("id") or "").lower()
        target_name = (params.get("name") or "").lower()
        cypher_checks_aliases = "alias IN coalesce(n.aliases" in query
        for e in self._entities:
            aliases = [a.lower() for a in (e.get("aliases") or [])]
            id_hit = e.get("id", "").lower() == target_id
            name_hit = e.get("name", "").lower() == target_name
            alias_hit = cypher_checks_aliases and target_name in aliases
            if id_hit or name_hit or alias_hit:
                return [e]
        return []


@pytest.mark.unit
def test_tool_entity_finds_entity_by_alias() -> None:
    """#253: looking up by an alias returns the canonical entity.

    The crawler stores aliases on entities; agents often know an entity
    by an alias rather than its canonical name. Before the fix the
    cypher only checked ``n.id`` and ``n.name``, so alias lookups
    silently returned not-found.
    """
    fake = AliasAwareFakeNeo4jClient(
        [
            {
                "id": "acme-inc",
                "name": "Acme Inc",
                "aliases": ["Acme", "ACME Corporation"],
                "type": "Organisation",
                "vault_path": "02-Areas/00-Clients/Acme/Acme.md",
                "role": None,
                "org": None,
                "tier": None,
                "engagement_status": None,
                "domain": None,
                "industry": None,
                "category": None,
            }
        ]
    )
    result = tool_entity(name="Acme", neo4j_client=fake)

    assert result["error"] == "", f"alias lookup returned error: {result['error']!r}"
    assert result["id"] == "acme-inc"
    assert result["name"] == "Acme Inc"


@pytest.mark.unit
def test_tool_entity_summary_includes_category_when_present() -> None:
    """An entity row carrying a ``category`` field appears in the summary string."""
    fake = FakeNeo4jClient(
        [
            {
                "id": "platform",
                "name": "Kairix Platform",
                "type": "Project",
                "vault_path": "02-Areas/Kairix/Kairix.md",
                "role": None,
                "org": None,
                "tier": None,
                "engagement_status": None,
                "domain": None,
                "industry": None,
                "category": "knowledge-platform",
            }
        ]
    )
    result = tool_entity(name="Kairix Platform", neo4j_client=fake)

    assert result["error"] == ""
    # The category value must appear verbatim in the summary so agents see it.
    assert "knowledge-platform" in result["summary"]


# tool_prep behaviour is now covered in tests/use_cases/test_prep.py
# (Phase 3c of #168 — tool_prep is a thin adapter around run_prep).


# tool_timeline behaviour is now covered in tests/use_cases/test_timeline.py
# (Phase 1 of #168 — tool_timeline is a thin adapter around run_timeline).


# NOTE: the ``build_server`` defensive ImportError branch (when the optional
# ``mcp`` extra is not installed) is documented as ``# pragma: no cover`` in
# server.py. The branch is reachable only when ``pip install 'kairix[agents]'``
# has not been run; the test suite (which exercises ``build_server`` end-to-end
# in tests/integration/test_mcp_build_server.py) requires the extra.


# ---------------------------------------------------------------------------
# tool_usage_guide (TEST-5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tool_usage_guide_empty_topic_returns_bundled_content() -> None:
    """Empty topic returns the bundled guide content with NO operator action.

    #466 regression: the guide ships as package data under
    ``kairix/agents/usage_guide/data/``, so ``tool_usage_guide()`` with no
    ``guide_path`` resolves it directly from the installed package — no
    ``docs/`` copy, no write, no ``onboard guide`` step. This is the
    production default an agent hits over MCP.

    Sabotage proof (executed locally): point ``_GUIDE_RESOURCE`` in
    ``kairix/use_cases/usage_guide.py`` at a non-existent ``data/`` file —
    the resolver no longer finds the bundled copy and the tool returns the
    ``UsageGuideNotFound`` envelope, so ``error == ""`` fails. Restored.
    """
    result = tool_usage_guide(topic="")
    assert result["error"] == "", f"bundled default returned an error: {result['error']!r}"
    assert result["content"].strip(), "bundled guide content was empty"


@pytest.mark.unit
def test_tool_usage_guide_topic_filter_against_bundled_guide() -> None:
    """A topic filter against the bundled guide returns a focused slice.

    Exercises the real bundled guide (no fixture, no operator action) so a
    structural change to the shipped guide that drops the budget topic is
    caught. ``budget`` is a stable heading/keyword in the canonical guide.
    """
    result = tool_usage_guide(topic="budget")
    assert result["error"] == "", f"bundled default returned an error: {result['error']!r}"
    assert "budget" in result["content"].lower(), (
        f"topic filter returned no budget content: {result['content'][:200]!r}"
    )


@pytest.mark.unit
def test_tool_usage_guide_returns_error_when_explicit_guide_path_missing(tmp_path: Path) -> None:
    """Missing guide file returns error dict — uses guide_path injection (no monkeypatch)."""
    missing = tmp_path / "no-such-guide.md"
    result = tool_usage_guide(topic="anything", guide_path=missing)
    assert result["content"] == ""
    assert result["error"].startswith("UsageGuideNotFound:")
    assert result["topic"] == "anything"


@pytest.mark.unit
def test_tool_usage_guide_returns_full_text_when_topic_empty(tmp_path: Path) -> None:
    """Empty topic returns the entire guide content unchanged."""
    guide = tmp_path / "guide.md"
    guide.write_text("# Title\n\nFull content here.\n", encoding="utf-8")
    result = tool_usage_guide(topic="", guide_path=guide)
    assert result["error"] == ""
    assert result["content"] == "# Title\n\nFull content here.\n"


@pytest.mark.unit
def test_tool_usage_guide_returns_only_matching_section_when_topic_in_heading(tmp_path: Path) -> None:
    """A topic that matches a heading returns just that section's content."""
    guide = tmp_path / "guide.md"
    guide.write_text(
        "## Search\nUse the search tool.\n## Temporal\nDate-aware retrieval.\n## Budget\nBudget rules.\n",
        encoding="utf-8",
    )
    result = tool_usage_guide(topic="temporal", guide_path=guide)
    assert result["error"] == ""
    assert "Date-aware retrieval" in result["content"]
    assert "Budget rules" not in result["content"], "non-matching section leaked"
    assert "Use the search tool" not in result["content"], "non-matching section leaked"


@pytest.mark.unit
def test_tool_usage_guide_aggregates_multiple_matching_sections(tmp_path: Path) -> None:
    """Multiple sections matching the topic are concatenated.

    Closes coverage of the ``if in_section and current: sections.append(...)``
    branch that fires when a matching section ends at end-of-file (no trailing
    heading to flush it).
    """
    guide = tmp_path / "guide.md"
    guide.write_text(
        "## Temporal Anchors\n"
        "Anchor dates to a reference point.\n"
        "## Other\n"
        "Unrelated content.\n"
        "## Temporal Rewriting\n"
        "Rewrites queries with date filters.\n",
        encoding="utf-8",
    )
    result = tool_usage_guide(topic="temporal", guide_path=guide)
    assert "Anchor dates to a reference point" in result["content"]
    assert "Rewrites queries with date filters" in result["content"]
    assert "Unrelated content" not in result["content"]


@pytest.mark.unit
def test_tool_usage_guide_falls_back_to_keyword_match_when_no_heading_matches(tmp_path: Path) -> None:
    """When no heading matches, fall back to keyword-line search across the guide."""
    guide = tmp_path / "guide.md"
    guide.write_text(
        "# Kairix Agent Usage Guide\n\n"
        "## Overview\nKairix supports adaptive recall.\n\n"
        "## Search\nUse the search tool to recall documents.\n",
        encoding="utf-8",
    )
    result = tool_usage_guide(topic="recall", guide_path=guide)
    assert result["error"] == ""
    # Both lines containing "recall" appear in the fallback content.
    assert "adaptive recall" in result["content"]
    assert "to recall documents" in result["content"]


@pytest.mark.unit
def test_tool_usage_guide_falls_back_to_truncated_text_when_no_match(tmp_path: Path) -> None:
    """When no heading and no keyword line matches, return the first 2000 chars of the guide."""
    body = "Line that does not contain the topic at all. " * 100  # ~4500 chars
    guide = tmp_path / "guide.md"
    guide.write_text(body, encoding="utf-8")
    result = tool_usage_guide(topic="utterly-absent-keyword", guide_path=guide)
    assert result["error"] == ""
    assert len(result["content"]) <= 2000


@pytest.mark.unit
def test_tool_usage_guide_returns_error_dict_when_read_fails(tmp_path: Path) -> None:
    """A guide path that exists but can't be read (e.g. it's a directory) → error dict.

    Phase 3f of #168 changed the error envelope from a generic
    'lookup failed' message to the structured ``<Class>: <msg>`` form
    that matches the other use cases.
    """
    bad = tmp_path / "guide-but-actually-a-dir"
    bad.mkdir()
    result = tool_usage_guide(topic="any", guide_path=bad)
    assert result["content"] == ""
    # IsADirectoryError on POSIX, PermissionError on Windows — both are class-prefixed
    assert ":" in result["error"]
    assert result["error"] != ""


# tool_contradict behaviour is now covered by tests/use_cases/test_contradict.py
# (Phase 2 of #168 — tool_contradict is a thin adapter around run_contradict).
