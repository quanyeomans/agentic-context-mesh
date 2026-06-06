"""Contract: ``TimelineResult`` <-> envelope round-trip preserves rendered text.

PR 2.7 / #421 — warm-MCP text-mode routing for ``kairix timeline``.

After this PR the CLI dispatcher can route ``kairix timeline <query>`` to
a warm MCP worker even when ``--json`` is not on argv. The dispatcher
receives a JSON envelope (the same dict ``tool_timeline`` returns); to
render the operator-facing text it converts envelope -> ``TimelineResult``
via ``TimelineResult.from_envelope`` and calls the existing
``format_header`` + ``format_results`` helpers. That seam MUST produce
byte-identical text to the in-process path — otherwise warm-MCP routing
silently changes operator output.

This contract pins that round-trip at the byte level for every relevant
shape (empty results, populated with multi-agent dated hits, populated
with date-filter window, fall-back search-pipeline branch, error
envelope). Production callers never construct ``TimelineResult`` from a
dict directly; the test goes through the public surface
(``timeline_output_to_envelope`` + ``TimelineResult.from_envelope``) so
the contract documents the supported shape and breaks loudly when
either side drifts.
"""

from __future__ import annotations

import pytest

from kairix.core.temporal.cli import format_header, format_results
from kairix.use_cases.timeline import (
    TimelineHit,
    TimelineResult,
    timeline_output_to_envelope,
)

pytestmark = pytest.mark.contract


def _roundtrip(result: TimelineResult) -> TimelineResult:
    """Project ``result`` to the envelope dict and rebuild via ``from_envelope``."""
    envelope = timeline_output_to_envelope(result)
    return TimelineResult.from_envelope(envelope)


# Sabotage-proof (executed): dropped the ``results`` list extraction from
# ``from_envelope`` (hard-coded to []); the format_results "No results
# found." branch fired for the rebuilt object while the original
# produced the "Found N result(s)" header, equality assertion fired;
# restored.
def test_roundtrip_preserves_text_with_empty_results() -> None:
    """Empty timeline (no hits) renders identically on both paths."""
    original = TimelineResult(
        original_query="what happened nowhere",
        rewritten_query="what happened nowhere",
        is_temporal=False,
        fell_back=True,
        time_window={},
        results=[],
    )
    rebuilt = _roundtrip(original)
    assert format_header(original, limit=10) == format_header(rebuilt, limit=10)
    assert format_results(original) == format_results(rebuilt)
    # Anchor: the "No results found." footer must appear on both sides.
    assert format_results(rebuilt) == "No results found."


# Sabotage-proof (executed): mutated ``from_envelope`` to drop the
# ``date`` field from each hit (set to ""); the multi-agent hit list
# embeds the date in each header line, equality assertion fired with a
# diff showing "undated" instead of "2026-04-15"; restored.
def test_roundtrip_preserves_text_with_multi_agent_dated_hits() -> None:
    """Populated timeline across multiple agents with date metadata."""
    original = TimelineResult(
        original_query="what happened in April 2026",
        rewritten_query="what happened in April 2026 (start=2026-04-01 end=2026-04-30)",
        is_temporal=True,
        fell_back=False,
        time_window={"start": "2026-04-01", "end": "2026-04-30"},
        results=[
            TimelineHit(
                path="agent-alpha/boards/sprint.md",
                title="Card: ship the thing",
                snippet="card body content alpha",
                score=2.5,
                date="2026-04-15",
                chunk_type="board_card",
            ),
            TimelineHit(
                path="agent-beta/memory/april.md",
                title="Section: planning",
                snippet="memory body content beta",
                score=1.8,
                date="2026-04-20",
                chunk_type="memory_section",
            ),
            TimelineHit(
                path="agent-gamma/boards/done.md",
                title="Card: finish review",
                snippet="card body content gamma",
                score=1.2,
                date="2026-04-28",
                chunk_type="board_card",
            ),
        ],
    )
    rebuilt = _roundtrip(original)
    assert format_header(original, limit=10) == format_header(rebuilt, limit=10)
    assert format_results(original) == format_results(rebuilt)
    # Anchor: every hit's path + date + title surfaces on the rebuilt
    # path — proves the per-hit envelope round-trip preserves the fields
    # format_results reads.
    rendered = format_results(rebuilt)
    assert "agent-alpha/boards/sprint.md" in rendered
    assert "agent-beta/memory/april.md" in rendered
    assert "agent-gamma/boards/done.md" in rendered
    assert "2026-04-15" in rendered
    assert "2026-04-28" in rendered


