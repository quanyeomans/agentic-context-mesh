"""Contract: ``SearchOutput`` <-> envelope round-trip preserves rendered text.

PR 2.2 / #421 — warm-MCP text-mode routing for ``kairix search``.

After this PR the CLI dispatcher can route ``kairix search "query"`` to
a warm MCP worker even when ``--json`` is not on argv. The dispatcher
receives a JSON envelope (the same dict ``tool_search`` returns); to
render the operator-facing text it converts envelope ->
``SearchOutput`` via ``SearchOutput.from_envelope`` and calls the
existing ``format_text``. That seam MUST produce byte-identical text
to the in-process path — otherwise warm-MCP routing silently changes
operator output.

This contract pins that round-trip at the byte level for every relevant
shape (empty results, multiple hits, hits with snippets / metadata, an
ENTITY-card prepended hit, and the error-envelope branch). Production
callers never construct ``SearchOutput`` from a dict directly; the
test goes through the public surface
(``search_output_to_envelope`` + ``SearchOutput.from_envelope``) so the
contract documents the supported shape and breaks loudly when either
side drifts.
"""

from __future__ import annotations

import pytest

from kairix.core.search.cli import format_text
from kairix.use_cases.search import SearchHit, SearchOutput, search_output_to_envelope

pytestmark = pytest.mark.contract


def _roundtrip(out: SearchOutput) -> SearchOutput:
    """Project ``out`` to the envelope dict and rebuild via ``from_envelope``."""
    envelope = search_output_to_envelope(out)
    return SearchOutput.from_envelope(envelope)


# Sabotage-proof (executed): mutated ``SearchHit.from_envelope`` to drop
# the ``snippet`` key (hard-coded ""); the multi-hit byte-equality
# assertion fired because format_text writes the snippet line per hit.
# Restored.
def test_roundtrip_preserves_text_with_multiple_hits() -> None:
    original = SearchOutput(
        query="agent-alpha quarterly review",
        intent="semantic",
        results=[
            SearchHit(
                path="/vault/agent-alpha/notes/q1.md",
                title="Q1 Review",
                snippet="Outcomes for the quarter — focus on agent-alpha throughput.",
                score=0.91,
                tier="vector",
                tokens=12,
                collection="agent-alpha",
            ),
            SearchHit(
                path="/vault/shared/playbooks/review.md",
                title="Quarterly Review Playbook",
                snippet="Steps the team runs every quarter to align on outcomes.",
                score=0.74,
                tier="bm25",
                tokens=11,
                collection="shared",
            ),
            SearchHit(
                path="/vault/agent-beta/notes/q1.md",
                title="agent-beta Q1",
                snippet="Goals and blockers from the agent-beta angle.",
                score=0.55,
                tier="vector",
                tokens=8,
                collection="agent-beta",
            ),
        ],
        bm25_count=2,
        vec_count=3,
        fused_count=3,
        total_tokens=31,
        latency_ms=42.5,
    )
    rebuilt = _roundtrip(original)
    assert format_text(original) == format_text(rebuilt)


# Sabotage-proof (executed): made ``SearchOutput.from_envelope`` return
# ``results=[]`` regardless of input; multi-hit equality fired because
# the rebuilt output rendered "No results found." while the original
# rendered three numbered hits. Restored.
def test_roundtrip_preserves_text_with_empty_results() -> None:
    original = SearchOutput(
        query="no-match query agent-alpha",
        intent="semantic",
        results=[],
        bm25_count=0,
        vec_count=0,
        fused_count=0,
        total_tokens=0,
        latency_ms=3.2,
    )
    rebuilt = _roundtrip(original)
    rendered_original = format_text(original)
    rendered_rebuilt = format_text(rebuilt)
    assert rendered_original == rendered_rebuilt
    # Anchor the empty-results branch — both renders must hit the
    # "No results found." footer.
    assert "No results found." in rendered_rebuilt


# Sabotage-proof (executed): mutated ``SearchOutput.from_envelope`` to
# set ``vec_failed=not bool(envelope.get("vec_failed", False))``
# (negating the round-tripped value); the diagnostics line emitted by
# format_text now flipped the ``vec_failed=True`` token relative to
# the original — multi-test equality fired. Restored.
def test_roundtrip_preserves_text_with_vec_failed_diagnostic() -> None:
    original = SearchOutput(
        query="vector-down query",
        intent="keyword",
        results=[
            SearchHit(
                path="/vault/agent-alpha/scratch/notes.md",
                title="Scratch notes",
                snippet="BM25-only result because the vector backend errored.",
                score=0.42,
                tier="bm25",
                tokens=10,
                collection="agent-alpha",
            ),
        ],
        bm25_count=1,
        vec_count=0,
        fused_count=1,
        vec_failed=True,
        total_tokens=10,
        latency_ms=150.0,
    )
    rebuilt = _roundtrip(original)
    rendered_rebuilt = format_text(rebuilt)
    assert format_text(original) == rendered_rebuilt
    assert "vec_failed=True" in rendered_rebuilt


