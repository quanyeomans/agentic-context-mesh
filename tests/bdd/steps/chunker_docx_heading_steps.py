"""Step definitions for ``chunker_docx_heading.feature`` (ADR-028 Wave G.1).

Drives the real :class:`kairix.chunkers.docx_heading.DocxHeadingChunker`
directly. The scripted docx-shaped markdown matches what
DocxExtractor produces (heading-prefixed paragraphs + GFM tables).

Sabotage-proofs per step:
  * "emits at least one prose chunk per section" — flipping the
    heading regex to never match fails the step.
  * "emits a separate chunk for the embedded table" — flipping
    :func:`_partition_table_and_prose` to return all-prose fails
    the step.
  * "carries its section path in metadata" — dropping the
    ``section_path`` metadata write fails the step.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.chunkers.docx_heading import DocxHeadingChunker

pytestmark = pytest.mark.bdd


@pytest.fixture
def docx_heading_state() -> dict[str, Any]:
    return {
        "chunker": None,
        "docx_markdown": "",
        "chunks": (),
    }


def _docx_markdown_with_table() -> str:
    return (
        "# Chapter 1: Intro\n\n"
        "intro-body\n\n"
        "## 1.1 Overview\n\n"
        "overview-body\n\n"
        "### 1.1.1 Risk Register\n\n"
        "risk-body\n\n"
        "| risk | mitigation |\n"
        "| --- | --- |\n"
        "| r1 | m1 |\n"
        "| r2 | m2 |\n"
    )


@given("the docx heading chunker is constructed")
def _construct_docx(docx_heading_state: dict[str, Any]) -> None:
    docx_heading_state["chunker"] = DocxHeadingChunker()


@given("the operator has a scripted docx markdown with hierarchy and a table")
def _docx_with_table(docx_heading_state: dict[str, Any]) -> None:
    docx_heading_state["docx_markdown"] = _docx_markdown_with_table()


@when("the operator invokes the docx heading chunker on the docx markdown")
def _invoke_docx(docx_heading_state: dict[str, Any]) -> None:
    chunker: DocxHeadingChunker = docx_heading_state["chunker"]
    docx_heading_state["chunks"] = chunker.chunk(
        text=docx_heading_state["docx_markdown"],
        section_kind="text",
        source_uri="agent-alpha-doc.docx",
    )


@then("the docx heading chunker emits at least one prose chunk per section")
def _prose_per_section(docx_heading_state: dict[str, Any]) -> None:
    chunks = docx_heading_state["chunks"]
    prose = [c for c in chunks if c.metadata["section_kind"] == "prose"]
    # Three sections — H1, H2, H3 — each with prose body.
    assert len(prose) >= 3


@then("the docx heading chunker emits a separate chunk for the embedded table")
def _table_chunk(docx_heading_state: dict[str, Any]) -> None:
    chunks = docx_heading_state["chunks"]
    tables = [c for c in chunks if c.metadata["section_kind"] == "table"]
    assert len(tables) == 1
    assert "| r1 | m1 |" in tables[0].text


@then("each chunk carries its section path in metadata")
def _section_path(docx_heading_state: dict[str, Any]) -> None:
    for c in docx_heading_state["chunks"]:
        assert "section_path" in c.metadata
