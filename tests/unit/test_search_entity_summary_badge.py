"""ADR-036 §Q7 — CLI badge + MCP envelope flag for entity-summary chunks (#462 Slice D).

Locks the operator-facing contract:

* CLI ``format_text`` renders a ``[Wikidata]`` suffix on the title for
  any hit whose ``path`` starts with ``entity://``
* ``search_output_to_envelope`` carries ``entity_summary: true`` on
  the per-hit dict for the same hits
* both surfaces leave non-entity hits untouched (byte-for-byte parity)

F1/F2-clean: composes via the canonical ``SearchHit``/``SearchOutput``
dataclasses; no monkey-patching, no env-var manipulation.
"""

from __future__ import annotations

import pytest

from kairix.core.search.cli import format_text
from kairix.use_cases.search import SearchHit, SearchOutput, search_output_to_envelope

pytestmark = pytest.mark.unit


def _hit(
    *,
    path: str,
    title: str = "Some title",
    snippet: str = "snippet body",
    score: float = 0.5,
) -> SearchHit:
    return SearchHit(
        path=path,
        title=title,
        snippet=snippet,
        score=score,
        tier="search",
        tokens=10,
        collection="entity-summaries",
    )


def _output(hits: list[SearchHit]) -> SearchOutput:
    return SearchOutput(
        query="anything",
        intent="semantic",
        results=hits,
        bm25_count=0,
        vec_count=0,
        fused_count=len(hits),
        vec_failed=False,
        total_tokens=sum(h.tokens for h in hits),
        latency_ms=12.0,
    )


# ---------------------------------------------------------------------------
# CLI badge
# ---------------------------------------------------------------------------


def test_cli_badge_appears_on_entity_summary_hit() -> None:
    """A hit whose path starts with ``entity://`` carries ``[Wikidata]``
    on its title line.

    Sabotage-proof: drop the ``if hit.path.startswith("entity://")``
    branch in ``format_text`` and ``[Wikidata]`` vanishes — the
    assertion below catches.
    """
    out = _output([_hit(path="entity://Q42#0", title="Ada Lovelace Institute")])
    rendered = format_text(out)
    assert "[Wikidata]" in rendered
    assert "Ada Lovelace Institute" in rendered


def test_cli_badge_absent_on_vault_hit() -> None:
    """A vanilla vault hit gets no badge — locks byte-for-byte parity
    for non-entity rows so the operator's existing eye-line stays the
    same."""
    out = _output([_hit(path="notes/topic.md", title="Topic note")])
    rendered = format_text(out)
    assert "[Wikidata]" not in rendered


def test_cli_badge_renders_for_only_entity_rows_in_mixed_result() -> None:
    """When a search returns both vault + entity rows, only the entity
    row carries the badge. Locks the per-row gating."""
    out = _output(
        [
            _hit(path="notes/vault.md", title="Vault note"),
            _hit(path="entity://Q1#0", title="Entity A"),
            _hit(path="docs/another.md", title="Another doc"),
        ]
    )
    rendered = format_text(out)
    # Split into hit blocks; ``[Wikidata]`` appears exactly once.
    assert rendered.count("[Wikidata]") == 1
    # And it's on the entity row's title line, not elsewhere.
    entity_line = next((line for line in rendered.splitlines() if "Entity A" in line), "")
    assert "[Wikidata]" in entity_line


# ---------------------------------------------------------------------------
# MCP envelope flag
# ---------------------------------------------------------------------------


def test_envelope_flag_set_on_entity_summary_hit() -> None:
    """The per-hit envelope dict carries ``entity_summary: True`` for
    every hit whose path starts with ``entity://``.

    Sabotage-proof: drop the conditional spread
    ``**({"entity_summary": True} if h.path.startswith("entity://") else {})``
    in ``search_output_to_envelope`` and the assertion below catches
    (the key is missing from the dict).
    """
    envelope = search_output_to_envelope(_output([_hit(path="entity://Q42#0", title="Ada Lovelace Institute")]))
    assert envelope["results"][0]["entity_summary"] is True


def test_envelope_flag_absent_on_vault_hit() -> None:
    """Non-entity hits don't carry the flag at all (the key is omitted,
    not set to False) — locks the additive shape so existing agents
    that don't know about ``entity_summary`` see byte-identical
    envelopes for their familiar rows."""
    envelope = search_output_to_envelope(_output([_hit(path="notes/topic.md", title="Topic note")]))
    assert "entity_summary" not in envelope["results"][0]


def test_envelope_flag_per_row_gating_in_mixed_result() -> None:
    """Mixed vault + entity hits → only the entity row's dict carries
    the flag; vault rows stay unchanged."""
    envelope = search_output_to_envelope(
        _output(
            [
                _hit(path="notes/vault.md", title="Vault note"),
                _hit(path="entity://Q1#0", title="Entity A"),
                _hit(path="docs/another.md", title="Another doc"),
            ]
        )
    )
    rows = envelope["results"]
    assert "entity_summary" not in rows[0]
    assert rows[1]["entity_summary"] is True
    assert "entity_summary" not in rows[2]
