"""#385 follow-up coverage — :func:`format_text` snippet width + title cleanup.

The 2026-06-02 commit reordered the result block so snippet leads + URL
is demoted; this file pins the remaining follow-ups:

* ``--snippet-width N`` flag threads through ``format_text(out,
  snippet_width=N)`` so operators tune triage vs deep-dive output
* ``clean_title_fallback`` strips the trailing ``#<int>`` chunk-sequence
  suffix that ``_SqliteChunkWriter`` appends to ``documents.path`` so
  archive-extracted chunks (``something.zip#1536``) read as
  ``something.zip`` in the title line — full path stays visible on the
  path line so debug context is preserved

F1/F2-clean: every test composes via the canonical
``SearchHit``/``SearchOutput`` dataclasses.
"""

from __future__ import annotations

import pytest

from kairix.core.search.cli import clean_title_fallback, format_text
from kairix.use_cases.search import SearchHit, SearchOutput

pytestmark = pytest.mark.unit


def _hit(
    *,
    path: str,
    title: str = "",
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
        collection="kb",
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
        latency_ms=10.0,
    )


# ---------------------------------------------------------------------------
# Title cleanup — chunk-sequence suffix strip
# ---------------------------------------------------------------------------


def testclean_title_fallback_strips_chunk_sequence_suffix() -> None:
    """A path ending in ``#<int>`` (the suffix ``_SqliteChunkWriter``
    appends) renders without the suffix in the title fallback.

    Sabotage-proof: drop the ``_CHUNK_SEQ_SUFFIX_RE.sub`` call and the
    fallback emits the raw chunk-sequence suffix — assertion catches.
    """
    assert clean_title_fallback("notes/topic.md#0") == "topic.md"
    assert clean_title_fallback("archive/something.zip#1536") == "something.zip"


def testclean_title_fallback_leaves_unsuffixed_paths_untouched() -> None:
    """A path with no chunk-sequence suffix passes through unchanged."""
    assert clean_title_fallback("notes/topic.md") == "topic.md"
    assert clean_title_fallback("plain.txt") == "plain.txt"


def testclean_title_fallback_handles_empty_path() -> None:
    """Empty input returns empty — locks the safe-fall-through path so
    a missing path doesn't render ``''``-shaped title."""
    assert clean_title_fallback("") == ""


def test_format_text_usesclean_title_fallback_when_title_is_empty() -> None:
    """A hit with no title + an archive-extracted path renders the
    cleaned basename in the title line; the full path stays visible
    on the path line below.

    Sabotage-proof: drop the helper call and the title line reads
    ``something.zip#1536`` instead of ``something.zip`` — assertion
    catches.
    """
    out = _output(
        [
            _hit(
                path="sharepoint/.../something.zip#1536",
                title="",
                snippet="multi-agent systems deploy two or more agents",
            )
        ]
    )
    rendered = format_text(out)
    lines = rendered.splitlines()
    # The path line should still show the full path with #1536.
    assert any("something.zip#1536" in line for line in lines)
    # The title line should show the cleaned basename (no #1536).
    title_lines = [line for line in lines if "something.zip" in line and "#1536" not in line]
    assert title_lines, f"expected a title line with cleaned basename; got {rendered!r}"


def test_format_text_prefers_explicit_title_over_path_fallback() -> None:
    """When ``hit.title`` is non-empty it wins over the path-fallback
    cleanup. Locks the operator's authored title taking priority."""
    out = _output([_hit(path="archive/something.zip#42", title="Strategic note", snippet="x")])
    rendered = format_text(out)
    assert "Strategic note" in rendered
    # No accidental suffix-strip on the path field.
    assert "something.zip#42" in rendered


# ---------------------------------------------------------------------------
# --snippet-width — triage vs deep-dive control
# ---------------------------------------------------------------------------


def test_snippet_width_truncates_long_snippet_to_configured_width() -> None:
    """A snippet longer than ``snippet_width`` truncates to that width
    + an ellipsis. Operators set width=200 for tighter triage output.

    Sabotage-proof: hard-code the old ``[:600]`` slice and a
    ``snippet_width=100`` test would still render 600 chars —
    assertion catches via the truncated-length check.
    """
    long_snippet = "x" * 800
    out = _output([_hit(path="a.md", snippet=long_snippet)])
    rendered = format_text(out, snippet_width=100)
    snippet_line = next((line for line in rendered.splitlines() if "x" * 50 in line), "")
    # 100 ``x`` chars + the ``…`` marker; allow the leading three-space
    # indent the format_text renderer adds.
    assert snippet_line.strip().startswith("x" * 100)
    assert snippet_line.strip().endswith("…")
    assert len(snippet_line.strip()) == 101  # 100 chars + ellipsis


def test_snippet_width_default_matches_pre_385_followup_behaviour() -> None:
    """The default ``snippet_width=600`` matches the 2026-06-02 commit
    so the operator's eye-line doesn't shift without an explicit flag."""
    long_snippet = "y" * 1000
    out = _output([_hit(path="a.md", snippet=long_snippet)])
    rendered = format_text(out)  # no kwarg → default 600
    snippet_line = next((line for line in rendered.splitlines() if "y" * 50 in line), "")
    assert len(snippet_line.strip()) == 601  # 600 chars + ellipsis


def test_snippet_width_zero_suppresses_snippet_rendering() -> None:
    """``snippet_width=0`` skips the snippet line entirely — operators
    triaging large result sets can use this for a header-only view."""
    out = _output([_hit(path="a.md", snippet="something we'd otherwise render")])
    rendered = format_text(out, snippet_width=0)
    assert "something we'd otherwise render" not in rendered


def test_snippet_width_extends_for_deep_dive() -> None:
    """``snippet_width=1200`` renders longer snippets without
    truncation — for deep-dive readability."""
    medium_snippet = "z" * 900
    out = _output([_hit(path="a.md", snippet=medium_snippet)])
    rendered = format_text(out, snippet_width=1200)
    # No ellipsis means no truncation happened.
    assert "z" * 900 in rendered
    assert "…" not in rendered
