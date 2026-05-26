"""Step definitions for silver_pathological_inputs.feature.

Drives the real DefaultSilverProcessor via its public process() method.
F47-clean, F1-clean — no monkeypatching, no internal-private imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.connectors.silver import DefaultSilverProcessor
from kairix.core.protocols import BronzeRef, DocMetadata, ExtractedDocument

pytestmark = pytest.mark.bdd

_CHUNK_BUDGET = 1000


@dataclass
class _SilverCtx:
    markdown: str = ""
    chunks: list[str] = field(default_factory=list)


@pytest.fixture
def silver_ctx() -> _SilverCtx:
    return _SilverCtx()


@given(parsers.parse("an extracted document with one paragraph {n:d} characters long"))
def given_paragraph_of_length(silver_ctx: _SilverCtx, n: int) -> None:
    # Build a paragraph of approximately n characters using sentences
    sentence = "This is one sentence used to construct a deliberately pathological paragraph. "
    repeats = max(1, n // len(sentence))
    silver_ctx.markdown = sentence * repeats


@given(parsers.parse("an extracted document with one {n:d}-character sentence"))
def given_sentence_of_length(silver_ctx: _SilverCtx, n: int) -> None:
    # Build a sentence with no internal punctuation — word-boundary only
    words = ["lorem"] * (n // 6)
    silver_ctx.markdown = " ".join(words)


@given(parsers.parse("an extracted document with one {n:d}-character word"))
def given_word_of_length(silver_ctx: _SilverCtx, n: int) -> None:
    silver_ctx.markdown = "a" * n


@given(parsers.parse("an extracted document with empty markdown"))
def given_empty_markdown(silver_ctx: _SilverCtx) -> None:
    silver_ctx.markdown = ""


@when(parsers.parse("the operator passes the document through DefaultSilverProcessor"))
def when_process(silver_ctx: _SilverCtx) -> None:
    silver = DefaultSilverProcessor()
    out = silver.process(
        BronzeRef(
            source_name="bdd-silver",
            item_id="bdd-item",
            raw_path="x/y/z",
            mime="text/markdown",
            fetched_at="2026-05-27T00:00:00Z",
        ),
        ExtractedDocument(
            markdown=silver_ctx.markdown,
            pages=(),
            images=(),
            metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
            confidence=0.5,
        ),
        source_uri="kairix://bdd-silver",
        source_modified_at="2026-05-27T00:00:00Z",
        sensitivity="public",
    )
    silver_ctx.chunks = [c.text for c in out.chunks]


@then(parsers.parse("the resulting chunks number {n:d} or more"))
def then_chunks_ge_n(silver_ctx: _SilverCtx, n: int) -> None:
    assert len(silver_ctx.chunks) >= n, f"expected >= {n} chunks, got {len(silver_ctx.chunks)}"


@then(parsers.parse("the resulting chunks number {n:d}"))
def then_chunks_eq_n(silver_ctx: _SilverCtx, n: int) -> None:
    assert len(silver_ctx.chunks) == n, f"expected {n} chunks, got {len(silver_ctx.chunks)}"


@then(parsers.parse("no chunk exceeds the {budget:d}-character budget"))
def then_max_chunk_in_budget(silver_ctx: _SilverCtx, budget: int) -> None:
    max_chunk = max((len(c) for c in silver_ctx.chunks), default=0)
    assert max_chunk <= budget, f"max chunk {max_chunk} exceeds budget {budget}"