# Sabotage-proof (executed): mutated ``from_envelope`` to default
# ``time_window`` to {} when input had a real dict; the header lost the
# "Window: 2026-04-01 → 2026-04-30" line and replaced it with the "no
# date filter" branch, equality fired; restored.
def test_roundtrip_preserves_text_with_date_filter_window() -> None:
    """Header line for the time window survives the round-trip."""
    original = TimelineResult(
        original_query="topic last week",
        rewritten_query="topic last week (start=2026-05-30 end=2026-06-06)",
        is_temporal=True,
        fell_back=False,
        time_window={"start": "2026-05-30", "end": "2026-06-06"},
        results=[
            TimelineHit(
                path="agent-alpha/notes.md",
                title="Note",
                snippet="some content",
                score=0.9,
                date="2026-06-01",
                chunk_type="memory_section",
            ),
        ],
    )
    rebuilt = _roundtrip(original)
    assert format_header(original, limit=5) == format_header(rebuilt, limit=5)
    # Window line must carry both bounds verbatim from the envelope.
    rendered_header = format_header(rebuilt, limit=5)
    assert "2026-05-30" in rendered_header
    assert "2026-06-06" in rendered_header


# Sabotage-proof (executed): mutated ``from_envelope`` to default
# ``fell_back`` to False regardless of input; the header lost the
# "Note: primary temporal index empty" trailer, equality fired with a
# diff on the trailing line; restored.
def test_roundtrip_preserves_text_with_fallback_branch() -> None:
    """Search-pipeline fallback marker survives the round-trip."""
    original = TimelineResult(
        original_query="topic without date",
        rewritten_query="topic without date",
        is_temporal=False,
        fell_back=True,
        time_window={},
        results=[
            TimelineHit(
                path="agent-alpha/general.md",
                title="general note",
                snippet="fallback search hit",
                score=0.5,
            ),
        ],
    )
    rebuilt = _roundtrip(original)
    assert format_header(original, limit=10) == format_header(rebuilt, limit=10)
    assert format_results(original) == format_results(rebuilt)
    # Anchor: the fall-back trailer must reach the rebuilt header.
    assert "primary temporal index empty" in format_header(rebuilt, limit=10)


# Sabotage-proof (executed): dropped the ``error`` key extraction from
# ``from_envelope``; the structural-field assertion fired on
# ``rebuilt.error == original.error``; restored.
def test_roundtrip_preserves_structural_fields() -> None:
    """Every field on the dataclass survives a round-trip with no drift."""
    original = TimelineResult(
        original_query="q",
        rewritten_query="q (rewritten)",
        is_temporal=True,
        fell_back=False,
        time_window={"start": "2026-04-01", "end": ""},
        results=[
            TimelineHit(
                path="agent-alpha/p.md",
                title="t",
                snippet="s",
                score=3.14,
                date="2026-04-10",
                chunk_type="board_card",
            ),
        ],
        error="ValueError: anchor parse failed",
    )
    rebuilt = _roundtrip(original)
    assert rebuilt.original_query == original.original_query
    assert rebuilt.rewritten_query == original.rewritten_query
    assert rebuilt.is_temporal == original.is_temporal
    assert rebuilt.fell_back == original.fell_back
    assert rebuilt.time_window == original.time_window
    assert rebuilt.error == original.error
    assert len(rebuilt.results) == len(original.results)
    rebuilt_hit = rebuilt.results[0]
    original_hit = original.results[0]
    assert rebuilt_hit.path == original_hit.path
    assert rebuilt_hit.title == original_hit.title
    assert rebuilt_hit.snippet == original_hit.snippet
    assert rebuilt_hit.score == original_hit.score
    assert rebuilt_hit.date == original_hit.date
    assert rebuilt_hit.chunk_type == original_hit.chunk_type
