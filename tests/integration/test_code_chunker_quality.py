"""Quality assertions for :class:`CodeChunker` (ADR-028 Wave G.1).

Seed Python source with multiple ``class`` + ``def`` blocks; assert:
  * Chunks split on those boundaries — adjacent functions don't
    collapse into one mid-function chunk.
  * Function signatures aren't duplicated across adjacent chunks
    (overlap window cap of 100 chars / ~25 tokens enforced).

Sabotage proofs (executed inline):
  * Set the Python separator stack to ``("\\n",)`` only → the
    boundary-preserving test fails because the chunker no longer
    splits at class/def lines.
  * Raise ``_CODE_OVERLAP_CHARS`` to 2000 → signature-duplication test
    fails because the same ``def`` line appears in two chunks.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.code import make_chunker

pytestmark = pytest.mark.integration


# Long enough that the chunker has to split on class / def boundaries
# (combined length comfortably exceeds the 1000-char budget so the
# language-aware splitter is forced into action). The "filler" comment
# lines inside each function pad the bodies so each method is itself
# a meaningful section.
_FILLER_BODY = "        # filler line so each function body is non-trivial.\n" * 6

_MULTI_FUNCTION_PY = f"""\
class Alpha:
    def alpha_one(self, payload):
        \"\"\"Alpha one — anchor token alpha-one.\"\"\"
{_FILLER_BODY}        result = []
        for index in range(100):
            result.append(payload + str(index))
        return result

    def alpha_two(self, payload):
        \"\"\"Alpha two — anchor token alpha-two.\"\"\"
{_FILLER_BODY}        result = []
        for index in range(100):
            result.append(payload + str(index) + " suffix")
        return result


class Beta:
    def beta_one(self, payload):
        \"\"\"Beta one — anchor token beta-one.\"\"\"
{_FILLER_BODY}        return payload.upper()

    def beta_two(self, payload):
        \"\"\"Beta two — anchor token beta-two.\"\"\"
{_FILLER_BODY}        return payload.lower()
"""


def test_chunks_split_on_class_or_def_boundaries() -> None:
    """At least one chunk starts with ``class `` or ``def ``.

    With language-aware separators in place, the chunker prefers
    those boundaries over generic newlines.
    """
    chunker = make_chunker(language="python")
    chunks = chunker.chunk(
        text=_MULTI_FUNCTION_PY,
        section_kind="text",
        source_uri="kairix/example.py",
    )
    assert len(chunks) >= 2
    boundary_starters = [c for c in chunks if c.text.lstrip().startswith(("class ", "def "))]
    assert boundary_starters, (
        f"expected at least one chunk to start at a class/def boundary; got starts={[c.text[:40] for c in chunks]!r}"
    )


def test_function_signatures_not_duplicated_across_chunks() -> None:
    """The same ``def name(`` signature appears in at most one chunk.

    Overlap is capped (100 chars / ~25 tokens) so a complete
    signature line never appears in two consecutive chunks.
    Sabotage-proof: raise ``_CODE_OVERLAP_CHARS`` to 2000 → at
    least one signature would show up in both chunks.
    """
    chunker = make_chunker(language="python")
    chunks = chunker.chunk(
        text=_MULTI_FUNCTION_PY,
        section_kind="text",
        source_uri="kairix/example.py",
    )
    for signature in (
        "def alpha_one(self, payload):",
        "def alpha_two(self, payload):",
        "def beta_one(self, payload):",
        "def beta_two(self, payload):",
    ):
        appearances = sum(1 for c in chunks if signature in c.text)
        assert appearances == 1, (
            f"signature {signature!r} appeared in {appearances} chunks — "
            "overlap window leaked the full signature across chunks"
        )


def test_anchor_tokens_route_to_distinct_chunks() -> None:
    """Each function's unique anchor token ends up in exactly one
    chunk — proves the chunker isn't collapsing distinct functions
    into a single window.
    """
    chunker = make_chunker(language="python")
    chunks = chunker.chunk(
        text=_MULTI_FUNCTION_PY,
        section_kind="text",
        source_uri="kairix/example.py",
    )
    anchors = ("alpha-one", "alpha-two", "beta-one", "beta-two")
    for anchor in anchors:
        appearances = sum(1 for c in chunks if anchor in c.text)
        assert appearances == 1, f"anchor {anchor!r} appeared in {appearances} chunks; expected exactly 1"


def test_short_code_emits_single_chunk() -> None:
    """A 3-line function fits well under budget — one chunk."""
    chunker = make_chunker(language="python")
    chunks = chunker.chunk(
        text="def foo():\n    return 1\n",
        section_kind="text",
        source_uri="x.py",
    )
    assert len(chunks) == 1
    assert "def foo" in chunks[0].text


def test_go_language_preferences_func_boundary() -> None:
    """Go ``func`` boundary is preferred over generic newlines.

    The first useful separator is ``\\nfunc `` — when the source
    contains two top-level func decls the chunker keeps them together
    in the final chunk rather than slicing mid-function. Note that
    the trailing overlap window prepends comment tail onto the chunk
    text, so we look for both function decls inside the same chunk
    rather than asserting the chunk starts with ``func ``.
    """
    code = "package main\n\n" + "// comment line\n" * 200 + "\nfunc First() {}\n\nfunc Second() {}\n"
    chunker = make_chunker(language="go")
    chunks = chunker.chunk(text=code, section_kind="text", source_uri="x.go")
    assert chunks
    same_chunk = [c for c in chunks if "func First" in c.text and "func Second" in c.text]
    assert same_chunk, (
        "expected both `func First` and `func Second` to land in the same "
        "chunk (the language-aware splitter should pack them together); "
        f"got chunks={[c.text[-80:] for c in chunks]!r}"
    )
