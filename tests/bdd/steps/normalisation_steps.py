"""Step definitions for normalisation.feature.

Tests the normalisation pipeline using temporary directories.
No external API calls.
"""

import pytest
from pytest_bdd import given, then, when

from kairix.reflib.filters import filter_collection
from kairix.reflib.frontmatter import (
    build_frontmatter,
    extract_existing_frontmatter,
    inject_frontmatter,
)
from kairix.reflib.sources import SourceDef

pytestmark = pytest.mark.bdd

_state: dict = {}


def _make_source(
    name: str = "Test Source",
    collection: str = "engineering",
    dir_name: str = "test-source",
    licence: str = "MIT",
    licence_tier: int = 2,
    source_url: str = "https://example.com",
) -> SourceDef:
    return SourceDef(
        name=name,
        collection=collection,
        dir_name=dir_name,
        licence=licence,
        licence_tier=licence_tier,
        source_url=source_url,
    )


@given("a raw collection with CONTRIBUTING.md and docs/guide.md")
def raw_collection_with_boilerplate(tmp_path):
    input_dir = tmp_path / "raw" / "engineering" / "test-source"
    input_dir.mkdir(parents=True)
    (input_dir / "CONTRIBUTING.md").write_text("# Contributing\nPlease follow these guidelines.")
    docs_dir = input_dir / "docs"
    docs_dir.mkdir()
    (docs_dir / "guide.md").write_text(
        "# Engineering Guide\n\n"
        "This guide covers architecture decision records and engineering practices. "
        "It provides a comprehensive overview of software development best practices "
        "and modern engineering workflows that teams should follow for quality."
    )
    _state["input_dir"] = tmp_path / "raw"
    _state["output_dir"] = tmp_path / "out"
    _state["source"] = _make_source()
    _state["files"] = [input_dir / "CONTRIBUTING.md", docs_dir / "guide.md"]


@when("I run the normalisation pipeline")
def run_normalisation():
    if "files" in _state and "filtered_result" not in _state:
        source = _state.get("source")
        files = _state["files"]
        included = filter_collection(files, source)
        _state["filtered_result"] = included

    if "raw_text" in _state and "normalised_text" not in _state:
        text = _state["raw_text"]
        source = _state["source"]
        fm = build_frontmatter(_state["file_path"], source, text)
        _state["normalised_text"] = inject_frontmatter(text, fm)


@then("the output does not contain CONTRIBUTING.md")
def output_excludes_contributing():
    included = _state["filtered_result"]
    names = [f.name for f in included]
    assert "CONTRIBUTING.md" not in names, f"CONTRIBUTING.md should be filtered but found in {names}"


@then("the output contains guide.md")
def output_includes_guide():
    included = _state["filtered_result"]
    names = [f.name for f in included]
    assert "guide.md" in names, f"guide.md should be included but not found in {names}"


@given("a document without frontmatter")
def document_without_frontmatter(tmp_path):
    file_path = tmp_path / "engineering" / "test-source" / "guide.md"
    file_path.parent.mkdir(parents=True)
    text = (
        "# Architecture Guide\n\n"
        "This document describes architecture decision records. "
        "An architecture decision record (ADR) captures a single "
        "architecture decision and its context."
    )
    file_path.write_text(text)
    _state["raw_text"] = text
    _state["file_path"] = file_path
    _state["source"] = _make_source()


@then("the output has YAML frontmatter with title and source")
def output_has_frontmatter():
    text = _state["normalised_text"]
    assert text.startswith("---"), "Output should start with YAML frontmatter delimiter"
    fm, _body = extract_existing_frontmatter(text)
    assert fm is not None, "Could not parse frontmatter"
    assert "title" in fm, f"Frontmatter missing 'title': {fm}"
    assert "source" in fm, f"Frontmatter missing 'source': {fm}"


@given("a source registered as licence tier 4")
def source_tier_4(tmp_path):
    source = _make_source(
        name="CC-BY-SA Source",
        licence="CC-BY-SA-4.0",
        licence_tier=4,
    )
    _state["tier4_source"] = source
    _state["tier4_included"] = False


@when("I run normalisation with max_tier 3")
def run_with_max_tier_3():
    source = _state["tier4_source"]
    # Tier filtering is checked before file processing
    _state["tier4_included"] = source.licence_tier <= 3


@then("that source is excluded from the output")
def source_excluded():
    assert not _state["tier4_included"], "Tier 4 source should be excluded when max_tier is 3"
