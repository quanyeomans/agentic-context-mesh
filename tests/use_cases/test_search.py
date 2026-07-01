"""Unit tests for ``kairix.use_cases.search.run_search``.

Drives the use case through SearchDeps injection — no @patch, no
monkeypatch. Pinning the contract that closes #168 Phase 2 drift:
same use case drives both the CLI's ``kairix search`` and the MCP's
``tool_search``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pytest

from kairix.core.health import HealthDeps
from kairix.core.search.intent import QueryIntent
from kairix.core.search.scope import Scope
from kairix.use_cases.search import (
    SearchDeps,
    SearchHit,
    SearchOutput,
    SearchSeamSignatureError,
    run_search,
    search_output_to_envelope,
)


def _healthy_health_deps() -> HealthDeps:
    return HealthDeps(
        secrets_loaded_fn=lambda: True,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )


def _degraded_vector_health_deps() -> HealthDeps:
    """Vector search offline; BM25 still up — the canonical fallback case."""
    return HealthDeps(
        secrets_loaded_fn=lambda: False,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeInner:
    path: str = ""
    title: str = ""
    snippet: str = ""
    boosted_score: float = 0.0
    collection: str = ""
    seq: int | None = None


@dataclass
class _FakeBudgeted:
    result: _FakeInner
    content: str = ""
    tier: str = ""
    token_estimate: int = 0


@dataclass
class _FakeSearchResult:
    query: str = ""
    intent: Any = QueryIntent.SEMANTIC
    results: list[_FakeBudgeted] = field(default_factory=list)
    bm25_count: int = 0
    vec_count: int = 0
    fused_count: int = 0
    vec_failed: bool = False
    total_tokens: int = 0
    latency_ms: float = 0.0
    error: str = ""


def _build_deps(
    *,
    sr: _FakeSearchResult | None = None,
    classify: Any = None,
    card: dict[str, Any] | None = None,
    search_raises: bool = False,
    classify_raises: bool = False,
    card_raises: bool = False,
) -> tuple[SearchDeps, dict[str, list[Any]]]:
    captured: dict[str, list[Any]] = {"search": [], "classify": [], "card": []}

    def fake_search(**kwargs: Any) -> _FakeSearchResult:
        captured["search"].append(kwargs)
        if search_raises:
            raise RuntimeError("search boom")
        return sr or _FakeSearchResult()

    def fake_classify(query: str) -> QueryIntent:
        captured["classify"].append(query)
        if classify_raises:
            raise RuntimeError("classify boom")
        return classify if classify is not None else QueryIntent.SEMANTIC

    def fake_card(name: str) -> dict[str, Any] | None:
        captured["card"].append(name)
        if card_raises:
            raise RuntimeError("card boom")
        return card

    return SearchDeps(search_fn=fake_search, classify_fn=fake_classify, entity_card_fn=fake_card), captured


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_search_hit_default_optional_fields() -> None:
    h = SearchHit(path="p", title="t", snippet="s", score=0.5)
    assert h.tier == ""
    assert h.tokens == 0
    assert h.collection == ""
    assert h.source == ""
    assert h.entity == {}
    # PLA-270 — chunk seq defaults to None for non-chunked rows.
    assert h.seq is None


@pytest.mark.unit
def test_chunk_seq_projected_from_fused_result_into_hit() -> None:
    """PLA-270 — ``_budgeted_to_hit`` lifts the typed ``seq`` off the fused result."""
    inner = _FakeInner(path="m365://doc.pdf#5", seq=5)
    sr = _FakeSearchResult(results=[_FakeBudgeted(result=inner, content="c")])
    deps, _ = _build_deps(sr=sr)
    out = run_search("q", deps=deps)
    assert out.results[0].seq == 5


@pytest.mark.unit
def test_chunk_seq_zero_is_preserved_through_projection() -> None:
    """seq=0 is a real first-chunk index, not a falsy 'missing'."""
    inner = _FakeInner(path="m365://doc.pdf#0", seq=0)
    sr = _FakeSearchResult(results=[_FakeBudgeted(result=inner, content="c")])
    deps, _ = _build_deps(sr=sr)
    out = run_search("q", deps=deps)
    assert out.results[0].seq == 0


@pytest.mark.unit
def test_chunk_seq_round_trips_through_envelope() -> None:
    """PLA-270 — ``seq`` survives the MCP envelope writer→reader round-trip."""
    hit = SearchHit(path="m365://doc.pdf#3", title="t", snippet="s", score=0.5, seq=3)
    out = SearchOutput(query="q", intent="semantic", results=[hit])
    env = search_output_to_envelope(out)
    assert env["results"][0]["seq"] == 3
    rebuilt = SearchOutput.from_envelope(env)
    assert rebuilt.results[0].seq == 3


@pytest.mark.unit
def test_chunk_seq_absent_from_envelope_rebuilds_as_none() -> None:
    """A row with no ``seq`` key rebuilds with ``seq=None`` (legacy-worker tolerant)."""
    rebuilt = SearchHit.from_envelope({"path": "notes/x.md", "score": 0.1})
    assert rebuilt.seq is None


@pytest.mark.unit
def test_search_output_default_results_is_empty_list() -> None:
    out = SearchOutput(query="q", intent="semantic")
    assert out.results == []
    assert out.error == ""
    assert out.bm25_count == 0
    assert out.vec_failed is False


# ---------------------------------------------------------------------------
# Happy path: pipeline produces hits, projection lifts every field.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pipeline_results_projected_into_search_hits() -> None:
    inner = _FakeInner(
        path="docs/note.md", title="Note", snippet="raw snippet", boosted_score=0.85, collection="shared"
    )
    budgeted = _FakeBudgeted(result=inner, content="boundary-trimmed snippet", tier="L1", token_estimate=42)
    sr = _FakeSearchResult(
        query="q",
        intent=QueryIntent.SEMANTIC,
        results=[budgeted],
        bm25_count=8,
        vec_count=12,
        fused_count=15,
        total_tokens=42,
        latency_ms=125.5,
    )
    deps, _ = _build_deps(sr=sr)

    out = run_search("q", deps=deps)

    assert out.query == "q"
    assert out.intent == "semantic"
    assert out.bm25_count == 8
    assert out.vec_count == 12
    assert out.fused_count == 15
    assert out.total_tokens == 42
    assert out.latency_ms == pytest.approx(125.5)
    assert out.error == ""
    assert len(out.results) == 1
    h = out.results[0]
    assert h.path == "docs/note.md"
    assert h.title == "Note"
    # boundary-trimmed content takes precedence over inner.snippet
    assert h.snippet == "boundary-trimmed snippet"
    assert h.score == pytest.approx(0.85)
    assert h.tier == "L1"
    assert h.tokens == 42
    assert h.collection == "shared"
    assert h.source == ""  # not an entity-graph card


@pytest.mark.unit
def test_results_truncated_to_limit() -> None:
    big = [_FakeBudgeted(result=_FakeInner(path=f"/p{i}"), content="") for i in range(25)]
    deps, _ = _build_deps(sr=_FakeSearchResult(results=big))
    out = run_search("q", limit=7, deps=deps)
    assert len(out.results) == 7


# ---------------------------------------------------------------------------
# Budget inference: explicit non-default wins; entity/keyword shrinks; "research" expands.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_budget_explicit_non_default_passed_through_unchanged() -> None:
    deps, captured = _build_deps()
    run_search("anything", budget=999, deps=deps)
    assert captured["search"][0]["budget"] == 999


@pytest.mark.unit
def test_budget_default_3000_with_entity_intent_drops_to_1500() -> None:
    deps, captured = _build_deps(classify=QueryIntent.ENTITY)
    run_search("who is Acme", deps=deps)
    assert captured["search"][0]["budget"] == 1500


@pytest.mark.unit
def test_budget_default_3000_with_keyword_intent_drops_to_1500() -> None:
    deps, captured = _build_deps(classify=QueryIntent.KEYWORD)
    run_search("token", deps=deps)
    assert captured["search"][0]["budget"] == 1500


@pytest.mark.unit
def test_budget_default_3000_with_research_keyword_expands_to_5000() -> None:
    deps, captured = _build_deps(classify=QueryIntent.SEMANTIC)
    run_search("research the topic", deps=deps)
    assert captured["search"][0]["budget"] == 5000


@pytest.mark.unit
def test_budget_default_3000_no_special_signals_stays_3000() -> None:
    deps, captured = _build_deps(classify=QueryIntent.SEMANTIC)
    run_search("ordinary query", deps=deps)
    assert captured["search"][0]["budget"] == 3000


@pytest.mark.unit
def test_classify_failure_falls_through_to_heuristic() -> None:
    """A classify exception must not crash; non-research queries stay at 3000."""
    deps, captured = _build_deps(classify_raises=True)
    run_search("ordinary query", deps=deps)
    assert captured["search"][0]["budget"] == 3000


@pytest.mark.unit
def test_query_classified_once_and_intent_threaded_into_search() -> None:
    """run_search classifies the query EXACTLY ONCE and threads that single
    intent into the search call, so the pipeline reuses it instead of
    re-classifying the same query (PLA-273 warm-path dedup).

    Sabotage (executed): (1) revert ``_infer_budget`` to take + call the
    ``classify_fn`` again and the recording classifier fires twice, so
    ``len(captured["classify"]) == 1`` fails; (2) drop the ``intent=``
    kwarg on the ``deps.search_fn`` call and ``captured["search"][0]``
    has no ``intent`` key, so the second assertion KeyErrors.
    """
    deps, captured = _build_deps(classify=QueryIntent.ENTITY)

    run_search("who is Acme", deps=deps)

    # The recording classifier seam fired exactly once for the request.
    assert len(captured["classify"]) == 1
    # That single classification threaded into the search call.
    assert captured["search"][0]["intent"] == QueryIntent.ENTITY


@pytest.mark.unit
def test_failed_classification_threads_none_intent_into_search(caplog: pytest.LogCaptureFixture) -> None:
    """When classification fails, run_search threads ``intent=None`` so the
    pipeline falls back to classifying internally (safe default), and the
    failure is logged WITH the traceback attached (``exc_info=True``) so an
    operator can see why classification fell back.

    Sabotage: (1) thread a non-None default intent on the failure path and
    the pipeline would skip its own (correct) classification; (2) flip the
    classify-failure log's ``exc_info=True`` to ``False`` and the captured
    record carries no traceback, failing the ``exc_info is not None`` assert.
    """
    deps, captured = _build_deps(classify_raises=True)

    with caplog.at_level(logging.DEBUG, logger="kairix.use_cases.search"):
        run_search("ordinary query", deps=deps)

    assert captured["search"][0]["intent"] is None
    # The classify-failure debug log attaches the exception traceback.
    # exc_info=True populates record.exc_info with the (type, value, tb)
    # tuple; exc_info=False leaves it the literal ``False`` — so assert the
    # tuple shape, not ``is not None`` (which ``False`` also satisfies).
    failure_records = [r for r in caplog.records if "intent classification failed" in r.getMessage()]
    assert failure_records, "expected a classify-failure debug log"
    assert isinstance(failure_records[0].exc_info, tuple)


@pytest.mark.unit
def test_health_probe_deduped_across_repeated_searches() -> None:
    """run_search routes health through the shared TTL cache, so a repeat
    search within the TTL window doesn't re-run the 4-probe fan-out per
    request (PLA-273). The ``_reset_workstream_b_caches`` conftest fixture
    leaves the cache cold at the start of each test, and two adjacent
    run_search calls land far inside the 10s TTL.

    Sabotage (executed): change ``cached_probe_health`` back to a direct
    ``probe_health`` call in run_search — the second search re-probes and
    the secrets-probe count climbs to 2.
    """
    counts = {"secrets": 0}

    def _counting_secrets() -> bool:
        counts["secrets"] += 1
        return True

    health_deps = HealthDeps(
        secrets_loaded_fn=_counting_secrets,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )
    base, _ = _build_deps()
    deps = SearchDeps(
        search_fn=base.search_fn,
        entity_card_fn=base.entity_card_fn,
        classify_fn=base.classify_fn,
        health_deps=health_deps,
    )

    run_search("q", deps=deps)  # cold cache → probes run once
    run_search("q", deps=deps)  # within TTL → cache hit, no re-probe

    assert counts["secrets"] == 1


# ---------------------------------------------------------------------------
# Entity-graph augmentation: ENTITY intent + card present → prepended.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_entity_card_prepended_when_entity_intent_and_card_found() -> None:
    sr = _FakeSearchResult(
        intent=QueryIntent.ENTITY,
        results=[_FakeBudgeted(result=_FakeInner(path="other.md"), content="other")],
    )
    card = {
        "id": "acme",
        "name": "Acme",
        "type": "Organisation",
        "summary": "Acme is a client engagement.",
        "vault_path": "02-Areas/00-Clients/Acme/Acme.md",
    }
    deps, captured = _build_deps(sr=sr, classify=QueryIntent.ENTITY, card=card)

    out = run_search("who is Acme", deps=deps)

    assert len(out.results) == 2
    # First hit is the entity card.
    first = out.results[0]
    assert first.source == "entity_graph"
    assert first.score == pytest.approx(1.0)
    assert first.path == "02-Areas/00-Clients/Acme/Acme.md"
    assert first.title == "Acme"
    assert first.entity == {"id": "acme", "name": "Acme", "type": "Organisation"}
    # Lookup happened against the de-prefixed name.
    assert captured["card"] == ["Acme"]


@pytest.mark.unit
def test_entity_card_skipped_when_include_entity_card_false() -> None:
    sr = _FakeSearchResult(intent=QueryIntent.ENTITY, results=[])
    deps, captured = _build_deps(sr=sr, classify=QueryIntent.ENTITY, card={"id": "x"})
    run_search("who is Acme", include_entity_card=False, deps=deps)
    assert captured["card"] == []


@pytest.mark.unit
def test_entity_card_skipped_for_non_entity_intent() -> None:
    sr = _FakeSearchResult(intent=QueryIntent.SEMANTIC, results=[])
    deps, captured = _build_deps(sr=sr, classify=QueryIntent.SEMANTIC, card={"id": "x"})
    run_search("a question", deps=deps)
    assert captured["card"] == []


@pytest.mark.unit
def test_entity_card_lookup_failure_does_not_break_search() -> None:
    sr = _FakeSearchResult(intent=QueryIntent.ENTITY, results=[])
    deps, _ = _build_deps(sr=sr, classify=QueryIntent.ENTITY, card_raises=True)
    out = run_search("who is Acme", deps=deps)
    assert out.error == ""
    assert out.results == []


@pytest.mark.unit
def test_entity_card_missing_query_name_skips_lookup() -> None:
    sr = _FakeSearchResult(intent=QueryIntent.ENTITY, results=[])
    deps, captured = _build_deps(sr=sr, classify=QueryIntent.ENTITY, card={"id": "x"})
    # Empty query → empty extracted name → no lookup
    run_search("", deps=deps)
    assert captured["card"] == []


# ---------------------------------------------------------------------------
# Result projection edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_budgeted_with_none_inner_yields_empty_hit() -> None:
    bad = _FakeBudgeted(result=None, content="x")  # type: ignore[arg-type]  # exercising malformed-shape tolerance
    deps, _ = _build_deps(sr=_FakeSearchResult(results=[bad]))
    out = run_search("q", deps=deps)
    assert out.results[0].path == ""
    assert out.results[0].score == pytest.approx(0.0)


@pytest.mark.unit
def test_inner_snippet_used_when_content_empty() -> None:
    inner = _FakeInner(path="/p", snippet="from inner", boosted_score=0.5)
    bad = _FakeBudgeted(result=inner, content="")  # adapter falls back to inner.snippet
    deps, _ = _build_deps(sr=_FakeSearchResult(results=[bad]))
    out = run_search("q", deps=deps)
    assert out.results[0].snippet == "from inner"


# ---------------------------------------------------------------------------
# Error path: top-level failure populates envelope.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_search_pipeline_failure_yields_error_envelope() -> None:
    deps, _ = _build_deps(search_raises=True)
    out = run_search("q", deps=deps)
    assert out.error.startswith("RuntimeError:")
    assert out.results == []
    assert out.intent == ""


# ---------------------------------------------------------------------------
# PLA-281: a DI-seam signature mismatch must fail LOUDLY, not swallow into an
# empty result envelope. This is the exact swallow that let a fake/real
# search_fn signature drift go green locally and red in CI Stage 3.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_search_fn_signature_mismatch_raises_instead_of_swallowing() -> None:
    """A search_fn whose signature can't bind the seam kwargs raises loudly.

    The seam passes max_tier=; a search_fn that never declared it (and has no
    **kwargs) raises a binding TypeError before its body runs. run_search must
    surface that as SearchSeamSignatureError rather than returning empty
    results with error set.
    """

    def narrow_search(*, query: str) -> Any:  # missing agent/scope/... — binding fails
        raise AssertionError("body must never run — binding fails first")

    deps = SearchDeps(search_fn=narrow_search)
    with pytest.raises(SearchSeamSignatureError):
        run_search("q", deps=deps)


@pytest.mark.unit
def test_runtime_typeerror_inside_search_fn_is_still_swallowed() -> None:
    """A TypeError raised INSIDE search_fn's body is a genuine runtime failure.

    It descends past the seam call frame, so it is NOT a signature mismatch and
    stays swallowed into the error envelope — the broad except still handles a
    real runtime fault gracefully.
    """

    def exploding_search(**kwargs: Any) -> Any:
        raise TypeError("runtime boom inside body")

    deps = SearchDeps(search_fn=exploding_search)
    out = run_search("q", deps=deps)
    assert out.error.startswith("TypeError:")
    assert out.results == []
    assert out.intent == ""


# ---------------------------------------------------------------------------
# Pass-through: every adapter param reaches the underlying search call.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_agent_and_scope_pass_through() -> None:
    deps, captured = _build_deps()
    run_search("q", agent="builder", scope=Scope.AGENT, deps=deps)
    call = captured["search"][0]
    assert call["agent"] == "builder"
    assert call["scope"] is Scope.AGENT


@pytest.mark.unit
def test_default_scope_is_shared_agent() -> None:
    deps, captured = _build_deps()
    run_search("q", deps=deps)
    assert captured["search"][0]["scope"] is Scope.SHARED_AGENT


# ---------------------------------------------------------------------------
# W3: health envelope on every search response (#246)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_healthy_state_search_carries_clean_health_field() -> None:
    deps, _ = _build_deps(sr=_FakeSearchResult(query="q"))
    deps = SearchDeps(
        search_fn=deps.search_fn,
        classify_fn=deps.classify_fn,
        entity_card_fn=deps.entity_card_fn,
        health_deps=_healthy_health_deps(),
    )
    out = run_search("q", deps=deps)
    assert out.health.vector_search == "ok"
    assert out.health.bm25 == "ok"
    assert out.health.chat == "ok"
    assert out.health.degraded_reason == ""
    assert out.health.next_action == ""


@pytest.mark.unit
def test_degraded_vector_returns_bm25_results_with_prescriptive_next_action() -> None:
    """W3 contract: when vector search is offline kairix still returns
    BM25 results AND tells the agent what to do.

    Sabotage anchor: dropping ``next_action`` from the degraded branch
    of ``search_next_action`` makes this test fail on the
    ``next_action`` assertion."""
    inner = _FakeInner(path="docs/note.md", title="Note", snippet="bm25 result", boosted_score=0.5)
    budgeted = _FakeBudgeted(result=inner, content="bm25 result", tier="L1")
    sr = _FakeSearchResult(query="q", results=[budgeted], bm25_count=1)
    deps, _ = _build_deps(sr=sr)
    deps = SearchDeps(
        search_fn=deps.search_fn,
        classify_fn=deps.classify_fn,
        entity_card_fn=deps.entity_card_fn,
        health_deps=_degraded_vector_health_deps(),
    )

    out = run_search("q", deps=deps)

    # Results still flow from the working subsystem (BM25-only).
    assert len(out.results) == 1
    assert out.results[0].snippet == "bm25 result"
    # Health surfaces the degradation.
    assert out.health.vector_search == "degraded"
    assert out.health.chat == "offline"
    assert out.health.bm25 == "ok"
    assert out.health.degraded_reason != ""
    # Prescriptive, agent-actionable: points at admin remediation + names the fallback.
    assert out.health.next_action != ""
    assert "BM25-only" in out.health.next_action
    assert "kairix onboard check" in out.health.next_action


@pytest.mark.unit
def test_search_envelope_includes_health_dict() -> None:
    """The MCP/CLI envelope projection must carry the health snapshot."""
    out = SearchOutput(query="q", intent="semantic")
    env = search_output_to_envelope(out)
    assert "health" in env
    assert env["health"]["vector_search"] == "ok"
    assert env["health"]["next_action"] == ""


@pytest.mark.unit
def test_every_degraded_search_response_carries_a_next_action() -> None:
    """Sabotage anchor: removing the directive in any degraded branch
    breaks this iteration."""
    for secrets, embed, bm25 in (
        (False, True, True),
        (True, False, True),
        (True, True, False),
        (False, False, True),
        (False, True, False),
        (True, False, False),
        (False, False, False),
    ):
        hd = HealthDeps(
            secrets_loaded_fn=lambda s=secrets: s,
            embed_backend_available_fn=lambda e=embed: e,
            bm25_index_available_fn=lambda b=bm25: b,
            neo4j_available_fn=lambda: True,
        )
        deps, _ = _build_deps()
        deps = SearchDeps(
            search_fn=deps.search_fn,
            classify_fn=deps.classify_fn,
            entity_card_fn=deps.entity_card_fn,
            health_deps=hd,
        )
        out = run_search("q", deps=deps)
        assert out.health.next_action != "", (
            f"search envelope dropped next_action for secrets={secrets} embed={embed} bm25={bm25}"
        )
