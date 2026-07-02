"""Unit tests for ``kairix.use_cases.enumeration`` (#437).

Source-cohesion enumeration completion: when the top retrieved chunks cohere
on one enumerable source, the helper pulls that source's COMPLETE ordered
content so a list-of-techniques is surfaced whole instead of clipped to the
score-ranked top-N snippets.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.use_cases.enumeration import (
    complete_enumeration,
    default_expand_callable,
    dominant_source_uri,
    looks_enumerable,
)
from kairix.use_cases.expand import ExpandedChunk, ExpandOutput

pytestmark = pytest.mark.unit


def _chunk(source_uri: str, seq: int, text: str) -> ExpandedChunk:
    return ExpandedChunk(path=f"{source_uri}#{seq}", seq=seq, text=text, tokens=len(text) // 4, source_uri=source_uri)


def _expand_output(source_uri: str, texts: list[str]) -> ExpandOutput:
    chunks = [_chunk(source_uri, i, t) for i, t in enumerate(texts)]
    return ExpandOutput(source_uri=source_uri, chunks=chunks, total_tokens=sum(c.tokens for c in chunks))


# ---------------------------------------------------------------------------
# looks_enumerable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "- Mechanical Turk\n- Pinocchio\n- Fake Door",
        "* first\n* second",
        "1. alpha\n2. bravo\n3. charlie",
        "1) alpha\n2) bravo",
        "# Heading one\n## Heading two",
    ],
)
def test_looks_enumerable_true_for_list_shaped_content(text: str) -> None:
    assert looks_enumerable(text)


@pytest.mark.parametrize(
    "text",
    [
        "This is a paragraph of prose discussing the topic in some detail.",
        "- only one bullet line here",  # below the 2-line floor
        "",
        "a - b - c is not a list because the dash is inline",
    ],
)
def test_looks_enumerable_false_for_prose_or_single_item(text: str) -> None:
    assert not looks_enumerable(text)


# ---------------------------------------------------------------------------
# dominant_source_uri
# ---------------------------------------------------------------------------


def test_dominant_source_uri_returns_shared_source_at_or_above_cohesion() -> None:
    rows = [("u://methods", "a"), ("u://methods", "b"), ("u://other", "c")]
    assert dominant_source_uri(rows) == "u://methods"


def test_dominant_source_uri_none_when_no_source_meets_cohesion() -> None:
    # Two distinct sources, one hit each — no cohesion.
    assert dominant_source_uri([("u://a", "x"), ("u://b", "y")]) is None


def test_dominant_source_uri_excludes_facts_rows() -> None:
    # Synthesised fact triplets are not a file enumeration to expand.
    rows = [("facts://f1", "role: VP"), ("facts://f1", "team: People")]
    assert dominant_source_uri(rows) is None


def test_dominant_source_uri_excludes_empty_source() -> None:
    rows = [("", "a"), ("", "b"), ("   ", "c")]
    assert dominant_source_uri(rows) is None


def test_dominant_source_uri_honours_custom_min_cohesion() -> None:
    rows = [("u://methods", "a"), ("u://methods", "b"), ("u://methods", "c")]
    assert dominant_source_uri(rows, min_cohesion=4) is None
    assert dominant_source_uri(rows, min_cohesion=3) == "u://methods"


# ---------------------------------------------------------------------------
# complete_enumeration
# ---------------------------------------------------------------------------

_SOURCE = "u://pretotyping-methods"
_TECHNIQUES = [
    "Mechanical Turk",
    "Pinocchio",
    "Stripped Tease",
    "Provincial",
    "Fake Door",
    "Pretend-to-Own",
    "Re-label",
]


def _bulleted(techniques: list[str]) -> str:
    return "\n".join(f"- {name}" for name in techniques)


def test_complete_enumeration_surfaces_all_items_from_dominant_source() -> None:
    """The load-bearing behaviour (#437): given top rows that carry only a
    SAMPLE of a bulleted catalogue, the completed enumeration carries EVERY
    item because it pulls the full ordered source, not the top snippets.

    Sabotage-proof (executed): making ``complete_enumeration`` return ``None``
    unconditionally drops the appended block and this assertion fails; the
    top rows alone hold only techniques 1-3.
    """
    # Top rows only surfaced the first three techniques.
    rows = [(_SOURCE, _bulleted(_TECHNIQUES[:3])), (_SOURCE, _bulleted(_TECHNIQUES[1:3]))]
    # The full source (via expand) carries all seven, split across two chunks.
    captured: dict[str, Any] = {}

    def fake_expand(uri: str) -> ExpandOutput:
        captured["uri"] = uri
        return _expand_output(uri, [_bulleted(_TECHNIQUES[:4]), _bulleted(_TECHNIQUES[4:])])

    result = complete_enumeration(rows, expand_fn=fake_expand)
    assert result is not None
    got_uri, full_text = result
    assert got_uri == _SOURCE
    assert captured["uri"] == _SOURCE
    # Every technique — including the ones the top rows dropped — is present.
    for name in _TECHNIQUES:
        assert name in full_text, f"missing enumerated item: {name}"


def test_complete_enumeration_none_when_no_source_dominates() -> None:
    rows = [("u://a", _bulleted(["one", "two"])), ("u://b", _bulleted(["three", "four"]))]

    def fake_expand(_uri: str) -> ExpandOutput:  # pragma: no cover - must not be called
        raise AssertionError("expand must not run when no source dominates")

    assert complete_enumeration(rows, expand_fn=fake_expand) is None


def test_complete_enumeration_none_when_dominant_source_is_prose() -> None:
    """Cohesion alone isn't enough — a prose source (no list shape) is left
    to today's top-N behaviour so we don't over-fetch every 2-hit document."""
    prose = "A long paragraph about the topic without any list structure at all."
    rows = [(_SOURCE, prose), (_SOURCE, prose)]

    def fake_expand(_uri: str) -> ExpandOutput:  # pragma: no cover - must not be called
        raise AssertionError("expand must not run for a non-enumerable source")

    assert complete_enumeration(rows, expand_fn=fake_expand) is None


def test_complete_enumeration_none_when_expand_errors() -> None:
    rows = [(_SOURCE, _bulleted(_TECHNIQUES[:2])), (_SOURCE, _bulleted(_TECHNIQUES[2:4]))]

    def erroring_expand(uri: str) -> ExpandOutput:
        return ExpandOutput(source_uri=uri, error="expand: nothing stored")

    assert complete_enumeration(rows, expand_fn=erroring_expand) is None


def test_complete_enumeration_none_when_expand_returns_empty() -> None:
    rows = [(_SOURCE, _bulleted(_TECHNIQUES[:2])), (_SOURCE, _bulleted(_TECHNIQUES[2:4]))]

    def empty_expand(uri: str) -> ExpandOutput:
        return ExpandOutput(source_uri=uri, chunks=[])

    assert complete_enumeration(rows, expand_fn=empty_expand) is None


def test_complete_enumeration_caps_block_length() -> None:
    """The spliced block is bounded so a pathological source can't balloon the
    synthesis prompt."""
    rows = [(_SOURCE, _bulleted(["a", "b"])), (_SOURCE, _bulleted(["c", "d"]))]
    huge = "- " + "x" * 50_000

    def fake_expand(uri: str) -> ExpandOutput:
        return _expand_output(uri, [huge])

    result = complete_enumeration(rows, expand_fn=fake_expand, max_chars=8000)
    assert result is not None
    _uri, full_text = result
    assert len(full_text) == 8000


# ---------------------------------------------------------------------------
# default_expand_callable — production pull adapter
# ---------------------------------------------------------------------------


def test_default_expand_callable_forwards_source_uri_with_bounded_budget() -> None:
    """The production adapter pulls the whole source (source_uri-only mode) with
    a bounded-but-roomy token budget, driven through the ``expand_runner`` seam
    so the test never touches the on-disk worker index."""
    captured: dict[str, Any] = {}

    def fake_runner(source_uri: str, *, token_budget: int) -> ExpandOutput:
        captured["source_uri"] = source_uri
        captured["token_budget"] = token_budget
        return _expand_output(source_uri, ["- one\n- two"])

    out = default_expand_callable("u://methods", expand_runner=fake_runner)

    assert captured["source_uri"] == "u://methods"
    assert captured["token_budget"] >= 8000, "enumeration pull must use a roomy (but bounded) budget"
    assert out.chunks
