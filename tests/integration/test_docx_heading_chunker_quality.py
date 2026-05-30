"""Quality integration tests for :class:`DocxHeadingChunker` (ADR-028 Wave G.1).

Seeds a DOCX-shaped markdown document with H1/H2/H3 hierarchy + an
embedded table and asserts:
  * chunks respect heading boundaries (no straddle across sections),
  * each chunk's metadata carries the ``section_path`` breadcrumb,
  * the embedded table emits its own chunk separate from prose.

Sabotage-prove targets:
- Drop the table partitioning (treat tables as prose lines):
  test_table_emits_its_own_chunk fails → restore.
- Drop the heading-stack pop logic (keep deeper headings after the
  level closes): test_section_path_breadcrumb_is_hierarchical fails →
  restore.

EXECUTED sabotage proof: edit ``_partition_table_and_prose`` to
always return ``([], lines)`` (no table grouping); re-run pytest;
test_table_emits_its_own_chunk fails because no chunk's
``metadata["section_kind"]`` equals ``"table"``. Restored.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.docx_heading import DocxHeadingChunker, version

pytestmark = pytest.mark.integration


def _docx_shaped_markdown() -> str:
    """Return a DocxExtractor-shaped markdown document with hierarchy + table.

    Structure:
        # Chapter 5: Compliance
        prose paragraph
        ## 5.1 Policy Overview
        prose for 5.1
        ## 5.2 Operations
        ### 5.2.1 Risk Register
        prose for 5.2.1
        | risk | mitigation |
        | --- | --- |
        | r1 | m1 |
        | r2 | m2 |
        ### 5.2.2 Audit Log
        prose for 5.2.2
    """
    return (
        "# Chapter 5: Compliance\n\n"
        "BODY_INTRO — the compliance chapter opens with scope notes.\n\n"
        "## 5.1 Policy Overview\n\n"
        "BODY_POLICY — the policy overview covers consent and retention.\n\n"
        "## 5.2 Operations\n\n"
        "BODY_OPS — operational controls section opener.\n\n"
        "### 5.2.1 Risk Register\n\n"
        "BODY_RISK — risks are scored on a five-point likelihood scale.\n\n"
        "| risk | mitigation |\n"
        "| --- | --- |\n"
        "| r1 | m1 |\n"
        "| r2 | m2 |\n\n"
        "### 5.2.2 Audit Log\n\n"
        "BODY_AUDIT — audit log retention is fourteen months.\n"
    )


def test_chunks_respect_heading_boundaries() -> None:
    """No section's body sentinel leaks into another section's chunk."""
    chunker = DocxHeadingChunker()
    chunks = chunker.chunk(
        text=_docx_shaped_markdown(),
        section_kind="text",
        source_uri="agent-alpha-compliance.docx",
    )
    sentinels = ["BODY_INTRO", "BODY_POLICY", "BODY_OPS", "BODY_RISK", "BODY_AUDIT"]
    sentinel_to_chunk: dict[str, int] = {}
    for sentinel in sentinels:
        owning = [i for i, c in enumerate(chunks) if sentinel in c.text]
        # Each sentinel must appear in exactly one chunk (the prose
        # chunk of its own section).
        assert len(owning) == 1, f"{sentinel} appeared in {len(owning)} chunks"
        sentinel_to_chunk[sentinel] = owning[0]
    # The five sentinels must land on five different chunks (no
    # cross-section merging).
    assert len(set(sentinel_to_chunk.values())) == 5


def test_section_path_breadcrumb_is_hierarchical() -> None:
    """``5.2.1 Risk Register`` chunk's path includes its H1 + H2 ancestors."""
    chunker = DocxHeadingChunker()
    chunks = chunker.chunk(
        text=_docx_shaped_markdown(),
        section_kind="text",
        source_uri="agent-alpha-compliance.docx",
    )
    risk_chunks = [c for c in chunks if "BODY_RISK" in c.text]
    assert len(risk_chunks) == 1
    path = risk_chunks[0].metadata["section_path"]
    assert "Chapter 5: Compliance" in path
    assert "5.2 Operations" in path
    assert "5.2.1 Risk Register" in path
    # The breadcrumb separator joins the three levels.
    assert path.count(">") == 2


def test_table_emits_its_own_chunk() -> None:
    """The table inside 5.2.1 emits a separate ``section_kind="table"`` chunk."""
    chunker = DocxHeadingChunker()
    chunks = chunker.chunk(
        text=_docx_shaped_markdown(),
        section_kind="text",
        source_uri="agent-alpha-compliance.docx",
    )
    table_chunks = [c for c in chunks if c.metadata["section_kind"] == "table"]
    assert len(table_chunks) == 1
    only_table = table_chunks[0]
    # The risk-mitigation table content must be present.
    assert "| risk | mitigation |" in only_table.text
    assert "| r1 | m1 |" in only_table.text
    # The table chunk inherits the same section path as its parent prose.
    assert "5.2.1 Risk Register" in only_table.metadata["section_path"]
    # The table is NOT mixed into the prose chunks (no straddle).
    prose_chunks = [c for c in chunks if c.metadata["section_kind"] == "prose"]
    for p in prose_chunks:
        assert "| r1 | m1 |" not in p.text


def test_section_path_appears_in_chunk_text() -> None:
    """The breadcrumb is also stamped into the chunk text as context."""
    chunker = DocxHeadingChunker()
    chunks = chunker.chunk(
        text=_docx_shaped_markdown(),
        section_kind="text",
        source_uri="agent-alpha-compliance.docx",
    )
    risk_chunks = [c for c in chunks if "BODY_RISK" in c.text]
    assert len(risk_chunks) == 1
    assert risk_chunks[0].text.startswith("[Section: ")


def test_chunker_version_flows_through() -> None:
    """F55 propagation across both prose and table chunks."""
    chunker = DocxHeadingChunker()
    chunks = chunker.chunk(
        text=_docx_shaped_markdown(),
        section_kind="text",
        source_uri="agent-alpha-compliance.docx",
    )
    assert len(chunks) >= 5
    for c in chunks:
        assert c.chunker_version == version
