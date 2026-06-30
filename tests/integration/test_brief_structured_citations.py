"""Integration: the brief emits structured ``SourceRef`` citations end-to-end
through the composed search pipeline (PLA-266).

Composed via ``kairix.core.factory.build_search_pipeline`` with canonical
fakes (F47) — work-signal query in → BM25Result → FusedResult →
BudgetedResult → ``SourceRef`` out. Proves the SLO (>=3 structured citations
per brief) and the agent-facing surface: ``run_brief``'s envelope carries the
resolvable breadcrumbs and the ``## Sources`` footer renders them, so an agent
reading a brief can cite or re-open any source without re-running search.

Sabotage anchors (executed — see test docstrings): removing the ``source_uri``
threading from ``_ref_from_budgeted`` (kairix/agents/briefing/sources.py)
collapses the connector breadcrumb to the munged path; dropping the footer
append in ``run_brief`` removes ``## Sources`` from the brief content. Restored.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.agents.briefing.sources import fetch_hybrid_search_sources
from kairix.core.factory import QUERY_CACHE_DISABLED, FactoryDeps, build_search_pipeline
from kairix.core.health import HealthDeps
from kairix.core.protocols import SourceRef
from kairix.core.search.config import RetrievalConfig
from kairix.core.search.fusion import RRFFusion
from kairix.core.search.intent import QueryIntent
from kairix.use_cases.brief import BriefDeps, brief_output_to_envelope, run_brief
from tests.fakes import (
    FakeClassifier,
    FakeCollectionResolver,
    FakeDocumentRepository,
    FakeEmbeddingService,
    FakeGraphRepository,
    FakePaths,
    FakeVectorRepository,
)

pytestmark = pytest.mark.integration

_AGENT = "agent-alpha"
# A connector chunk whose canonical source_uri differs from its synthetic
# chunk-key path, two vault notes (source_uri NULL → falls back to path), and
# a decision note — four hits so the >=3-citations SLO has headroom. All four
# carry "deployment" so the work-signal query matches them.
_CORPUS: list[dict[str, Any]] = [
    {
        "path": "archive/handbook.zip#1536",
        "source_uri": "sharepoint://acme-site/handbook.zip",
        "collection": "shared",
        "title": "Acme Handbook",
        "content": "deployment runbook deploy procedure",
    },
    {
        "path": "notes/onboarding.md",
        "collection": "shared",
        "title": "Onboarding",
        "content": "deployment notes for new hires",
    },
    {
        "path": "decisions/2026-06-30.md",
        "source_uri": "obsidian://decisions/2026-06-30.md",
        "collection": "agent-alpha",
        "title": "Deploy decision",
        "content": "we will cut over the deployment on friday",
    },
    {
        "path": "notes/rollback.md",
        "collection": "shared",
        "title": "Rollback",
        "content": "deployment rollback steps if the cutover fails",
    },
]

# The fan-out's actionable work-signal — cleaned to the query "deployment",
# which matches every corpus doc in the substring-keyed fake doc repo.
_FOCUS_SIGNALS = ["[2026-06-30] [pending] deployment"]


def _build_pipeline() -> Any:
    return build_search_pipeline(
        config=RetrievalConfig.defaults(),
        paths=FakePaths(),
        deps=FactoryDeps(
            classifier_override=FakeClassifier(intent=QueryIntent.SEMANTIC),
            doc_repo_override=FakeDocumentRepository(documents=_CORPUS),
            embed_service_override=FakeEmbeddingService(dim=8),
            vec_repo_override=FakeVectorRepository(results=[]),
            graph_override=FakeGraphRepository(available=True),
            fusion_override=RRFFusion(k=60),
            boosts_override=[],
            resolver_override=FakeCollectionResolver({(_AGENT, "shared+agent"): ["shared", "agent-alpha"]}),
            query_cache_override=QUERY_CACHE_DISABLED,
        ),
    )


def _hermetic_health_deps() -> HealthDeps:
    return HealthDeps(
        secrets_loaded_fn=lambda: True,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )


def test_composed_search_yields_three_plus_structured_citations() -> None:
    """The brief's structured-source capture, composed via the factory, meets
    the >=3-citations SLO and threads the canonical connector source_uri.

    Sabotage-proof (executed): replaced ``source_uri=str(getattr(inner,
    "source_uri", "") or "")`` with ``source_uri=""`` in ``_ref_from_budgeted``
    — the connector assertion fired (``sharepoint://...`` fell back to the
    munged chunk path). Restored.
    """
    pipeline = _build_pipeline()

    refs = fetch_hybrid_search_sources(_AGENT, pipeline=pipeline, focus_signals=_FOCUS_SIGNALS)

    assert len(refs) >= 3, f"SLO is >=3 structured citations per brief; got {len(refs)}: {refs}"
    assert all(isinstance(r, SourceRef) for r in refs)

    by_path = {r.path: r for r in refs}
    # The connector chunk surfaces its CANONICAL resolvable breadcrumb …
    connector = by_path["archive/handbook.zip#1536"]
    assert connector.source_uri == "sharepoint://acme-site/handbook.zip"
    assert connector.collection == "shared"
    # … while a passthrough vault note falls back to its path (still resolvable).
    vault = by_path["notes/onboarding.md"]
    assert vault.source_uri == "notes/onboarding.md"


def test_run_brief_envelope_and_footer_carry_composed_citations() -> None:
    """run_brief (agent-facing) over the composed pipeline returns >=3
    SourceRefs in the envelope AND renders them in the ## Sources footer.

    Sabotage-proof (executed): removed ``content = content +
    render_sources_footer(sources)`` from ``run_brief`` — the
    ``"## Sources" in out.content`` assertion fired. Restored.
    """
    pipeline = _build_pipeline()
    deps = BriefDeps(
        generate_fn=lambda _agent, **_kw: "Today's focus is the deployment cutover.",
        briefing_dir_fn=lambda: None,
        config_fn=lambda: {"agents": {_AGENT: {"surfaces": [{"path": "memory/agent-alpha", "label": "memory"}]}}},
        # Compose the structured-citation seam over the FACTORY-built pipeline
        # (F47) — the same retrieval the production default runs, with fakes.
        sources_fn=lambda agent: fetch_hybrid_search_sources(agent, pipeline=pipeline, focus_signals=_FOCUS_SIGNALS),
        health_deps=_hermetic_health_deps(),
    )

    out = run_brief(_AGENT, deps=deps)

    assert out.error == ""
    # The envelope carries machine-parseable provenance — the SLO floor.
    env = brief_output_to_envelope(out)
    assert len(env["sources"]) >= 3
    assert any(s["source_uri"] == "sharepoint://acme-site/handbook.zip" for s in env["sources"])

    # The footer renders the structured citations into the brief content.
    assert "## Sources" in out.content
    assert out.content.startswith("Today's focus is the deployment cutover.")
    assert "sharepoint://acme-site/handbook.zip" in out.content
