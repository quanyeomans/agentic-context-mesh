"""Unit coverage for the folded queue-aware search seams (PLA-322).

The queue-aware search implementation used to be a second implementation in
the MCP adapter (``tool_search_queue_aware``); PLA-322 folded it onto the
search use case as ``run_search_queue_aware``. These tests exercise the
production ``_default_*`` seams the fold introduced — ``_default_flag_reader``
and ``_default_queue_search`` — through the PUBLIC surface (F5 clean), so the
fold's default wiring stays visible to the coverage floor (F7 / F86) without
reaching into private names.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kairix.core.health import HealthDeps
from kairix.core.search.intent import QueryIntent
from kairix.use_cases.search import QueueAwareSearchDeps, SearchDeps, run_search_queue_aware

pytestmark = pytest.mark.unit


def _hermetic_search_deps() -> SearchDeps:
    """A ``SearchDeps`` whose pipeline + health probes are fakes.

    Lets ``run_search`` (reached via the default queue-search delegate) run
    without touching the real pipeline / filesystem / services.
    """
    inner = SimpleNamespace(
        path="notes/deck.md#0",
        title="Deck",
        snippet="quarterly deck",
        boosted_score=0.5,
        collection="agent-alpha",
        source_uri="sharepoint://site/deck.docx",
        seq=0,
        source_page=None,
    )
    budgeted = SimpleNamespace(result=inner, content="quarterly deck", tier="vector", token_estimate=3)
    pipeline_result = SimpleNamespace(
        query="quarterly deck",
        intent="semantic",
        results=[budgeted],
        bm25_count=1,
        vec_count=0,
        fused_count=1,
        vec_failed=False,
        total_tokens=3,
        latency_ms=1.0,
        error="",
    )
    return SearchDeps(
        search_fn=lambda **_kwargs: pipeline_result,
        entity_card_fn=lambda _name: None,
        classify_fn=lambda _query: QueryIntent.SEMANTIC,
        health_deps=HealthDeps(
            secrets_loaded_fn=lambda: True,
            embed_backend_available_fn=lambda: True,
            bm25_index_available_fn=lambda: True,
            neo4j_available_fn=lambda: True,
        ),
    )


def test_default_queue_deps_off_branch_runs_use_case_and_serialises() -> None:
    """Default ``QueueAwareSearchDeps`` OFF path serialises the search envelope.

    With no ``queue_deps``, ``run_search_queue_aware`` constructs the default
    ``QueueAwareSearchDeps`` — whose ``flag_reader`` (``_default_flag_reader``)
    reads the ``agent_query_queue`` registry default (OFF). The OFF branch then
    calls the default delegate (``_default_queue_search``), which runs
    ``run_search`` and serialises it. One public call exercises both production
    seams and the default-deps construction.

    Sabotage: change ``_default_flag_reader`` to return ``True`` and the OFF
    branch is skipped — the call then routes to the real queue dispatch instead
    of returning this hermetic envelope, so ``result["query"]`` is no longer the
    fake pipeline's ``"quarterly deck"``. Restored.
    """
    result = run_search_queue_aware("any query", deps=_hermetic_search_deps())

    assert isinstance(result, dict)
    assert result["query"] == "quarterly deck"
    assert result["results"][0]["source_uri"] == "sharepoint://site/deck.docx"
    # OFF branch: the response is the plain search envelope — no carry-along key.
    assert "carry_along" not in result


def test_queue_aware_off_branch_forwards_explicit_collections_to_delegate() -> None:
    """Explicit collection scope must survive queue-aware routing when the queue flag is OFF."""
    captured: dict[str, object] = {}

    def fake_search(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"query": kwargs["query"], "results": []}

    queue_deps = QueueAwareSearchDeps(
        flag_reader=lambda _name: False,
        search_fn=fake_search,
        queue_db_factory=lambda: None,
    )

    result = run_search_queue_aware(
        "Reverse Demo Guidance",
        collections=["sharepoint"],
        queue_deps=queue_deps,
    )

    assert isinstance(result, dict)
    assert captured["collections"] == ["sharepoint"]
