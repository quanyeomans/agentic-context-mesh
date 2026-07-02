"""Source-cohesion enumeration completion (#437).

When the top retrieved chunks for a synthesis (``prep`` / ``research``) are
dominated by ONE source document AND that document carries list-shaped
content — a bullet / numbered list, or several headed sections — the
score-ranked top-N snippets return only a SAMPLE of the enumeration, not
the whole catalogue. An agent asking "what techniques does X describe?"
then gets a truncated answer and has to fire follow-up queries to fill the
gaps (the 2026-06-07 pretotyping dogfood: 5 of 6+ named methods surfaced).

This module is the smallest synthesis-layer fix (mechanism 1 in #437). It
detects the "top hits cohere on one enumerable source" shape and, REUSING
the ``expand`` backbone (:func:`kairix.use_cases.expand.run_expand`), pulls
that source's COMPLETE ordered chunk set so the synthesiser sees every list
item instead of the top few. Shared by ``prep`` and ``research`` so both
agent surfaces enumerate identically (the CLI/MCP feature-parity contract,
#168).

The expansion is bounded on both ends — ``_ENUM_EXPAND_BUDGET`` caps the
token pull and ``_MAX_ENUMERATION_CHARS`` caps the block spliced into the
LLM context — so a pathological source can't drive an unbounded read.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from kairix.use_cases.expand import ExpandOutput, run_expand

# Trigger threshold: how many of the top rows must cohere on one source
# before its full enumeration is pulled. Two is the floor the issue names
# — a single hit is an ordinary snippet, two+ from one file is the
# "reading a list inside one document" signal.
_DEFAULT_MIN_COHESION = 2

# Token budget for the full-source pull. Roomy enough to hold a complete
# techniques catalogue (the #437 repro is ~7 methods across a handful of
# chunks) while still clamping a pathological source.
_ENUM_EXPAND_BUDGET = 12000

# Hard cap on the completed-enumeration block spliced into the LLM context.
# ~2K tokens — a sane ceiling that keeps the synthesis prompt bounded even
# when the source is large.
_MAX_ENUMERATION_CHARS = 8000

# A line is "list-shaped" when it opens a bullet (``-`` / ``*`` / ``+``), a
# numbered item (``1.`` / ``1)``), or a markdown heading (``#``..``######``).
_LIST_LINE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|#{1,6}\s+)")


def default_expand_callable(
    source_uri: str,
    *,
    expand_runner: Callable[..., ExpandOutput] = run_expand,
) -> ExpandOutput:
    """Production expand adapter — pull a source's full ordered chunk set.

    Wraps :func:`kairix.use_cases.expand.run_expand` with the enumeration
    token budget and no ``seq`` (source_uri-only mode) so the walk resolves
    the document's real chunks and returns them ordered by ``seq``. The
    ``expand_runner`` seam lets a test drive this adapter without touching
    the on-disk worker index; production callers leave it defaulted.
    """
    return expand_runner(source_uri, token_budget=_ENUM_EXPAND_BUDGET)


def looks_enumerable(text: str, *, min_list_lines: int = 2) -> bool:
    """True when ``text`` carries list-shaped content.

    Cheap guard so cohesion expansion only fires for the enumeration case
    #437 targets (bullet / numbered list or several headed sections), not
    every prose source that happens to land two hits. ``min_list_lines``
    list-shaped lines are required to count as an enumeration.
    """
    list_lines = sum(1 for line in text.splitlines() if _LIST_LINE.match(line))
    return list_lines >= min_list_lines


def dominant_source_uri(
    rows: Sequence[tuple[str, str]],
    *,
    min_cohesion: int = _DEFAULT_MIN_COHESION,
) -> str | None:
    """Return the source_uri shared by ``>= min_cohesion`` rows, else ``None``.

    ``rows`` are ``(source_uri, snippet)`` pairs for the top retrieved
    chunks. Empty source_uris and synthesised ``facts://`` rows are excluded
    — a fact triplet is not a file enumeration to expand.
    """
    counts: dict[str, int] = {}
    for source_uri, _snippet in rows:
        uri = (source_uri or "").strip()
        if not uri or uri.startswith("facts://"):
            continue
        counts[uri] = counts.get(uri, 0) + 1
    if not counts:
        return None
    uri, count = max(counts.items(), key=lambda item: item[1])
    return uri if count >= min_cohesion else None


def _ordered_source_text(out: ExpandOutput) -> str:
    """Join an ``ExpandOutput``'s chunks (already seq-ordered) into one block."""
    return "\n".join(chunk.text for chunk in out.chunks if chunk.text).strip()


def complete_enumeration(
    rows: Sequence[tuple[str, str]],
    *,
    expand_fn: Callable[[str], ExpandOutput],
    min_cohesion: int = _DEFAULT_MIN_COHESION,
    max_chars: int = _MAX_ENUMERATION_CHARS,
) -> tuple[str, str] | None:
    """Complete the enumeration when the top rows cohere on one enumerable source.

    Returns ``(source_uri, full_ordered_text)`` — the dominant source's
    COMPLETE ordered content, capped at ``max_chars`` — so the caller can
    splice every list item into the synthesis context. Returns ``None`` when
    no source dominates, the dominant source is not list-shaped, or the
    expansion yields nothing / errors (the caller then keeps today's
    top-N-snippet behaviour unchanged).

    ``expand_fn`` is the pull seam — production passes
    :func:`default_expand_callable`; tests inject a fake returning a canned
    :class:`ExpandOutput`.
    """
    uri = dominant_source_uri(rows, min_cohesion=min_cohesion)
    if uri is None:
        return None
    dominant_text = "\n".join(snippet for source_uri, snippet in rows if (source_uri or "").strip() == uri)
    if not looks_enumerable(dominant_text):
        return None
    out = expand_fn(uri)
    if out.error:
        return None
    full = _ordered_source_text(out)
    if not full:
        return None
    return uri, full[:max_chars]


__all__ = [
    "complete_enumeration",
    "default_expand_callable",
    "dominant_source_uri",
    "looks_enumerable",
]
