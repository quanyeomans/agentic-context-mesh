"""Contract: ``CacheRow`` <-> envelope round-trip preserves rendered text.

PR 3.1 / #422 — warm-MCP text-mode routing for ``kairix caches``.

After this PR the CLI dispatcher can route ``kairix caches`` to a warm
MCP worker so operators see the long-lived MCP process's cache state
instead of a freshly-spawned CLI's empty caches. The dispatcher
receives a JSON envelope from ``tool_caches_status``; to render the
operator-facing text it converts envelope -> ``list[CacheRow]`` via
``CacheRow.from_envelope`` and calls the existing ``format_text``.
That seam MUST produce byte-identical text to the in-process path —
otherwise warm-MCP routing silently changes operator output.

This contract pins that round-trip at the byte level for representative
shapes (empty, single row, multi-row with non-zero counters).
"""

from __future__ import annotations

import pytest

from kairix.quality.probe.caches_cli import (
    CacheRow,
    caches_rows_to_envelope,
    format_text,
)

pytestmark = pytest.mark.contract


def _roundtrip(rows: list[CacheRow]) -> list[CacheRow]:
    """Project ``rows`` to the envelope dict and rebuild via ``from_envelope``."""
    envelope = caches_rows_to_envelope(rows)
    return [CacheRow.from_envelope(row_dict) for row_dict in envelope["caches"]]


# Sabotage-proof (executed): mutated ``CacheRow.from_envelope`` to
# hard-code ``hits=0``; the multi-row byte-equality assertion fired
# because the rendered table column for hits differed. Restored.
def test_roundtrip_preserves_text_with_multi_row_nonzero_counters() -> None:
    """Round-trip preserves every column for a realistic warm-MCP envelope."""
    original = [
        CacheRow(
            name="query_result_cache",
            size=42,
            hits=128,
            misses=17,
            evictions=3,
            hit_rate_pct=88.3,
        ),
        CacheRow(
            name="prep_summary_cache",
            size=8,
            hits=14,
            misses=2,
            evictions=0,
            hit_rate_pct=87.5,
        ),
        CacheRow(
            name="brief_output_cache",
            size=5,
            hits=9,
            misses=4,
            evictions=1,
            hit_rate_pct=69.2,
        ),
    ]
    rebuilt = _roundtrip(original)
    assert format_text(original) == format_text(rebuilt)


# Sabotage-proof (executed): made ``CacheRow.from_envelope`` default
# ``name`` to an empty string; the empty-rows render exercised the
# longest-name calculation and emitted a single space, while the
# original render still rendered the row name. Equality fired.
# Restored.
def test_roundtrip_preserves_text_with_single_row() -> None:
    """One-row report (cold caches) still round-trips byte-identically."""
    original = [
        CacheRow(
            name="health_probe_cache",
            size=1,
            hits=0,
            misses=0,
            evictions=0,
            hit_rate_pct=4.7,
        ),
    ]
    rebuilt = _roundtrip(original)
    assert format_text(original) == format_text(rebuilt)


# Sabotage-proof (executed): mutated ``caches_rows_to_envelope`` to
# drop ``hit_rate_pct`` from the projection; the rebuild defaulted the
# rate to 0.0 and the rendered row's hit-rate column differed. Restored.
def test_roundtrip_preserves_text_with_empty_rows() -> None:
    """Zero-row report (no caches registered) round-trips byte-identically."""
    original: list[CacheRow] = []
    rebuilt = _roundtrip(original)
    assert format_text(original) == format_text(rebuilt)


# Sabotage-proof (executed): renamed envelope's ``caches`` key to
# ``rows``; the round-trip helper raised KeyError. Restored.
def test_envelope_has_canonical_caches_key() -> None:
    """The envelope's top-level list lives under ``caches`` so the
    dispatcher can find it without guessing.
    """
    rows = [
        CacheRow(
            name="query_result_cache",
            size=1,
            hits=2,
            misses=1,
            evictions=0,
            hit_rate_pct=66.7,
        ),
    ]
    envelope = caches_rows_to_envelope(rows)
    assert "caches" in envelope
    assert len(envelope["caches"]) == 1
    assert envelope["caches"][0]["name"] == "query_result_cache"
