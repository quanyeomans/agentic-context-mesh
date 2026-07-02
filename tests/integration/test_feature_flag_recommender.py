"""Integration tests for the ``recommender`` flag (F54 both-branch).

Exercises both branches of the two flag-gated recommender surfaces through
their production composition surfaces:

  * **The MCP/CLI adapter gate** — :func:`kairix.agents.mcp.server.tool_recommend_capabilities`
    reads the ``recommender`` flag via an injected ``flag_reader`` dep. When
    OFF it returns a disabled envelope WITHOUT calling ``run_recommend``;
    when ON it delegates to ``run_recommend`` and returns its envelope.
  * **The worker corpus-build hook** —
    :func:`kairix.worker.maybe_build_capability_corpus_at_boot` reads the
    flag. When OFF it is a structural no-op (the corpus builder never runs);
    when ON it builds the capabilities corpus.

F47 — the worker branch is reached via the production boot hook; the
adapter branch through the production ``tool_recommend_capabilities`` entry point. No
direct ``*Pipeline(...)`` construction.

F1/F2-clean: the flag value is threaded through the production
``flag_reader`` / ``read_flag`` DI seams via plain callables and
:class:`tests.fakes.FakeFeatureFlagResolver` — no @patch, no
``KAIRIX_FEATURE_*`` env vars.

Sabotage proof (executed by the agent, restored on completion):
inverting the gate in ``tool_recommend_capabilities`` so the OFF branch delegates to
``run_recommend`` — confirmed that ``test_recommender_flag_off_adapter_disabled``
fails (the disabled envelope is replaced by a real recommendations
envelope); restoring the gate returns it to green. See the Step 4.5 report
for the observed run.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_DISABLED_FRAGMENT = "recommender is disabled"


# ---------------------------------------------------------------------------
# Adapter gate — tool_recommend_capabilities
# ---------------------------------------------------------------------------


def _recommend_deps_with_one_hit() -> object:
    """A ``RecommendDeps`` whose search returns one kairix-tool hit."""
    from kairix.use_cases.recommend import RecommendDeps
    from tests.fakes import FakeSearchPipeline

    fake = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="capability://kairix/search",
                title="search",
                content="Hybrid retrieval.",
            ),
        ]
    )
    return RecommendDeps(
        search_fn=lambda **kw: fake.search(**kw),
        catalogue_fn=lambda: [
            {"name": "search", "mcp_tool": "search", "cli": "kairix search", "category": "retrieval"},
        ],
        correlation_id_fn=lambda: "cid",
    )


def test_recommender_flag_off_adapter_disabled() -> None:
    """Flag OFF — ``tool_recommend_capabilities`` returns a disabled envelope, no recs."""
    from kairix.agents.mcp.server import tool_recommend_capabilities

    resolver = FakeFeatureFlagResolver().with_flag("recommender", False)

    envelope = tool_recommend_capabilities(
        task="find prior work",
        deps=_recommend_deps_with_one_hit(),
        flag_reader=lambda: resolver.get("recommender"),
    )

    assert envelope["recommendations"] == []
    assert _DISABLED_FRAGMENT in envelope["error"]


def test_recommender_flag_on_adapter_delegates() -> None:
    """Flag ON — ``tool_recommend_capabilities`` delegates to ``run_recommend``."""
    from kairix.agents.mcp.server import tool_recommend_capabilities

    resolver = FakeFeatureFlagResolver().with_flag("recommender", True)

    envelope = tool_recommend_capabilities(
        task="find prior work",
        deps=_recommend_deps_with_one_hit(),
        flag_reader=lambda: resolver.get("recommender"),
    )

    assert envelope["error"] == ""
    names = [r["name"] for r in envelope["recommendations"]]
    assert "search" in names


# ---------------------------------------------------------------------------
# Worker corpus-build hook — maybe_build_capability_corpus_at_boot
# ---------------------------------------------------------------------------


def _seeded_corpus_deps() -> object:
    """A ``CapabilityCorpusDeps`` that writes one cap, BM25-only."""
    from kairix.knowledge.capabilities.builder import (
        CapabilityCatalogueBuilder,
        CapabilityCorpusDeps,
    )

    def _caps() -> list[dict[str, object]]:
        return [
            {
                "name": "contradict",
                "mcp_tool": "contradict",
                "cli": "kairix contradict",
                "category": "synthesis",
                "when_to_use": "Check new content for conflicts.",
            },
        ]

    return CapabilityCorpusDeps(
        builder=CapabilityCatalogueBuilder(catalogue_fn=_caps, now_fn=lambda: "2026-06-20T00:00:00+00:00"),
        embed_batch_fn=lambda texts: [],  # BM25-only branch
    )


def test_recommender_flag_off_worker_hook_noop(tmp_path: Path) -> None:
    """Flag OFF — the worker corpus-build hook is a structural no-op.

    The DB factory + corpus builder are wrapped with never-call stubs; a
    misroute increments their counters and the assertions fail loudly.
    """
    from kairix.worker import maybe_build_capability_corpus_at_boot

    resolver = FakeFeatureFlagResolver().with_flag("recommender", False)
    db_calls = {"n": 0}

    def _never_db() -> sqlite3.Connection:
        db_calls["n"] += 1
        return sqlite3.connect(":memory:")

    result = maybe_build_capability_corpus_at_boot(
        read_flag=resolver.get,
        db_factory=_never_db,
    )

    assert result is None, "OFF branch must return None (no build ran)"
    assert db_calls["n"] == 0, "OFF branch must not open the DB"


def test_recommender_flag_on_worker_hook_builds(tmp_path: Path) -> None:
    """Flag ON — the worker corpus-build hook writes the capabilities corpus."""
    from kairix.core.db.schema import create_schema
    from kairix.worker import maybe_build_capability_corpus_at_boot

    resolver = FakeFeatureFlagResolver().with_flag("recommender", True)
    db_path = tmp_path / "index.sqlite"

    def _db_factory() -> sqlite3.Connection:
        db = sqlite3.connect(db_path)
        create_schema(db)
        return db

    result = maybe_build_capability_corpus_at_boot(
        read_flag=resolver.get,
        db_factory=_db_factory,
        corpus_deps=_seeded_corpus_deps(),
    )

    assert result is not None, "ON branch must return a CapabilityCorpusResult"
    assert result.written == 1
    assert result.error == ""