# Sabotage-proof (executed): dropped the ``error`` key extraction from
# ``SearchOutput.from_envelope`` (defaulted to ""); the error-branch
# assertion fired because the rebuilt envelope rendered the diagnostics
# line instead of the "Error: ..." line. Restored.
def test_roundtrip_preserves_text_with_error_envelope() -> None:
    original = SearchOutput(
        query="provider misconfigured query",
        intent="",
        results=[],
        error="ValueError: provider 'azure' not registered",
    )
    rebuilt = _roundtrip(original)
    rendered_original = format_text(original)
    rendered_rebuilt = format_text(rebuilt)
    assert rendered_original == rendered_rebuilt
    # The error branch short-circuits after emitting the Error: line —
    # both renders must carry it.
    assert "Error: ValueError: provider 'azure' not registered" in rendered_rebuilt


# Sabotage-proof (executed): mutated ``SearchHit.from_envelope`` to
# coerce ``source`` to "" unconditionally; the entity-card branch in
# the envelope round-trip dropped the ``source`` flag and the entity-card
# dict it gates, but format_text still rendered the title — the byte-equality
# still passed (format_text doesn't render ``source`` directly), so the
# assertion that hardened this was on the rebuilt hit's ``source`` attribute
# (which is what downstream entity-aware consumers read). Reverted.
def test_roundtrip_preserves_entity_card_hit_source_and_entity_dict() -> None:
    original = SearchOutput(
        query="what is project-orion",
        intent="entity",
        results=[
            SearchHit(
                path="/vault/entities/project-orion.md",
                title="project-orion",
                snippet="An internal initiative for agent-alpha throughput.",
                score=1.0,
                tier="",
                tokens=12,
                collection="",
                source="entity_graph",
                entity={"id": "ent-1", "name": "project-orion", "type": "project"},
            ),
            SearchHit(
                path="/vault/shared/launch.md",
                title="Launch notes",
                snippet="Background on the project-orion launch sequence.",
                score=0.66,
                tier="bm25",
                tokens=9,
                collection="shared",
            ),
        ],
        bm25_count=1,
        vec_count=1,
        fused_count=2,
        total_tokens=21,
        latency_ms=27.4,
    )
    rebuilt = _roundtrip(original)
    assert format_text(original) == format_text(rebuilt)
    # The entity-card hit's structural fields must survive — downstream
    # consumers (CLI rendering, agent UIs) read ``source`` to recognise
    # the entity-graph hit and ``entity`` for the card payload.
    assert rebuilt.results[0].source == "entity_graph"
    assert rebuilt.results[0].entity == {"id": "ent-1", "name": "project-orion", "type": "project"}


# Sabotage-proof (executed): mutated ``SearchOutput.from_envelope`` to
# coerce ``intent`` to "" regardless of input; format_text emits
# ``Intent: <value>`` as the second line — the rebuilt render now
# emitted ``Intent: `` (empty) while the original kept the intent
# string, and the equality assertion fired. Restored.
def test_roundtrip_preserves_diagnostics_line_fields() -> None:
    original = SearchOutput(
        query="diagnostics-line query",
        intent="semantic",
        results=[
            SearchHit(
                path="/vault/notes.md",
                title="Notes",
                snippet="A short note.",
                score=0.5,
                tier="vector",
                tokens=3,
                collection="shared",
            ),
        ],
        bm25_count=4,
        vec_count=7,
        fused_count=5,
        total_tokens=33,
        latency_ms=123.4,
    )
    rebuilt = _roundtrip(original)
    rendered_rebuilt = format_text(rebuilt)
    assert format_text(original) == rendered_rebuilt
    # Anchor every diagnostic field on the rebuilt render — these are
    # the operator-visible counts the warm-MCP path must carry through.
    assert "Intent: semantic" in rendered_rebuilt
    assert "Results: 1 returned" in rendered_rebuilt
    assert "BM25=4" in rendered_rebuilt
    assert "vec=7" in rendered_rebuilt
    assert "33 tokens" in rendered_rebuilt
    assert "123ms" in rendered_rebuilt
