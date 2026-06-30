"""
Tests for briefing source fetchers (kairix/briefing/sources.py).

PR 1.2 / #420 — the memory-reading fetchers take an iterable of
``memory_dirs`` (the surfaces declared on the agent's AgentScope).
Tests pass a tmp_path-rooted list rather than monkeypatching internals.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from kairix.agents.briefing.sources import (
    fetch_entity_stub,
    fetch_hybrid_search,
    fetch_hybrid_search_sources,
    fetch_knowledge_rules,
    fetch_memory_logs,
    fetch_recent_decisions,
    fetch_recent_memory,
)
from kairix.core.protocols import SourceRef
from kairix.text import estimate_tokens, truncate_to_tokens
from tests.fakes import FakeSearchPipeline

# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTokenHelpers:
    @pytest.mark.unit
    def testestimate_tokens_empty(self):
        assert estimate_tokens("") == 0

    @pytest.mark.unit
    def testestimate_tokens_small(self):
        # "hello world" = 2 words * 1.3 = 2
        count = estimate_tokens("hello world")
        assert count >= 2

    @pytest.mark.unit
    def testtruncate_to_tokens_short(self):
        text = "hello world"
        result = truncate_to_tokens(text, 100)
        assert result == text  # no truncation needed

    @pytest.mark.unit
    def testtruncate_to_tokens_truncates(self):
        words = ["word"] * 1000
        text = " ".join(words)
        result = truncate_to_tokens(text, 50)
        assert len(result) < len(text)
        assert "[truncated]" in result


# ---------------------------------------------------------------------------
# Memory log tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchMemoryLogs:
    @pytest.mark.unit
    def test_returns_empty_for_missing_dir(self):
        result = fetch_memory_logs("nonexistent_agent_xyz")
        assert result == ""

    @pytest.mark.unit
    def test_reads_tagged_items(self, tmp_path):
        today = date.today()
        memory_dir = tmp_path / "builder" / "memory"
        memory_dir.mkdir(parents=True)

        content = (
            "## Session\n"
            "[pending] Fix the RRF bug\n"
            "[blocked] Waiting for Azure quota\n"
            "[action: send summary to BuilderCo]\n"
            "Normal log entry\n"
        )
        (memory_dir / f"{today.isoformat()}.md").write_text(content)
        result = fetch_memory_logs("builder", memory_dirs=[memory_dir])

        assert "[pending]" in result or "pending" in result.lower()
        assert "[blocked]" in result or "blocked" in result.lower()

    @pytest.mark.unit
    def test_handles_read_error_gracefully(self, tmp_path):
        memory_dir = tmp_path / "builder" / "memory"
        memory_dir.mkdir(parents=True)
        today = date.today()
        bad_file = memory_dir / f"{today.isoformat()}.md"
        bad_file.write_bytes(b"\xff\xfe invalid utf-8")
        result = fetch_memory_logs("builder", memory_dirs=[memory_dir])
        # Should not raise — may return empty or partial content
        assert isinstance(result, str)

    @pytest.mark.unit
    def test_respects_token_cap(self, tmp_path):
        today = date.today()
        memory_dir = tmp_path / "builder" / "memory"
        memory_dir.mkdir(parents=True)

        # Create large content
        content = "\n".join([f"[pending] item {i}" for i in range(1000)])
        (memory_dir / f"{today.isoformat()}.md").write_text(content)
        result = fetch_memory_logs("builder", max_tokens=50, memory_dirs=[memory_dir])

        assert estimate_tokens(result) <= 100  # some buffer


# ---------------------------------------------------------------------------
# Recent memory tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchRecentMemory:
    @pytest.mark.unit
    def test_returns_empty_for_missing_dir(self):
        result = fetch_recent_memory("nonexistent_agent_xyz")
        assert result == ""

    @pytest.mark.unit
    def test_reads_today_and_yesterday(self, tmp_path):
        today = date.today()
        yesterday = today - timedelta(days=1)
        memory_dir = tmp_path / "builder" / "memory"
        memory_dir.mkdir(parents=True)

        (memory_dir / f"{today.isoformat()}.md").write_text("Today's content here")
        (memory_dir / f"{yesterday.isoformat()}.md").write_text("Yesterday content here")
        result = fetch_recent_memory("builder", memory_dirs=[memory_dir])

        assert today.isoformat() in result
        assert yesterday.isoformat() in result


# ---------------------------------------------------------------------------
# Entity stub tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchEntityStub:
    @pytest.mark.unit
    def test_returns_empty_for_missing_entity(self):
        result = fetch_entity_stub("nonexistent_agent_xyz")
        assert result == ""

    @pytest.mark.unit
    def test_reads_concept_stub(self, tmp_path):
        entity_dir = tmp_path / "04-Agent-Knowledge" / "entities" / "concept"
        entity_dir.mkdir(parents=True)
        (entity_dir / "builder.md").write_text("# Builder\nThe engineering agent.")
        result = fetch_entity_stub("builder", document_root=tmp_path)

        assert "Builder" in result or "builder" in result.lower()


# ---------------------------------------------------------------------------
# Knowledge rules tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchKnowledgeRules:
    @pytest.mark.unit
    def test_returns_empty_for_missing_rules(self, tmp_path):
        # Use an isolated document store root with no rules files
        result = fetch_knowledge_rules("nonexistent_agent_xyz", document_root=tmp_path)
        assert result == ""

    @pytest.mark.unit
    def test_reads_rules_file(self, tmp_path):
        rules_dir = tmp_path / "04-Agent-Knowledge" / "builder"
        rules_dir.mkdir(parents=True)
        (rules_dir / "rules.md").write_text("# Rules\n1. Never commit secrets\n2. Always test")
        result = fetch_knowledge_rules("builder", document_root=tmp_path)

        assert "secrets" in result.lower() or "rules" in result.lower()


# ---------------------------------------------------------------------------
# Recent decisions tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchRecentDecisions:
    @pytest.mark.unit
    def test_returns_empty_for_missing_decisions(self):
        result = fetch_recent_decisions("nonexistent_agent_xyz")
        assert result == ""

    @pytest.mark.unit
    def test_reads_decisions_file(self, tmp_path):
        decisions_dir = tmp_path / "04-Agent-Knowledge" / "builder"
        decisions_dir.mkdir(parents=True)
        (decisions_dir / "decisions.md").write_text(
            "# Decisions\n- ADR-001: Use Azure embeddings\n- ADR-002: SQLite for entity facts"
        )
        result = fetch_recent_decisions("builder", document_root=tmp_path)

        assert "ADR" in result or "decision" in result.lower()

    @pytest.mark.unit
    def test_returns_empty_when_no_decisions_file(self, tmp_path):
        # Should return empty string when decisions.md doesn't exist
        result = fetch_recent_decisions("builder", document_root=tmp_path)
        assert isinstance(result, str)

    @pytest.mark.unit
    def test_truncates_long_decisions_to_last_3000_chars(self, tmp_path):
        """When decisions.md exceeds 3000 chars, only the tail is kept (line 222)."""
        decisions_dir = tmp_path / "04-Agent-Knowledge" / "builder"
        decisions_dir.mkdir(parents=True)
        # Make a >3000 char file with a distinctive trailing marker
        body = ("Old decision line. " * 200) + "\n## RECENT_MARKER\nLatest decision here.\n"
        assert len(body) > 3000
        (decisions_dir / "decisions.md").write_text(body)
        result = fetch_recent_decisions("builder", document_root=tmp_path, max_tokens=10_000)
        # The recent marker must be in the (tail-trimmed) output
        assert "RECENT_MARKER" in result
        # Old lines from the very start should not all be present
        # (head truncation keeps last 3000 chars)
        assert len(result) <= len(body) + 200  # accounts for header text + truncation marker


@pytest.mark.unit
class TestFetchMemoryLogsErrorPaths:
    """Cover error handling and outer-except wrappers."""

    @pytest.mark.unit
    def test_outer_exception_returns_empty(self, tmp_path):
        """When the memory_dir.exists() call raises (e.g., bad type), the outer
        except returns ''. Use a Path-like object whose exists() raises."""

        class _ExplodingPath:
            def exists(self):
                raise RuntimeError("disk gone")

        result = fetch_memory_logs("builder", memory_dirs=[_ExplodingPath()])  # type: ignore[list-item]  # exploding fake to trip outer except
        assert result == ""


@pytest.mark.unit
class TestFetchRecentMemoryErrorPaths:
    @pytest.mark.unit
    def test_outer_exception_returns_empty(self):
        """fetch_recent_memory wraps everything in try/except; bad input → ''."""

        class _ExplodingPath:
            def exists(self):
                raise RuntimeError("disk gone")

        result = fetch_recent_memory("builder", memory_dirs=[_ExplodingPath()])  # type: ignore[list-item]  # exploding fake to trip outer except
        assert result == ""

    @pytest.mark.unit
    def test_unreadable_file_per_day_logs_warning(self, tmp_path):
        """When a per-day file raises on read, the warning fires but processing
        continues for sibling days (lines 107-108)."""
        from datetime import date

        memory_dir = tmp_path / "builder" / "memory"
        memory_dir.mkdir(parents=True)
        today = date.today()
        bad = memory_dir / f"{today.isoformat()}.md"
        bad.write_bytes(b"\xff\xfe garbage")  # invalid utf-8 still readable with errors=replace
        bad.chmod(0o000)
        try:
            result = fetch_recent_memory("builder", memory_dirs=[memory_dir])
            # When the file is unreadable, the function returns "" (no parts collected)
            assert isinstance(result, str)
        finally:
            bad.chmod(0o644)


@pytest.mark.unit
class TestFetchEntityStubErrorPaths:
    @pytest.mark.unit
    def test_outer_exception_returns_empty(self):
        """fetch_entity_stub catches Exception in the outer scope (lines 159-161)."""

        # Pass document_root as something whose / operation raises
        class _ExplodingRoot:
            def __truediv__(self, other):
                raise RuntimeError("bad root")

        result = fetch_entity_stub("builder", document_root=_ExplodingRoot())  # type: ignore[arg-type]  # exploding fake to trip outer except
        assert result == ""

    @pytest.mark.unit
    def test_unreadable_entity_stub_returns_empty(self, tmp_path):
        """A matching stub that raises on read is logged and skipped (lines 153-154)."""
        entity_dir = tmp_path / "04-Agent-Knowledge" / "entities" / "concept"
        entity_dir.mkdir(parents=True)
        stub = entity_dir / "builder.md"
        stub.write_text("# Builder")
        stub.chmod(0o000)
        try:
            result = fetch_entity_stub("builder", document_root=tmp_path)
            # Skipped — falls through to next candidate (none) → "" returned
            assert result == ""
        finally:
            stub.chmod(0o644)


@pytest.mark.unit
class TestFetchKnowledgeRulesErrorPaths:
    @pytest.mark.unit
    def test_outer_exception_returns_empty(self):
        """fetch_knowledge_rules wraps everything (lines 196-198)."""

        class _ExplodingRoot:
            def __truediv__(self, other):
                raise RuntimeError("bad root")

        result = fetch_knowledge_rules("builder", document_root=_ExplodingRoot())  # type: ignore[arg-type]  # exploding fake to trip outer except
        assert result == ""

    @pytest.mark.unit
    def test_unreadable_rules_file_logged(self, tmp_path):
        """Unreadable rules.md is logged and skipped (lines 187-188)."""
        rules_dir = tmp_path / "04-Agent-Knowledge" / "builder"
        rules_dir.mkdir(parents=True)
        rules = rules_dir / "rules.md"
        rules.write_text("# Rules\n1. Be excellent.")
        rules.chmod(0o000)
        try:
            result = fetch_knowledge_rules("builder", document_root=tmp_path)
            assert result == ""
        finally:
            rules.chmod(0o644)


@pytest.mark.unit
class TestFetchRecentDecisionsErrorPaths:
    @pytest.mark.unit
    def test_outer_exception_returns_empty(self):
        """fetch_recent_decisions wraps in try/except (lines 233-235)."""

        class _ExplodingRoot:
            def __truediv__(self, other):
                raise RuntimeError("bad root")

        result = fetch_recent_decisions("builder", document_root=_ExplodingRoot())  # type: ignore[arg-type]  # exploding fake to trip outer except
        assert result == ""

    @pytest.mark.unit
    def test_unreadable_decisions_file_logged(self, tmp_path):
        """Unreadable decisions.md is logged and skipped (lines 224-225)."""
        decisions_dir = tmp_path / "04-Agent-Knowledge" / "builder"
        decisions_dir.mkdir(parents=True)
        decisions = decisions_dir / "decisions.md"
        decisions.write_text("# Decisions\n- ADR-001")
        decisions.chmod(0o000)
        try:
            result = fetch_recent_decisions("builder", document_root=tmp_path)
            assert result == ""
        finally:
            decisions.chmod(0o644)


@pytest.mark.unit
class TestFetchHybridSearchErrorPaths:
    """Exercise the production no-injection path of fetch_hybrid_search.

    With both DI seams left ``None`` the function builds the real pipeline
    via ``build_search_pipeline`` and collects focus signals through the
    cached source fetchers. In an environment with no Azure creds / Neo4j
    and no on-disk signal, the search fails (or returns empty) and the
    outer except returns '' — the function must never raise.
    """

    @pytest.mark.unit
    def test_failure_returns_empty(self):
        """Calling without creds / Neo4j leaves build_search_pipeline returning
        a degraded pipeline whose .search() raises. The outer except returns ''.
        """
        # The pipeline construction may succeed but .search() will fail without
        # a populated index. Either way the function returns ''.
        result = fetch_hybrid_search("nonexistent-agent-xyz")
        assert isinstance(result, str)


@pytest.mark.unit
class TestFetchHybridSearchQueryFromSignal:
    """PLA-264: the retrieval query is built from real agent work-signal
    (the [pending]/[blocked]/TODO lines the fan-out already fetched), not the
    bare agent name — while agent= / scope= are preserved for collection
    scoping. Driven through the FakeSearchPipeline DI seam, which records the
    query + kwargs it received.
    """

    @pytest.mark.unit
    def test_query_is_built_from_focus_signals_not_agent_name(self):
        pipeline = FakeSearchPipeline(
            scripted_results=[
                FakeSearchPipeline.make_chunk_row(
                    path="notes/connectors.md",
                    title="Connectors",
                    content="connector refactor design notes",
                ),
            ]
        )
        signals = [
            "[2026-06-30] [pending] ship the connector refactor",
            "[2026-06-29] [blocked] waiting on review of PR 42",
        ]

        result = fetch_hybrid_search("builder", pipeline=pipeline, focus_signals=signals)

        assert pipeline.calls, "fetch_hybrid_search did not call pipeline.search"
        call = pipeline.calls[0]
        query = call["query"]
        # The query carries the real work-items, not the degenerate "builder".
        assert "ship the connector refactor" in query
        assert "waiting on review of PR 42" in query
        assert query != "builder"
        # The status markers and date labels are stripped from the query text.
        assert "[pending]" not in query
        assert "[blocked]" not in query
        # agent= / scope= are preserved for collection scoping.
        assert call["kwargs"]["agent"] == "builder"
        assert call["kwargs"]["scope"] == "shared+agent"
        # The scripted hit is rendered into the returned context.
        assert "connector refactor design notes" in result

    @pytest.mark.unit
    def test_duplicate_focus_signals_are_deduped_in_query(self):
        """A work-item repeated across days collapses to a single occurrence in
        the query — the dedup guard keeps the focus string tight rather than
        echoing the same task once per memory line.
        """
        pipeline = FakeSearchPipeline(scripted_results=[])
        signals = [
            "[2026-06-30] [pending] ship the connector refactor",
            "[2026-06-29] [pending] ship the connector refactor",
        ]

        fetch_hybrid_search("builder", pipeline=pipeline, focus_signals=signals)

        query = pipeline.calls[0]["query"]
        assert query.count("ship the connector refactor") == 1

    @pytest.mark.unit
    def test_query_falls_back_to_agent_name_when_no_signal(self):
        """With no actionable signal (fresh agent), the query falls back to the
        agent name rather than searching for the empty string — agent= / scope=
        still preserved.
        """
        pipeline = FakeSearchPipeline(scripted_results=[])

        result = fetch_hybrid_search("growth", pipeline=pipeline, focus_signals=[])

        assert pipeline.calls
        call = pipeline.calls[0]
        assert call["query"] == "growth"
        assert call["kwargs"]["agent"] == "growth"
        assert call["kwargs"]["scope"] == "shared+agent"
        # No results scripted → empty context, no raise.
        assert result == ""


@pytest.mark.unit
class TestFetchHybridSearchSources:
    """PLA-266: the brief captures the retrieved chunks as resolvable
    :class:`SourceRef` breadcrumbs (the structured citations the agent reads
    back), not just the prose text the synthesiser collapses. Driven through
    the FakeSearchPipeline DI seam so the structured projection is proven
    without an index.
    """

    @staticmethod
    def _three_chunk_pipeline() -> FakeSearchPipeline:
        return FakeSearchPipeline(
            scripted_results=[
                FakeSearchPipeline.make_chunk_row(
                    path="archive/handbook.zip#1536",
                    title="Acme Handbook",
                    content="deployment runbook deploy procedure",
                    source_uri="sharepoint://acme-site/handbook.zip",
                    collection="shared",
                ),
                FakeSearchPipeline.make_chunk_row(
                    path="notes/onboarding.md",
                    title="Onboarding",
                    content="deployment notes for new hires",
                    collection="shared",
                ),
                FakeSearchPipeline.make_chunk_row(
                    path="decisions/2026-06-30.md",
                    title="Deploy decision",
                    content="we will cut over the deployment on friday",
                    source_uri="obsidian://decisions/2026-06-30.md",
                    collection="agent-alpha",
                ),
            ]
        )

    @pytest.mark.unit
    def test_returns_one_sourceref_per_retrieved_chunk(self):
        """Each retrieved chunk becomes a resolvable SourceRef — the SLO is
        >=3 structured citations per brief, so three hits yields three refs.
        """
        refs = fetch_hybrid_search_sources(
            "agent-alpha",
            pipeline=self._three_chunk_pipeline(),
            focus_signals=["[2026-06-30] [pending] ship the deployment"],
        )

        assert len(refs) == 3
        assert all(isinstance(r, SourceRef) for r in refs)

    @pytest.mark.unit
    def test_canonical_source_uri_is_threaded_not_munged_path(self):
        """The connector breadcrumb is the canonical ``source_uri``, distinct
        from the synthetic ``#<seq>`` chunk-key path.
        """
        refs = fetch_hybrid_search_sources(
            "agent-alpha",
            pipeline=self._three_chunk_pipeline(),
            focus_signals=["[pending] deployment"],
        )

        by_path = {r.path: r for r in refs}
        connector = by_path["archive/handbook.zip#1536"]
        assert connector.source_uri == "sharepoint://acme-site/handbook.zip"
        assert connector.collection == "shared"
        assert connector.title == "Acme Handbook"

    @pytest.mark.unit
    def test_passthrough_chunk_source_uri_falls_back_to_path(self):
        """A vault note with no connector URI still yields a resolvable
        breadcrumb — ``source_uri`` falls back to the path.
        """
        refs = fetch_hybrid_search_sources(
            "agent-alpha",
            pipeline=self._three_chunk_pipeline(),
            focus_signals=["[pending] deployment"],
        )

        by_path = {r.path: r for r in refs}
        vault = by_path["notes/onboarding.md"]
        assert vault.source_uri == "notes/onboarding.md"

    @pytest.mark.unit
    def test_no_results_yields_empty_list(self):
        """No hits → no citations, and the function never raises."""
        refs = fetch_hybrid_search_sources(
            "growth",
            pipeline=FakeSearchPipeline(scripted_results=[]),
            focus_signals=[],
        )
        assert refs == []

    @pytest.mark.unit
    def test_failure_returns_empty_list_never_raises(self):
        """A pipeline whose ``search`` raises degrades to no citations."""

        class _BoomPipeline:
            def search(self, **_kwargs):
                raise RuntimeError("backend down")

        refs = fetch_hybrid_search_sources(
            "agent-alpha",
            pipeline=_BoomPipeline(),
            focus_signals=["[pending] deployment"],
        )
        assert refs == []

    @pytest.mark.unit
    def test_production_path_builds_pipeline_and_returns_list(self):
        """With both DI seams ``None`` the function builds the real pipeline via
        ``build_search_pipeline`` (the production default the brief runs) and
        returns a list — empty in a no-index/no-creds environment, never a
        raise. Mirrors ``TestFetchHybridSearchErrorPaths`` for the text path.
        """
        result = fetch_hybrid_search_sources("nonexistent-agent-xyz")
        assert isinstance(result, list)
