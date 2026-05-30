"""Quality assertions for :class:`MarkdownStructuralChunker` (ADR-028 Wave G.1).

Seed markdown with H1 / H2 / H3 headings + bullets; assert:
  * Chunks respect heading boundaries — no single chunk straddles two
    headings of the same or lower depth.
  * Each chunk's metadata carries the heading-hierarchy path.
  * Oversize sections recurse with overlap **inside** the section
    only — overlap never crosses a heading boundary.

Sabotage proofs (executed inline where called out):
  * Remove the heading-stack flush in ``_split_into_sections`` →
    ``test_chunks_respect_heading_boundaries`` fails because two
    siblings merge.
  * Drop the metadata heading_path entry → ``test_metadata_path_captures_hierarchy`` fails.
  * Apply overlap across heading boundaries → ``test_overlap_stays_within_section`` fails.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.markdown_structural import make_chunker

pytestmark = pytest.mark.integration


_MULTI_HEADING_DOC = """\
# Project Plan

Overview paragraph.

## Risks

* risk-one: schedule slippage
* risk-two: scope creep

## Mitigations

* mit-one: weekly checkpoint
* mit-two: scope-locked sprints

# Closing Notes

Wrap-up paragraph.
"""


def test_chunks_respect_heading_boundaries() -> None:
    """No single chunk contains text from two different H1/H2 sections.

    The doc has 4 leaf sections (Project Plan/Overview, Risks,
    Mitigations, Closing Notes). With the budget-fitting check
    none of them collapse together.
    """
    chunker = make_chunker()
    chunks = chunker.chunk(
        text=_MULTI_HEADING_DOC,
        section_kind="text",
        source_uri="docs/plan.md",
    )
    assert chunks
    # Each chunk's text must not contain BOTH "risk-one" and "mit-one"
    # (two different H2 sections); same for "Closing" and "Overview".
    for chunk in chunks:
        body = chunk.text
        assert not (("risk-one" in body) and ("mit-one" in body)), f"Risks section bled into Mitigations: {body!r}"
        assert not (("Closing Notes" in body) and ("Overview paragraph" in body)), f"H1 boundary leaked: {body!r}"


def test_metadata_path_captures_hierarchy() -> None:
    """Each chunk metadata.heading_path reflects the H1>H2 breadcrumb."""
    chunker = make_chunker()
    chunks = chunker.chunk(
        text=_MULTI_HEADING_DOC,
        section_kind="text",
        source_uri="docs/plan.md",
    )
    paths = {chunk.metadata.get("heading_path", "") for chunk in chunks}
    assert "Project Plan > Risks" in paths
    assert "Project Plan > Mitigations" in paths
    assert "Closing Notes" in paths


def test_overlap_stays_within_section() -> None:
    """When the chunker emits multiple chunks, overlap text never crosses
    an H1/H2 boundary. We seed an oversized first H2 section so it
    splits, then assert the second H2's body never appears inside any
    chunk that contains the first H2's body.
    """
    large_section = "Paragraph line one. " * 800  # ~16K chars > budget
    doc = f"# Big Doc\n\n## SectionAlpha\n\n{large_section}\n\n## SectionBeta\n\nBETA-SENTINEL body.\n"
    chunker = make_chunker()
    chunks = chunker.chunk(text=doc, section_kind="text", source_uri="docs/big.md")
    # SectionAlpha is oversize so we expect multiple chunks for it.
    alpha_chunks = [c for c in chunks if "Paragraph line one" in c.text]
    assert len(alpha_chunks) > 1, "expected SectionAlpha to split"
    for chunk in alpha_chunks:
        assert "BETA-SENTINEL" not in chunk.text, (
            f"Overlap leaked from SectionAlpha into SectionBeta: {chunk.text[-200:]!r}"
        )


def test_yaml_frontmatter_outside_body_still_chunks() -> None:
    """YAML frontmatter renders as the pre-heading section — it should
    chunk cleanly without raising, even if there are no headings.
    """
    doc = "---\ntitle: A\n---\n\nbody only, no headings\n"
    chunker = make_chunker()
    chunks = chunker.chunk(text=doc, section_kind="text", source_uri="x.md")
    assert chunks
    # Heading path is empty for pre-heading material.
    assert all(c.metadata.get("heading_path", "") == "" for c in chunks)


def test_small_doc_emits_single_chunk() -> None:
    chunker = make_chunker()
    chunks = chunker.chunk(
        text="# Tiny\n\nbody",
        section_kind="text",
        source_uri="x.md",
    )
    assert len(chunks) == 1
    assert "Tiny" in chunks[0].metadata.get("heading_path", "")
